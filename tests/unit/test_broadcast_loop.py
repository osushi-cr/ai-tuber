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
    mock_saint.body.wait_for_queue_strict = AsyncMock(return_value=True)

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


@pytest.mark.asyncio
async def test_handle_qa_timeout():
    ctx = _make_ctx()
    ctx.idle_counter = MAX_WAIT_CYCLES
    phase = await handle_qa(ctx)
    
    assert phase == BroadcastPhase.CLOSING


@pytest.mark.asyncio
async def test_handle_closing():
    ctx = _make_ctx()
    # asyncio.sleep をモックしてテストを高速化
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)
    
    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once_with(reason=None)


@pytest.mark.asyncio
async def test_handle_closing_passes_technical_failure_reason():
    ctx = _make_ctx()
    ctx.closing_reason = "technical_failure"
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)

    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once_with(reason="technical_failure")
