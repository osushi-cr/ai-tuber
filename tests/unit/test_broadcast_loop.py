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
    handle_waiting,
    handle_intro,
    handle_news,
    handle_qa,
    handle_closing,
    run_broadcast_loop,
    _preload_next_news,
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
    # WAITING フェーズで使う先行合成メソッド。 sentences のリストを受け取り、 各 sentence の
    # TTS 合成結果 (file_path / duration / style / text) を辞書で返す。
    mock_saint.prepare_sentences_synth = AsyncMock(
        side_effect=lambda sentences: [
            {"file_path": f"/tmp/{i}.wav", "duration": 1.0, "style": s[0], "text": s[1]}
            for i, s in enumerate(sentences)
        ]
    )
    # NEWS フェーズで news_finished / QA 開始 / QA 初手 chitchat の Gemini 生成。
    # 既定で 1 文ずつ返す。 prepare_sentences_synth で wav 化される前提。
    mock_saint.prepare_news_finished_text = AsyncMock(return_value=[("neutral", "おしまい")])
    mock_saint.prepare_qa_intro_text = AsyncMock(return_value=[("joyful", "コメントどうぞ〜")])
    mock_saint.prepare_qa_chitchat_text = AsyncMock(return_value=[("neutral", "雑談")])
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
    mock_saint.body.queue_bgm_switch = AsyncMock(return_value={"action_id": "bgm-action"})
    mock_saint.body.queue_content_set = AsyncMock(return_value={"action_id": "content-action"})
    mock_saint.body.queue_speak = AsyncMock(return_value={"action_id": "speak-action"})
    mock_saint.body.wait_for_queue_strict = AsyncMock(return_value=True)
    mock_saint.register_chitchat = AsyncMock()

    mock_news = news_service or MagicMock()

    return BroadcastContext(
        saint_graph=mock_saint,
        news_service=mock_news,
    )


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

    with patch("saint_graph.broadcast_loop._HANDLERS", {BroadcastPhase.WAITING: finish_immediately}):
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


@pytest.mark.asyncio
async def test_preload_next_news_falls_back_to_qa_chitchat_on_last_news():
    """最後の news 再生中（has_next=False）は QA 初手 chitchat を裏で先行投入する。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    item1 = MagicMock(); item1.title = "T1"
    item2 = MagicMock(); item2.title = "T2"
    news_service.items = [item1, item2]
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.prepare_qa_chitchat_text = AsyncMock(
        return_value=[("neutral", "今日のニュース振り返り")]
    )
    ctx.saint_graph.play_prepared_sentences = AsyncMock(return_value=["qa-speak"])

    await _preload_next_news(ctx)

    ctx.saint_graph.prepare_qa_chitchat_text.assert_called_once_with(
        recent_titles=["T1", "T2"]
    )
    ctx.saint_graph.play_prepared_sentences.assert_called_once()
    assert ctx.preloaded_qa_action_ids == ["qa-speak"]
    # 通常の news prefetch 経路は呼ばれない
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()
    # QA chitchat sentences の前に scene_switch + qa content image を投入している
    ctx.saint_graph.body.queue_scene_switch.assert_any_call("kurara_main")
    ctx.saint_graph.body.set_content_image.assert_any_call(image="qa")
    # handle_qa 側で scene init を二重実行しないようマーク済
    assert BroadcastPhase.QA in ctx.phase_scene_initialized


@pytest.mark.asyncio
async def test_preload_first_qa_chitchat_orders_scene_before_chitchat_enqueue():
    """scene_switch / set_content_image は QA chitchat sentences より先に enqueue される。

    body queue は順序保証のため、enqueue 順がそのまま再生順になる。
    """
    news_service = MagicMock()
    news_service.has_next.return_value = False
    item1 = MagicMock(); item1.title = "T1"
    news_service.items = [item1]
    ctx = _make_ctx(news_service=news_service)
    call_order: list[str] = []

    async def record_scene(scene):
        call_order.append(f"scene_switch:{scene}")
        return {"action_id": "scene"}

    async def record_image(**kwargs):
        call_order.append(f"set_content_image:{kwargs}")

    async def record_play(sentences, wait_after=True):
        call_order.append(f"play_prepared_sentences:wait_after={wait_after}")
        return ["qa-speak"]

    ctx.saint_graph.body.queue_scene_switch = AsyncMock(side_effect=record_scene)
    ctx.saint_graph.body.set_content_image = AsyncMock(side_effect=record_image)
    ctx.saint_graph.prepare_qa_chitchat_text = AsyncMock(
        return_value=[("neutral", "振り返り")]
    )
    ctx.saint_graph.play_prepared_sentences = AsyncMock(side_effect=record_play)

    await _preload_next_news(ctx)

    assert call_order == [
        "scene_switch:kurara_main",
        "set_content_image:{'image': 'qa'}",
        "play_prepared_sentences:wait_after=False",
    ]


@pytest.mark.asyncio
async def test_preload_first_qa_chitchat_skips_scene_init_mark_on_scene_failure():
    """scene_switch が失敗した場合は phase_scene_initialized に QA を add しない。

    handle_qa 側で通常の scene init 経路を走らせるため。
    """
    news_service = MagicMock()
    news_service.has_next.return_value = False
    item1 = MagicMock(); item1.title = "T1"
    news_service.items = [item1]
    ctx = _make_ctx(news_service=news_service)
    ctx.saint_graph.body.queue_scene_switch = AsyncMock(
        side_effect=RuntimeError("OBS not connected")
    )
    ctx.saint_graph.prepare_qa_chitchat_text = AsyncMock(
        return_value=[("neutral", "x")]
    )
    ctx.saint_graph.play_prepared_sentences = AsyncMock(return_value=["qa-speak"])

    await _preload_next_news(ctx)

    # chitchat 自体は enqueue される（fallback）
    assert ctx.preloaded_qa_action_ids == ["qa-speak"]
    # ただし scene_switch 失敗時は scene init マークしない
    assert BroadcastPhase.QA not in ctx.phase_scene_initialized


@pytest.mark.asyncio
async def test_preload_next_news_skips_qa_chitchat_when_already_preloaded():
    """同じ has_next=False 状況で二度呼ばれても QA chitchat の二重投入は起きない。"""
    news_service = MagicMock()
    news_service.has_next.return_value = False
    news_service.items = []
    ctx = _make_ctx(news_service=news_service)
    ctx.preloaded_qa_action_ids = ["existing"]
    ctx.saint_graph.prepare_qa_chitchat_text = AsyncMock(return_value=[("neutral", "x")])
    ctx.saint_graph.play_prepared_sentences = AsyncMock(return_value=["another"])

    await _preload_next_news(ctx)

    ctx.saint_graph.prepare_qa_chitchat_text.assert_not_called()
    assert ctx.preloaded_qa_action_ids == ["existing"]


def _ctx_with_prepared(news_service=None, prepared_intro=None, prepared_news1=None):
    """handle_intro テスト用に prepared_intro / prepared_news1 を事前にセットした ctx。"""
    ctx = _make_ctx(news_service=news_service)
    ctx.prepared_intro = prepared_intro or [
        {"file_path": "/tmp/intro_0.wav", "duration": 3.0, "style": "joyful", "text": "やっほ〜"}
    ]
    ctx.prepared_news1 = prepared_news1 or [
        {"file_path": "/tmp/news1_0.wav", "duration": 5.0, "style": "neutral", "text": "本文"}
    ]
    return ctx


@pytest.mark.asyncio
async def test_handle_intro_uses_prepared_speeches_without_resynthesis():
    """handle_intro は ctx.prepared_intro / prepared_news1 を使い、 再合成 (prepare_intro_text /
    prepare_news_reading_text / prepare_sentences_synth) は呼ばない。
    """
    news_service = MagicMock()
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item
    news_service.has_next.return_value = True

    ctx = _ctx_with_prepared(news_service=news_service)

    phase = await handle_intro(ctx)

    assert phase == BroadcastPhase.NEWS
    ctx.saint_graph.prepare_intro_text.assert_not_called()
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()


@pytest.mark.asyncio
async def test_handle_intro_queue_order():
    """handle_intro の enqueue 順序:
    scene_switch(kurara_main) → bgm(op) → content_set(intro) → intro speak (prepared) →
    content_set("") → bgm(news) → news1 speak (prepared)。

    speak / scene / bgm / content が全部 worker queue を通るので、 視聴者目線では
    順序通りに切り替わる。
    """
    news_service = MagicMock()
    item = MagicMock()
    item.title = "Title"
    item.content = "Summary"
    news_service.peek_current_item.return_value = item
    news_service.has_next.return_value = True

    ctx = _ctx_with_prepared(news_service=news_service)

    # speak は body.queue_speak で投入、 content は body.queue_content_set。
    # 順序を見るため、 単一の events list に時系列で記録する。
    events = []

    async def queue_scene_switch(scene):
        events.append(("scene_switch", scene))
        return {"action_id": f"scene-{scene}"}

    async def queue_bgm_switch(bgm_id):
        events.append(("bgm_switch", bgm_id))
        return {"action_id": f"bgm-{bgm_id}"}

    async def queue_content_set(image, visible):
        events.append(("content_set", image, visible))
        return {"action_id": f"content-{image}-{visible}"}

    async def queue_speak(text=None, style=None, speaker_id=None,
                         caption_title=None, caption_summary=None,
                         prepared_wav_path=None, prepared_duration=None):
        events.append(("speak", prepared_wav_path, caption_title))
        return {"action_id": f"speak-{prepared_wav_path}"}

    ctx.saint_graph.body.queue_scene_switch = AsyncMock(side_effect=queue_scene_switch)
    ctx.saint_graph.body.queue_bgm_switch = AsyncMock(side_effect=queue_bgm_switch)
    ctx.saint_graph.body.queue_content_set = AsyncMock(side_effect=queue_content_set)
    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)

    await handle_intro(ctx)

    # 期待順序: scene → bgm(op) → content(intro) → speak(intro) → content("") → bgm(news) → speak(news1)
    assert events == [
        ("scene_switch", "kurara_main"),
        ("bgm_switch", "op"),
        ("content_set", "intro", True),
        ("speak", "/tmp/intro_0.wav", None),
        ("content_set", "", False),
        ("bgm_switch", "news"),
        ("speak", "/tmp/news1_0.wav", "Title"),  # news1 は caption 同期
    ]


@pytest.mark.asyncio
async def test_handle_intro_news1_speak_has_caption_synced():
    """handle_intro 内で news1 の speak action は caption_title / caption_summary が
    渡されている（speak worker 内で再生開始時に caption が表示される設計）。
    """
    news_service = MagicMock()
    item = MagicMock()
    item.title = "ニュースタイトル"
    item.content = "ニュース要約本文"
    news_service.peek_current_item.return_value = item
    news_service.has_next.return_value = True

    captured = []

    async def queue_speak(text=None, style=None, speaker_id=None,
                         caption_title=None, caption_summary=None,
                         prepared_wav_path=None, prepared_duration=None):
        captured.append({
            "prepared_wav_path": prepared_wav_path,
            "caption_title": caption_title,
            "caption_summary": caption_summary,
        })
        return {"action_id": "speak-action"}

    ctx = _ctx_with_prepared(news_service=news_service)
    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)
    ctx.saint_graph.body.queue_content_set = AsyncMock(return_value={"action_id": "c"})
    ctx.saint_graph.body.queue_bgm_switch = AsyncMock(return_value={"action_id": "b"})
    ctx.saint_graph.body.queue_scene_switch = AsyncMock(return_value={"action_id": "s"})

    await handle_intro(ctx)

    # intro speak: caption なし
    assert captured[0]["caption_title"] is None
    # news1 speak: caption あり
    assert captured[1]["caption_title"] == "ニュースタイトル"
    assert captured[1]["caption_summary"] == "ニュース要約本文"
    assert captured[1]["prepared_wav_path"] == "/tmp/news1_0.wav"


@pytest.mark.asyncio
async def test_handle_news_uses_prepared_current_news_with_caption():
    """ctx.prepared_current_news が事前に設定されているとき、 handle_news は
    現 news 分は再合成せず prepared wav を queue_speak で投入する
    （最初の sentence に caption 同期）。
    """
    news_service = MagicMock()
    item = MagicMock()
    item.title = "ニュース2タイトル"
    item.content = "ニュース2要約"
    news_service.peek_current_item.return_value = item
    # 現 news を消化したら次が無い（最後の news 扱い）。 _preload_after_current_news の
    # 影響を測定外にし、 「現 news 分の再合成が起きない」のみを検証する。
    news_service.has_next.return_value = False
    news_service.items = [item]

    ctx = _make_ctx(news_service=news_service)
    ctx.prepared_current_news = {
        "item": item,
        "sentences": [
            {"file_path": "/tmp/n2_0.wav", "duration": 4.0, "style": "neutral", "text": "本文1"},
            {"file_path": "/tmp/n2_1.wav", "duration": 3.0, "style": "neutral", "text": "本文2"},
        ],
    }

    captured = []

    async def queue_speak(text=None, style=None, speaker_id=None,
                         caption_title=None, caption_summary=None,
                         prepared_wav_path=None, prepared_duration=None):
        captured.append({
            "prepared_wav_path": prepared_wav_path,
            "caption_title": caption_title,
        })
        return {"action_id": f"speak-{prepared_wav_path}"}

    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)

    await handle_news(ctx)

    # 2 sentence 投入、 最初だけ caption 同期
    assert len(captured) == 2
    assert captured[0]["prepared_wav_path"] == "/tmp/n2_0.wav"
    assert captured[0]["caption_title"] == "ニュース2タイトル"
    assert captured[1]["prepared_wav_path"] == "/tmp/n2_1.wav"
    assert captured[1]["caption_title"] is None
    # 現 news 分の Gemini 再生成は呼ばれない（prepared から直接使う）
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()


@pytest.mark.asyncio
async def test_handle_news_prepares_next_news_during_current_playback():
    """現 news 再生 enqueue 後、 次 news の Gemini 生成 + TTS 合成が裏で走り
    ctx.prepared_next_news に格納される（複数 news 連鎖の lookahead）。
    """
    current_item = MagicMock(title="C", content="c")
    next_item = MagicMock(title="N", content="n")

    news_service = MagicMock()
    news_service.has_next.return_value = True
    # peek は handle_news 入口で current, 進めたあとに next を返す
    news_service.peek_current_item.side_effect = [current_item, next_item, next_item]

    ctx = _make_ctx(news_service=news_service)
    ctx.prepared_current_news = {
        "item": current_item,
        "sentences": [{"file_path": "/tmp/c.wav", "duration": 1.0, "style": "neutral", "text": "c"}],
    }
    # 次 news の Gemini 生成は別 sentences を返す
    ctx.saint_graph.prepare_news_reading_text = AsyncMock(
        return_value=[("neutral", "next-text")]
    )

    await handle_news(ctx)

    # 次 news を Gemini 生成
    ctx.saint_graph.prepare_news_reading_text.assert_awaited_with(title="N", content="n")
    # 合成も走った（少なくとも 1 回、 次 news 分）
    assert ctx.saint_graph.prepare_sentences_synth.await_count >= 1
    # ctx.prepared_next_news に格納
    assert ctx.prepared_next_news is not None
    assert ctx.prepared_next_news["item"] is next_item


@pytest.mark.asyncio
async def test_handle_news_last_item_prepares_news_finished_and_qa_intro():
    """最後の news を読むとき、 再生中に news_finished / qa_intro / qa_first_chitchat
    の合成も裏で走り ctx に保存される（QA への沈黙短縮）。
    """
    last_item = MagicMock(title="L", content="l")

    news_service = MagicMock()
    # 最後の news なので「次」は無い
    news_service.has_next.return_value = False
    news_service.peek_current_item.return_value = last_item
    news_service.items = [last_item]

    ctx = _make_ctx(news_service=news_service)
    ctx.prepared_current_news = {
        "item": last_item,
        "sentences": [{"file_path": "/tmp/l.wav", "duration": 1.0, "style": "neutral", "text": "l"}],
    }

    ctx.saint_graph.prepare_news_finished_text = AsyncMock(
        return_value=[("neutral", "finished")]
    )
    ctx.saint_graph.prepare_qa_intro_text = AsyncMock(
        return_value=[("joyful", "qa-intro")]
    )
    ctx.saint_graph.prepare_qa_chitchat_text = AsyncMock(
        return_value=[("neutral", "qa-first")]
    )

    await handle_news(ctx)

    # news_finished / qa_intro / qa_first の Gemini 生成が呼ばれた
    ctx.saint_graph.prepare_news_finished_text.assert_awaited_once()
    ctx.saint_graph.prepare_qa_intro_text.assert_awaited_once()
    ctx.saint_graph.prepare_qa_chitchat_text.assert_awaited_once()
    # ctx に prepared を保存
    assert ctx.prepared_news_finished is not None
    assert ctx.prepared_qa_intro is not None
    assert ctx.prepared_qa_first is not None


@pytest.mark.asyncio
async def test_handle_waiting_prepares_intro_and_news1_then_transitions_to_intro(monkeypatch):
    """WAITING フェーズで:
    - intro / news1 の Gemini 生成 + TTS 合成を並列実行
    - 60 秒待機（テストではモック化）
    - ctx.prepared_intro / prepared_news1 に合成結果を保存
    - INTRO フェーズへ遷移
    """
    import saint_graph.broadcast_loop as bl_mod

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(bl_mod.asyncio, "sleep", fake_sleep)

    news_service = MagicMock()
    item = MagicMock()
    item.title = "Title"
    item.content = "Content"
    news_service.peek_current_item.return_value = item

    ctx = _make_ctx(news_service=news_service)

    phase = await handle_waiting(ctx)

    assert phase == BroadcastPhase.INTRO
    # intro / news1 の Gemini 生成が呼ばれた
    ctx.saint_graph.prepare_intro_text.assert_awaited_once()
    ctx.saint_graph.prepare_news_reading_text.assert_awaited_once_with(
        title="Title", content="Content"
    )
    # 合成も 2 回（intro と news1）呼ばれた
    assert ctx.saint_graph.prepare_sentences_synth.await_count == 2
    # 60 秒 sleep が呼ばれた
    assert 60 in sleep_calls or 60.0 in sleep_calls
    # 合成結果が ctx に保存された
    assert ctx.prepared_intro is not None
    assert len(ctx.prepared_intro) == 1
    assert ctx.prepared_intro[0]["file_path"] == "/tmp/0.wav"
    assert ctx.prepared_news1 is not None
    assert ctx.prepared_news1[0]["text"] == "本文"


@pytest.mark.asyncio
async def test_handle_waiting_skips_news1_when_no_news_items(monkeypatch):
    """ニュースが 0 件のとき、 news1 の合成は呼ばれない。 intro と 60 秒待機だけ走る。"""
    import saint_graph.broadcast_loop as bl_mod

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(bl_mod.asyncio, "sleep", fake_sleep)

    news_service = MagicMock()
    news_service.peek_current_item.return_value = None

    ctx = _make_ctx(news_service=news_service)

    phase = await handle_waiting(ctx)

    assert phase == BroadcastPhase.INTRO
    ctx.saint_graph.prepare_intro_text.assert_awaited_once()
    ctx.saint_graph.prepare_news_reading_text.assert_not_called()
    # intro のみ合成
    assert ctx.saint_graph.prepare_sentences_synth.await_count == 1
    assert ctx.prepared_intro is not None
    assert ctx.prepared_news1 is None


@pytest.mark.asyncio
async def test_handle_qa_consumes_preloaded_chitchat():
    """preloaded_qa_action_ids があれば handle_qa 冒頭で消化して QA フェーズに残る。"""
    ctx = _make_ctx()
    ctx.preloaded_qa_action_ids = ["id1", "id2"]
    ctx.idle_counter = 5
    ctx.qa_speak_counter = 0

    phase = await handle_qa(ctx)

    assert phase == BroadcastPhase.QA
    assert ctx.preloaded_qa_action_ids is None
    assert ctx.idle_counter == 0
    assert ctx.qa_speak_counter == 1
    ctx.saint_graph.body.wait_for_queue_strict.assert_any_call(action_ids=["id1", "id2"])
    # poll/promote 系統は走らない（preloaded ルートで早期 return）
    ctx.saint_graph.body.get_comments.assert_not_called()
