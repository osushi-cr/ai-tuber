"""
broadcast_loop.py のフェーズハンドラのユニットテスト。
SaintGraph の高レベルメソッド (process_intro 等) を呼び出すことを検証します。
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from saint_graph.broadcast_loop import (
    BroadcastPhase,
    BroadcastContext,
    handle_intro,
    handle_news,
    handle_qa,
    handle_closing,
    run_broadcast_loop,
)
from saint_graph.config import MAX_WAIT_CYCLES

def _make_ctx(news_service=None, comments=None):
    mock_saint = MagicMock()
    # 新しいメソッドの AsyncMock 化
    mock_saint.process_turn = AsyncMock()
    mock_saint.process_intro = AsyncMock()
    mock_saint.process_news_reading = AsyncMock()
    mock_saint.prepare_news_reading_text = AsyncMock(return_value=[("neutral", "本文")])
    mock_saint.play_prepared_sentences = AsyncMock()
    mock_saint.play_prepared_sentences_with_caption = AsyncMock(return_value=["speak-action"])
    mock_saint.process_news_finished = AsyncMock()
    mock_saint.process_closing = AsyncMock()
    
    mock_saint.body = MagicMock()
    mock_saint.body.get_comments = AsyncMock(return_value=comments or [])
    mock_saint.body.wait_for_queue = AsyncMock()
    mock_saint.body.update_news_caption = AsyncMock()
    mock_saint.body.clear_news_caption = AsyncMock()
    mock_saint.body.play_filler = AsyncMock()
    mock_saint.body.play_bgm = AsyncMock()
    mock_saint.body.stop_bgm = AsyncMock()
    mock_saint.body.switch_bgm = AsyncMock()
    mock_saint.body.switch_scene = AsyncMock()
    mock_saint.body.queue_scene_switch = AsyncMock(return_value={"action_id": "scene-action"})
    mock_saint.body.queue_caption_clear = AsyncMock(return_value={"action_id": "caption-clear"})
    mock_saint.body.queue_auto_filler_start = AsyncMock(return_value={"action_id": "auto-start"})
    mock_saint.body.queue_auto_filler_stop = AsyncMock(return_value={"action_id": "auto-stop"})
    mock_saint.body.wait_for_queue_strict = AsyncMock(return_value=True)
    mock_saint.register_chitchat = AsyncMock()

    mock_news = news_service or MagicMock()

    return BroadcastContext(
        saint_graph=mock_saint,
        news_service=mock_news,
    )


@pytest.mark.asyncio
async def test_handle_intro():
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    phase = await handle_intro(ctx)
    
    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.process_intro.assert_called_once()
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()
    ctx.saint_graph.body.queue_scene_switch.assert_has_calls([
        call("waiting"),
        call("kurara_main"),
    ])
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action"]),
        call(["scene-action"]),
    ])


@pytest.mark.asyncio
async def test_handle_intro_prefetches_first_news_text_only():
    news_service = MagicMock()
    news_service.has_next.return_value = True
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    assert ctx.next_news_task is not None
    await ctx.next_news_task
    ctx.saint_graph.process_intro.assert_called_once()
    ctx.saint_graph.prepare_news_reading_text.assert_called_once_with(
        title="Title", content="Content"
    )
    ctx.saint_graph.process_news_reading.assert_not_called()
    ctx.saint_graph.body.queue_scene_switch.assert_has_calls([
        call("waiting"),
        call("kurara_main"),
    ])


@pytest.mark.asyncio
async def test_handle_news_with_comment():
    ctx = _make_ctx(comments=[{"author": "User", "message": "Hi?"}])
    phase = await handle_news(ctx)
    
    assert phase == BroadcastPhase.NEWS
    # コメント応答は process_turn を直接呼ぶ（共通ユーティリティ）
    ctx.saint_graph.process_turn.assert_called_once()
    assert "User: Hi" in ctx.saint_graph.process_turn.call_args[0][0]
    ctx.saint_graph.process_news_reading.assert_not_called()
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_reading():
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item
    
    ctx = _make_ctx(news_service=news_service)
    phase = await handle_news(ctx)
    
    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.prepare_news_reading_text.assert_called_once_with(
        title="Title", content="Content"
    )
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_called_once_with(
        [("neutral", "本文")],
        caption_title="Title",
        caption_summary="Content",
        wait_after=False,
    )
    ctx.saint_graph.body.queue_scene_switch.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action", "speak-action"]),
    ])
    ctx.saint_graph.body.wait_for_queue.assert_not_called()
    ctx.saint_graph.process_news_reading.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_uses_prefetched_sentences_and_prefetches_next():
    async def prepared_current():
        return [("joyful", "現在ニュース")]

    news_service = MagicMock()
    news_service.has_next.side_effect = [True, True]
    current = MagicMock()
    current.title = "Current"
    current.content = "Current content"
    next_item = MagicMock()
    next_item.title = "Next"
    next_item.content = "Next content"
    news_service.peek_current_item.side_effect = [current, next_item]
    news_service.get_next_item.return_value = current

    ctx = _make_ctx(news_service=news_service)
    ctx.next_news_task = asyncio.create_task(prepared_current())

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.body.update_news_caption.assert_not_called()
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_called_once_with(
        [("joyful", "現在ニュース")],
        caption_title="Current",
        caption_summary="Current content",
        wait_after=False,
    )
    ctx.saint_graph.body.queue_scene_switch.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action", "speak-action"]),
    ])
    ctx.saint_graph.body.wait_for_queue.assert_not_called()
    ctx.saint_graph.process_news_reading.assert_not_called()
    ctx.saint_graph.prepare_news_reading_text.assert_called_once_with(
        title="Next", content="Next content"
    )
    assert ctx.next_news_task is not None
    await ctx.next_news_task


@pytest.mark.asyncio
async def test_handle_news_retries_failed_presentation_setup_then_closes():
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"action_id": "scene-1"},
        {"action_id": "scene-2"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [False, False]

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.CLOSING
    assert ctx.closing_reason == "technical_failure"
    assert ctx.saint_graph.body.queue_scene_switch.call_count == 2
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-1", "speak-action"]),
        call(["scene-2", "speak-action"]),
    ])
    ctx.saint_graph.prepare_news_reading_text.assert_called_once_with(
        title="Title", content="Content"
    )
    assert ctx.saint_graph.play_prepared_sentences_with_caption.call_count == 2


@pytest.mark.asyncio
async def test_handle_intro_closes_when_main_scene_strict_fails():
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"action_id": "waiting"},
        {"action_id": "main-1"},
        {"action_id": "main-2"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [True, False, False]

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.CLOSING
    assert ctx.closing_reason == "technical_failure"
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["waiting"]),
        call(["main-1"]),
        call(["main-2"]),
    ])


@pytest.mark.asyncio
async def test_handle_intro_retries_waiting_scene_then_succeeds():
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"action_id": "waiting-1"},
        {"action_id": "waiting-2"},
        {"action_id": "main"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [False, True, True]

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.body.queue_scene_switch.assert_has_calls([
        call("waiting"),
        call("waiting"),
        call("kurara_main"),
    ])
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["waiting-1"]),
        call(["waiting-2"]),
        call(["main"]),
    ])


@pytest.mark.asyncio
async def test_handle_intro_continues_when_waiting_scene_fails_twice(caplog):
    caplog.set_level("WARNING", logger="saint-graph")
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"action_id": "waiting-1"},
        {"action_id": "waiting-2"},
        {"action_id": "main"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [False, False, True]

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    assert ctx.closing_reason is None
    assert "INTRO start waiting_scene failed on attempt 2" in caplog.text
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["waiting-1"]),
        call(["waiting-2"]),
        call(["main"]),
    ])


@pytest.mark.asyncio
async def test_handle_intro_retries_waiting_scene_without_action_id():
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"status": "error"},
        {"action_id": "waiting-2"},
        {"action_id": "main"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [True, True]

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.body.wait_for_queue.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["waiting-2"]),
        call(["main"]),
    ])


@pytest.mark.asyncio
async def test_handle_news_retries_failed_speak_caption_then_succeeds():
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.play_prepared_sentences_with_caption.side_effect = [
        ["speak-1"],
        ["speak-2"],
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [
        False,  # caption 同梱 speak 失敗
        True,   # retry 成功
    ]

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.NEWS
    assert ctx.closing_reason is None
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_has_calls([
        call(
            [("neutral", "本文")],
            caption_title="Title",
            caption_summary="Content",
            wait_after=False,
        ),
        call(
            [("neutral", "本文")],
            caption_title="Title",
            caption_summary="Content",
            wait_after=False,
        ),
    ])
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action", "speak-1"]),
        call(["scene-action", "speak-2"]),
    ])
    ctx.saint_graph.body.wait_for_queue.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_strict_checks_all_speak_action_ids():
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.prepare_news_reading_text.return_value = [
        ("neutral", "一文目"),
        ("joyful", "二文目"),
    ]
    ctx.saint_graph.play_prepared_sentences_with_caption.return_value = [
        "speak-1",
        "speak-2",
    ]

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.body.wait_for_queue_strict.assert_called_once_with([
        "scene-action",
        "speak-1",
        "speak-2",
    ])


@pytest.mark.asyncio
async def test_handle_news_failed_speak_caption_twice_closes_with_reason():
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.play_prepared_sentences_with_caption.side_effect = [
        ["speak-1"],
        ["speak-2"],
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [
        False,
        False,
    ]

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.CLOSING
    assert ctx.closing_reason == "technical_failure"
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action", "speak-1"]),
        call(["scene-action", "speak-2"]),
    ])


@pytest.mark.asyncio
async def test_handle_news_finished():
    news_service = MagicMock()
    news_service.has_next.return_value = False
    
    ctx = _make_ctx(news_service=news_service)
    phase = await handle_news(ctx)
    
    assert phase == BroadcastPhase.QA
    ctx.saint_graph.process_news_finished.assert_called_once()
    ctx.saint_graph.body.clear_news_caption.assert_called_once()


@pytest.mark.asyncio
async def test_handle_qa_wait():
    ctx = _make_ctx()
    phase = await handle_qa(ctx)
    
    assert phase == BroadcastPhase.QA
    assert ctx.idle_counter == 1
    ctx.saint_graph.body.queue_scene_switch.assert_called_once_with("kurara_main")
    ctx.saint_graph.body.wait_for_queue_strict.assert_called_once_with(["scene-action"])


@pytest.mark.asyncio
async def test_handle_qa_timeout():
    ctx = _make_ctx()
    ctx.idle_counter = MAX_WAIT_CYCLES
    phase = await handle_qa(ctx)
    
    assert phase == BroadcastPhase.CLOSING


@pytest.mark.asyncio
async def test_handle_qa_queues_main_scene_only_once():
    ctx = _make_ctx()

    first = await handle_qa(ctx)
    second = await handle_qa(ctx)

    assert first == BroadcastPhase.QA
    assert second == BroadcastPhase.QA
    ctx.saint_graph.body.queue_scene_switch.assert_called_once_with("kurara_main")


@pytest.mark.asyncio
async def test_handle_qa_closes_when_main_scene_strict_fails():
    ctx = _make_ctx()
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"action_id": "qa-1"},
        {"action_id": "qa-2"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [False, False]

    phase = await handle_qa(ctx)

    assert phase == BroadcastPhase.CLOSING
    assert ctx.closing_reason == "technical_failure"
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["qa-1"]),
        call(["qa-2"]),
    ])


@pytest.mark.asyncio
async def test_handle_closing():
    ctx = _make_ctx()
    # asyncio.sleep をモックしてテストを高速化
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)
    
    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once_with(reason=None)
    ctx.saint_graph.body.queue_scene_switch.assert_called_once_with("ending")
    ctx.saint_graph.body.wait_for_queue_strict.assert_called_once_with(["scene-action"])


@pytest.mark.asyncio
async def test_handle_closing_passes_technical_failure_reason():
    ctx = _make_ctx()
    ctx.closing_reason = "technical_failure"
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)

    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once_with(reason="technical_failure")


@pytest.mark.asyncio
async def test_handle_closing_continues_when_ending_scene_strict_fails(caplog):
    caplog.set_level("WARNING", logger="saint-graph")
    ctx = _make_ctx()
    ctx.saint_graph.body.queue_scene_switch.return_value = {"action_id": "ending"}
    ctx.saint_graph.body.wait_for_queue_strict.return_value = False

    phase = await handle_closing(ctx)

    assert phase is None
    ctx.saint_graph.body.queue_scene_switch.assert_called_once_with("ending")
    ctx.saint_graph.body.wait_for_queue_strict.assert_called_once_with(["ending"])
    assert "CLOSING ending scene_switch failed" in caplog.text


@pytest.mark.asyncio
async def test_run_broadcast_loop_moves_startup_presentation_to_loop():
    ctx = _make_ctx()

    async def finish_immediately(_ctx):
        return None

    with patch("saint_graph.broadcast_loop._HANDLERS", {BroadcastPhase.INTRO: finish_immediately}):
        await run_broadcast_loop(ctx)

    ctx.saint_graph.body.queue_caption_clear.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(["caption-clear"])
    ctx.saint_graph.register_chitchat.assert_called_once()
    ctx.saint_graph.body.queue_auto_filler_start.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(["auto-start"])
    ctx.saint_graph.body.queue_auto_filler_stop.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(["auto-stop"])


@pytest.mark.asyncio
async def test_run_broadcast_loop_warns_and_continues_when_auto_filler_start_fails(caplog):
    caplog.set_level("WARNING", logger="saint-graph")
    ctx = _make_ctx()
    handled = False
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [
        True,   # startup caption clear
        False,  # auto-filler start
        True,   # auto-filler stop
    ]

    async def finish_immediately(_ctx):
        nonlocal handled
        handled = True
        return None

    with patch("saint_graph.broadcast_loop._HANDLERS", {BroadcastPhase.INTRO: finish_immediately}):
        await run_broadcast_loop(ctx)

    assert handled is True
    ctx.saint_graph.body.queue_auto_filler_start.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["caption-clear"]),
        call(["auto-start"]),
        call(["auto-stop"]),
    ])
    assert "broadcast startup auto_filler_start failed" in caplog.text


@pytest.mark.asyncio
async def test_run_broadcast_loop_continues_when_auto_filler_start_has_no_action_id(caplog):
    caplog.set_level("WARNING", logger="saint-graph")
    ctx = _make_ctx()
    handled = False
    ctx.saint_graph.body.queue_auto_filler_start.return_value = {"status": "error"}

    async def finish_immediately(_ctx):
        nonlocal handled
        handled = True
        return None

    with patch("saint_graph.broadcast_loop._HANDLERS", {BroadcastPhase.INTRO: finish_immediately}):
        await run_broadcast_loop(ctx)

    assert handled is True
    assert "broadcast startup auto_filler_start did not return action_id" in caplog.text
    ctx.saint_graph.body.wait_for_queue.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["caption-clear"]),
        call(["auto-stop"]),
    ])


@pytest.mark.asyncio
async def test_run_broadcast_loop_detects_startup_caption_clear_failure():
    ctx = _make_ctx()
    ctx.saint_graph.body.queue_caption_clear.side_effect = [
        {"action_id": "clear-1"},
        {"action_id": "clear-2"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [False, False, True]

    async def closing_handler(_ctx):
        assert _ctx.closing_reason == "technical_failure"
        return None

    with patch("saint_graph.broadcast_loop._HANDLERS", {BroadcastPhase.CLOSING: closing_handler}):
        await run_broadcast_loop(ctx)

    ctx.saint_graph.register_chitchat.assert_not_called()
    ctx.saint_graph.body.queue_auto_filler_start.assert_not_called()
    ctx.saint_graph.body.queue_auto_filler_stop.assert_called_once()
