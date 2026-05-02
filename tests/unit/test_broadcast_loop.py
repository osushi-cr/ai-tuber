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
    # waiting シーン中に Gemini で挨拶テキストだけ取得するメソッド。
    # 既定で 1 文返し、 後続の play_prepared_sentences で TTS+再生される。
    mock_saint.prepare_intro_text = AsyncMock(return_value=[("joyful", "やっほ〜")])
    mock_saint.process_news_reading = AsyncMock()
    mock_saint.prepare_news_reading_text = AsyncMock(return_value=[("neutral", "本文")])
    mock_saint.play_prepared_sentences = AsyncMock(return_value=["intro-speak"])
    mock_saint.play_prepared_sentences_with_caption = AsyncMock(return_value=["speak-action"])
    mock_saint.process_news_finished = AsyncMock()
    mock_saint.process_closing = AsyncMock()
    
    mock_saint.body = MagicMock()
    mock_saint.body.get_comments = AsyncMock(return_value=comments or [])
    mock_saint.body.wait_for_queue = AsyncMock()
    mock_saint.body.update_news_caption = AsyncMock()
    mock_saint.body.clear_news_caption = AsyncMock()
    mock_saint.body.set_caption = AsyncMock()
    mock_saint.body.set_content_image = AsyncMock()
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
    ctx.saint_graph.prepare_intro_text.assert_called_once()
    ctx.saint_graph.play_prepared_sentences.assert_called_once()
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()
    ctx.saint_graph.body.queue_scene_switch.assert_has_calls([
        call("waiting"),
        call("kurara_main"),
    ])


@pytest.mark.asyncio
async def test_handle_intro_switches_bgm_chitchat_then_op():
    """waiting で chitchat BGM、 kurara_main 切替後に op BGM を流す。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)

    await handle_intro(ctx)

    ctx.saint_graph.body.switch_bgm.assert_has_calls([
        call("chitchat"),
        call("op"),
    ])


@pytest.mark.asyncio
async def test_handle_intro_orders_text_generation_before_scene_switch_and_speech():
    """waiting 中に prepare_intro_text → kurara_main 切替 → play_prepared_sentences の順。
    Gemini テキスト取得は waiting シーン中、 TTS+再生は kurara_main 切替後。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    call_order: list[str] = []

    ctx = _make_ctx(news_service=news_service)

    async def record_scene(scene):
        call_order.append(f"scene:{scene}")
        return {"action_id": "scene-action"}

    async def record_prepare():
        call_order.append("prepare_intro_text")
        return [("joyful", "やっほ〜")]

    async def record_play(sentences, wait_after=True):
        call_order.append(f"play_prepared_sentences:wait_after={wait_after}")
        return ["intro-speak"]

    async def record_strict(action_ids=None, **kwargs):
        if action_ids == ["intro-speak"]:
            call_order.append("wait_intro_speech")
        return True

    async def record_filler_start():
        call_order.append("auto_filler_start")
        return {"action_id": "auto-start"}

    ctx.saint_graph.body.queue_scene_switch = AsyncMock(side_effect=record_scene)
    ctx.saint_graph.prepare_intro_text = AsyncMock(side_effect=record_prepare)
    ctx.saint_graph.play_prepared_sentences = AsyncMock(side_effect=record_play)
    ctx.saint_graph.body.wait_for_queue_strict = AsyncMock(side_effect=record_strict)
    ctx.saint_graph.body.queue_auto_filler_start = AsyncMock(side_effect=record_filler_start)

    await handle_intro(ctx)

    # waiting → prepare_intro_text (Gemini) → kurara_main 切替 → TTS+再生投入
    # → INTRO 再生完了待ち → auto_filler 起動
    assert call_order == [
        "scene:waiting",
        "prepare_intro_text",
        "scene:kurara_main",
        "play_prepared_sentences:wait_after=False",
        "wait_intro_speech",
        "auto_filler_start",
    ]


@pytest.mark.asyncio
async def test_handle_intro_starts_auto_filler_after_intro_speech():
    """auto_filler は INTRO 完了後に起動する（INTRO 中の chitchat 割り込み防止）。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.play_prepared_sentences = AsyncMock(return_value=["intro-action"])

    await handle_intro(ctx)

    ctx.saint_graph.prepare_intro_text.assert_called_once()
    ctx.saint_graph.play_prepared_sentences.assert_called_once()
    assert ctx.saint_graph.play_prepared_sentences.call_args.kwargs["wait_after"] is False
    ctx.saint_graph.body.queue_auto_filler_start.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(
        action_ids=["intro-action"]
    )


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
    ctx.saint_graph.prepare_intro_text.assert_called_once()
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
    """次ニュース prefetch が未完了時は、 コメントが来てたら反応 turn で時間稼ぎする。"""
    ctx = _make_ctx(comments=[{"author": "User", "message": "Hi?"}])
    # next_news_task が None なら next_ready=False → コメント反応経路
    assert ctx.next_news_task is None
    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.NEWS
    # コメント応答は process_turn を直接呼ぶ（共通ユーティリティ）
    ctx.saint_graph.process_turn.assert_called_once()
    assert "User: Hi" in ctx.saint_graph.process_turn.call_args[0][0]
    ctx.saint_graph.process_news_reading.assert_not_called()
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_skips_comment_when_prefetch_is_ready():
    """次ニュースの prefetch task が done なら、 コメント拾いをスキップして即ニュース読み上げに進む。"""
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item

    ctx = _make_ctx(news_service=news_service, comments=[
        {"author": "User", "message": "気になる！"},
    ])

    # prefetch task を「即完了する future」として用意（done=True）
    ready_future: asyncio.Future = asyncio.Future()
    ready_future.set_result([("neutral", "本文")])
    ctx.next_news_task = ready_future

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.NEWS
    # コメント来てたが prefetch ready のため反応 turn は走らない
    ctx.saint_graph.process_turn.assert_not_called()
    # 直接ニュース読み上げに進む
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_called_once()


@pytest.mark.asyncio
async def test_handle_news_with_single_comment_updates_caption_with_author_and_clears_after():
    """単一コメント picking 時: type=comment / title=視聴者名 / summary=本文のみ → 反応後にクリア。"""
    ctx = _make_ctx(comments=[{"author": "視聴者A", "message": "テストコメントだよ"}])
    await handle_news(ctx)

    ctx.saint_graph.body.set_caption.assert_any_call(
        type="comment", title="視聴者A", summary="テストコメントだよ"
    )
    ctx.saint_graph.body.set_caption.assert_any_call(visible=False)


@pytest.mark.asyncio
async def test_handle_news_with_multiple_comments_lists_authors_and_joins_messages():
    """複数コメント picking 時: title=「先頭名 ほか N 名」 / summary=本文のみ改行連記（視聴者名は title 側のみ）。"""
    ctx = _make_ctx(comments=[
        {"author": "視聴者A", "message": "メッセージ1"},
        {"author": "視聴者B", "message": "メッセージ2"},
        {"author": "視聴者C", "message": "メッセージ3"},
    ])
    await handle_news(ctx)

    expected_summary = "メッセージ1\nメッセージ2\nメッセージ3"
    ctx.saint_graph.body.set_caption.assert_any_call(
        type="comment", title="視聴者A ほか 2 名", summary=expected_summary
    )
    ctx.saint_graph.body.set_caption.assert_any_call(visible=False)


@pytest.mark.asyncio
async def test_handle_news_does_not_manually_toggle_auto_filler_during_comment_response():
    """コメント反応 turn では auto_filler_stop/start を手動で呼ばない。
    text 生成中（queue 空）は filler が出て沈黙感を埋め、 speak 投入後は
    body-streamer 側の _auto_filler_loop が `queue empty` チェックで自動抑制する。"""
    ctx = _make_ctx(comments=[{"author": "視聴者A", "message": "こんにちは"}])
    await handle_news(ctx)

    ctx.saint_graph.body.queue_auto_filler_stop.assert_not_called()
    ctx.saint_graph.body.queue_auto_filler_start.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_does_not_toggle_auto_filler_even_if_process_turn_raises():
    """process_turn が例外で死んでも auto_filler は触らない（手動 stop/start なし方針）。"""
    ctx = _make_ctx(comments=[{"author": "視聴者A", "message": "こんにちは"}])
    ctx.saint_graph.process_turn.side_effect = RuntimeError("Gemini failure")

    await handle_news(ctx)

    ctx.saint_graph.body.queue_auto_filler_stop.assert_not_called()
    ctx.saint_graph.body.queue_auto_filler_start.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_clears_comment_caption_even_if_process_turn_raises():
    """process_turn で例外が出ても caption は確実にクリアされる。"""
    ctx = _make_ctx(comments=[{"author": "視聴者A", "message": "テストコメント"}])
    ctx.saint_graph.process_turn.side_effect = RuntimeError("Gemini failure")

    # _poll_and_respond は内部で例外を握りつぶす（False を返す）が、
    # caption の clear は finally で確実に呼ばれる
    await handle_news(ctx)

    # set_caption は最低 2 回呼ばれる: comment 表示 + 反応後クリア
    set_calls = ctx.saint_graph.body.set_caption.call_args_list
    assert any(c.kwargs.get("type") == "comment" for c in set_calls)
    assert any(c.kwargs.get("visible") is False for c in set_calls)


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
    # scene strict と speak strict は別呼出（仕様変更で speak の retry を撤去）
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action"]),
        call(["speak-action"]),
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
        call(["scene-action"]),
        call(["speak-action"]),
    ])
    ctx.saint_graph.body.wait_for_queue.assert_not_called()
    ctx.saint_graph.process_news_reading.assert_not_called()
    ctx.saint_graph.prepare_news_reading_text.assert_called_once_with(
        title="Next", content="Next content"
    )
    assert ctx.next_news_task is not None
    await ctx.next_news_task


@pytest.mark.asyncio
async def test_handle_news_scene_strict_retries_then_closes_on_double_failure():
    """scene strict は retry 付き helper（_queue_and_wait_strict）。 2 回失敗で CLOSING、 speak は投入されない。"""
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
        call(["scene-1"]),
        call(["scene-2"]),
    ])
    ctx.saint_graph.prepare_news_reading_text.assert_called_once_with(
        title="Title", content="Content"
    )
    # scene 確定前に CLOSING へフォールバックするため speak は投入されない
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_not_called()


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
async def test_handle_intro_continues_when_waiting_scene_fails_once(caplog):
    """waiting scene は best-effort（once helper）のため、 失敗しても retry せず loop は継続する。"""
    caplog.set_level("WARNING", logger="saint-graph")
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"action_id": "waiting-1"},
        {"action_id": "main"},
    ]
    # waiting strict 失敗、 main strict、 INTRO speech strict、 auto_filler_start strict
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [False, True, True, True]

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    assert ctx.closing_reason is None
    assert "INTRO start waiting_scene failed" in caplog.text
    # waiting は 1 回投入のみ（retry なし）、 続いて main 切替
    ctx.saint_graph.body.queue_scene_switch.assert_has_calls([
        call("waiting"),
        call("kurara_main"),
    ])
    # waiting strict, main strict, INTRO speech action 待ち, auto_filler_start strict
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["waiting-1"]),
        call(["main"]),
        call(action_ids=["intro-speak"]),
        call(["auto-start"]),
    ])


@pytest.mark.asyncio
async def test_handle_intro_continues_when_waiting_scene_has_no_action_id():
    """waiting の queue 投入で action_id が返らなくても、 loop は止めず main 切替へ進む。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch.side_effect = [
        {"status": "error"},
        {"action_id": "main"},
    ]
    ctx.saint_graph.body.wait_for_queue_strict.return_value = True

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    # action_id 欠落時のフォールバックとして wait_for_queue が呼ばれる
    ctx.saint_graph.body.wait_for_queue.assert_called_once()
    # main strict, INTRO speech strict, auto_filler_start strict が実行される
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["main"]),
        call(action_ids=["intro-speak"]),
        call(["auto-start"]),
    ])


@pytest.mark.asyncio
async def test_handle_news_scene_strict_retry_then_speak_succeeds():
    """scene strict は retry 付き、 speak strict は retry なしで成功する正常パス。"""
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.play_prepared_sentences_with_caption.return_value = ["speak-1"]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [
        False,  # scene strict 1 回目失敗
        True,   # scene strict retry で成功
        True,   # speak strict 成功
    ]

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.NEWS
    assert ctx.closing_reason is None
    # play_prepared_sentences_with_caption は scene 確定後に 1 回だけ呼ばれる（retry なし）
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_called_once_with(
        [("neutral", "本文")],
        caption_title="Title",
        caption_summary="Content",
        wait_after=False,
    )
    # strict は scene 2 回（retry）と speak 1 回の合計 3 回
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action"]),
        call(["scene-action"]),
        call(["speak-1"]),
    ])


@pytest.mark.asyncio
async def test_handle_news_strict_checks_all_speak_action_ids():
    """speak strict は全 speak action_id を一括で確認する（scene strict とは別呼出）。"""
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
    # scene strict と speak strict は別呼出。speak 側は全 ids 一括
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action"]),
        call(["speak-1", "speak-2"]),
    ])


@pytest.mark.asyncio
async def test_handle_news_speak_failure_closes_immediately_without_retry():
    """speak strict 失敗は retry せず即 CLOSING/technical_failure。 二重発話を避ける。"""
    news_service = MagicMock()
    news_service.has_next.side_effect = [True, False]
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.get_next_item.return_value = item

    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.play_prepared_sentences_with_caption.return_value = ["speak-1"]
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [
        True,   # scene strict 成功
        False,  # speak strict 失敗（retry なし）
    ]

    phase = await handle_news(ctx)

    assert phase == BroadcastPhase.CLOSING
    assert ctx.closing_reason == "technical_failure"
    # play_prepared_sentences_with_caption は 1 回しか呼ばれない（retry なしで二重発話を回避）
    ctx.saint_graph.play_prepared_sentences_with_caption.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_has_calls([
        call(["scene-action"]),
        call(["speak-1"]),
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
async def test_handle_closing(monkeypatch):
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    ctx = _make_ctx()
    # asyncio.sleep をモックしてテストを高速化
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)

    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once_with(reason=None)
    ctx.saint_graph.body.queue_scene_switch.assert_called_once_with("ending")
    ctx.saint_graph.body.wait_for_queue_strict.assert_called_once_with(["scene-action"])


@pytest.mark.asyncio
async def test_handle_closing_passes_technical_failure_reason(monkeypatch):
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    ctx = _make_ctx()
    ctx.closing_reason = "technical_failure"
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)

    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once_with(reason="technical_failure")


@pytest.mark.asyncio
async def test_handle_closing_holds_ending_scene_for_configured_duration(monkeypatch):
    """ending シーン切替後に BROADCAST_ENDING_DURATION 秒だけ asyncio.sleep する。"""
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "45")
    ctx = _make_ctx()

    sleep_calls: list[float] = []

    async def record_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("asyncio.sleep", side_effect=record_sleep):
        await handle_closing(ctx)

    # ending 切替後の 45 秒 sleep が呼ばれる（他にも内部 sleep があれば混じる）
    assert 45.0 in sleep_calls


@pytest.mark.asyncio
async def test_handle_closing_skips_sleep_when_duration_is_zero(monkeypatch):
    """BROADCAST_ENDING_DURATION=0 で待機をスキップする。"""
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    ctx = _make_ctx()

    sleep_calls: list[float] = []

    async def record_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("asyncio.sleep", side_effect=record_sleep):
        await handle_closing(ctx)

    # 60 秒や 45 秒のような ending 用待機は走らない
    assert 60.0 not in sleep_calls
    assert 45.0 not in sleep_calls


@pytest.mark.asyncio
async def test_handle_closing_uses_closing_pool_when_available(monkeypatch, tmp_path):
    """CLOSING_POOL_DIR に closing_*.wav があれば Gemini を使わずプールから再生する。"""
    import wave

    pool_dir = tmp_path / "closings"
    pool_dir.mkdir()
    for i in range(1, 4):
        wav_path = pool_dir / f"closing_{i:02d}.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"")
    monkeypatch.setenv("CLOSING_POOL_DIR", str(pool_dir))
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")

    ctx = _make_ctx()
    ctx.saint_graph.body.queue_filler = AsyncMock(
        return_value={"action_id": "closing-action"}
    )

    phase = await handle_closing(ctx)

    assert phase is None
    # Gemini は呼ばない
    ctx.saint_graph.process_closing.assert_not_called()
    # プールから 1 件選ばれて queue_filler に渡される
    ctx.saint_graph.body.queue_filler.assert_called_once()
    call_kwargs = ctx.saint_graph.body.queue_filler.call_args.kwargs
    assert call_kwargs["file_path"].endswith(".wav")
    assert "closing_" in call_kwargs["file_path"]
    # action_id を strict 待ち
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(
        action_ids=["closing-action"]
    )
    # ending シーン切替
    ctx.saint_graph.body.queue_scene_switch.assert_called_once_with("ending")


@pytest.mark.asyncio
async def test_handle_closing_falls_back_to_gemini_when_pool_empty(monkeypatch, tmp_path):
    """CLOSING_POOL_DIR が存在しても wav が無ければ Gemini フォールバック。"""
    pool_dir = tmp_path / "closings"
    pool_dir.mkdir()
    monkeypatch.setenv("CLOSING_POOL_DIR", str(pool_dir))
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")

    ctx = _make_ctx()
    with patch("asyncio.sleep", return_value=None):
        phase = await handle_closing(ctx)

    assert phase is None
    ctx.saint_graph.process_closing.assert_called_once()


@pytest.mark.asyncio
async def test_handle_closing_stops_auto_filler_at_entry(monkeypatch):
    """CLOSING 突入時に auto_filler_stop を queue 投入する（chitchat 割り込み防止）。"""
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    ctx = _make_ctx()

    with patch("asyncio.sleep", return_value=None):
        await handle_closing(ctx)

    ctx.saint_graph.body.queue_auto_filler_stop.assert_called()


@pytest.mark.asyncio
async def test_handle_closing_continues_when_ending_scene_strict_fails(caplog, monkeypatch):
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
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
    """run_broadcast_loop 冒頭で caption_clear と register_chitchat、
    終了時に auto_filler_stop が呼ばれる。
    auto_filler_start は handle_intro 内に移動したのでここでは呼ばれない。"""
    ctx = _make_ctx()

    async def finish_immediately(_ctx):
        return None

    with patch("saint_graph.broadcast_loop._HANDLERS", {BroadcastPhase.INTRO: finish_immediately}):
        await run_broadcast_loop(ctx)

    ctx.saint_graph.body.queue_caption_clear.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(["caption-clear"])
    ctx.saint_graph.register_chitchat.assert_called_once()
    # auto_filler_start は handle_intro 内に移動したため、 ハンドラを stub した
    # このテストでは呼ばれない
    ctx.saint_graph.body.queue_auto_filler_start.assert_not_called()
    # 終了時の auto_filler_stop は引き続き finally で呼ばれる
    ctx.saint_graph.body.queue_auto_filler_stop.assert_called_once()
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(["auto-stop"])


@pytest.mark.asyncio
async def test_handle_intro_warns_and_continues_when_auto_filler_start_fails(caplog):
    """auto_filler_start が strict 失敗しても handle_intro は NEWS フェーズへ進む。"""
    caplog.set_level("WARNING", logger="saint-graph")
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    # waiting strict, main strict, INTRO speech strict, auto_filler_start strict
    ctx.saint_graph.body.wait_for_queue_strict.side_effect = [True, True, True, False]

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.body.queue_auto_filler_start.assert_called_once()
    assert "INTRO end auto_filler_start failed" in caplog.text


@pytest.mark.asyncio
async def test_handle_intro_continues_when_auto_filler_start_has_no_action_id(caplog):
    """auto_filler_start の queue 投入で action_id が返らなくても handle_intro は継続。"""
    caplog.set_level("WARNING", logger="saint-graph")
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_auto_filler_start.return_value = {"status": "error"}

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    assert "INTRO end auto_filler_start did not return action_id" in caplog.text


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


# ---------------------------------------------------------------------------
# content 画像 overlay（くららの右に intro / qa / end 画像を出す）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_intro_sets_intro_content_image_during_speech():
    """kurara_main 切替後、 挨拶 speak 投入前に intro 画像を表示する。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)

    await handle_intro(ctx)

    ctx.saint_graph.body.set_content_image.assert_any_call(image="intro")


@pytest.mark.asyncio
async def test_handle_intro_clears_content_image_before_news_phase():
    """挨拶完了後、 NEWS フェーズに渡す前に intro 画像を畳む。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)

    await handle_intro(ctx)

    ctx.saint_graph.body.set_content_image.assert_any_call(visible=False)


@pytest.mark.asyncio
async def test_handle_intro_does_not_set_caption_for_intro_text():
    """intro テキスト caption は出さず content 画像のみ。 set_caption(type=intro) を呼ばない。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    ctx = _make_ctx(news_service=news_service)

    await handle_intro(ctx)

    intro_caption_calls = [
        c for c in ctx.saint_graph.body.set_caption.call_args_list
        if c.kwargs.get("type") == "intro"
    ]
    assert intro_caption_calls == []


@pytest.mark.asyncio
async def test_handle_qa_sets_qa_content_image_on_first_entry():
    """QA フェーズ初回入口で qa 画像を表示する。"""
    ctx = _make_ctx()

    await handle_qa(ctx)

    ctx.saint_graph.body.set_content_image.assert_called_once_with(image="qa")


@pytest.mark.asyncio
async def test_handle_qa_does_not_reset_content_image_on_reentry():
    """QA は loop で複数回呼ばれるが、 content 画像は初回のみ set。"""
    ctx = _make_ctx()

    await handle_qa(ctx)
    await handle_qa(ctx)

    assert ctx.saint_graph.body.set_content_image.call_count == 1


@pytest.mark.asyncio
async def test_handle_closing_sets_end_content_image_then_clears_before_ending_scene(monkeypatch, tmp_path):
    """CLOSING 冒頭で end 画像、 ending scene 切替前に clear する（QA 画像を上書き）。"""
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    monkeypatch.setenv("CLOSING_POOL_DIR", str(tmp_path))
    ctx = _make_ctx()

    with patch("asyncio.sleep", return_value=None):
        await handle_closing(ctx)

    calls = ctx.saint_graph.body.set_content_image.call_args_list
    # 1 回目: end 画像 set / 2 回目: ending 切替前に clear
    assert calls[0] == call(image="end")
    assert calls[-1] == call(visible=False)


@pytest.mark.asyncio
async def test_handle_closing_does_not_set_caption_for_closing_text(monkeypatch, tmp_path):
    """closing テキスト caption は出さず content 画像のみ。 set_caption(type=closing) を呼ばない。"""
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    monkeypatch.setenv("CLOSING_POOL_DIR", str(tmp_path))
    ctx = _make_ctx()

    with patch("asyncio.sleep", return_value=None):
        await handle_closing(ctx)

    closing_caption_calls = [
        c for c in ctx.saint_graph.body.set_caption.call_args_list
        if c.kwargs.get("type") == "closing"
    ]
    assert closing_caption_calls == []
