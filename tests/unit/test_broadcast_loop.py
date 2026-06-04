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
async def test_handle_closing_stops_auto_filler_at_entry(monkeypatch):
    """CLOSING 突入時に auto_filler_stop を queue 投入する（chitchat 割り込み防止）。"""
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    ctx = _make_ctx()

    with patch("asyncio.sleep", return_value=None):
        await handle_closing(ctx)

    ctx.saint_graph.body.queue_auto_filler_stop.assert_called()


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
async def test_handle_closing_queue_order(monkeypatch, tmp_path):
    """handle_closing の enqueue 順序:
    closing_wav → content_set(qa, false) → bgm(ed) → scene(ending) → content_set(end, true)
    の順で worker queue に積む（視聴者目線で順序保証）。
    最後に ending_duration 秒 sleep する。
    """
    monkeypatch.setenv("CLOSING_POOL_DIR", str(tmp_path))
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "0")
    # closing pool に 1 件
    closing_wav = tmp_path / "closing_test.wav"
    closing_wav.write_bytes(b"")  # 中身は無視（test では再生しない）

    ctx = _make_ctx()

    events = []

    async def queue_filler(file_path=None, style=None, **kwargs):
        events.append(("filler", style))
        return {"action_id": "filler-a"}

    async def queue_content_set(image, visible):
        events.append(("content_set", image, visible))
        return {"action_id": f"c-{image}-{visible}"}

    async def queue_bgm_switch(bgm_id):
        events.append(("bgm_switch", bgm_id))
        return {"action_id": f"bgm-{bgm_id}"}

    async def queue_scene_switch(scene):
        events.append(("scene_switch", scene))
        return {"action_id": f"scene-{scene}"}

    async def queue_auto_filler_stop():
        events.append(("auto_filler_stop",))
        return {"action_id": "afs"}

    ctx.saint_graph.body.queue_filler = AsyncMock(side_effect=queue_filler)
    ctx.saint_graph.body.queue_content_set = AsyncMock(side_effect=queue_content_set)
    ctx.saint_graph.body.queue_bgm_switch = AsyncMock(side_effect=queue_bgm_switch)
    ctx.saint_graph.body.queue_scene_switch = AsyncMock(side_effect=queue_scene_switch)
    ctx.saint_graph.body.queue_auto_filler_stop = AsyncMock(side_effect=queue_auto_filler_stop)

    result = await handle_closing(ctx)

    assert result is None
    # auto_filler 停止 → closing wav → qa 画像非表示 → ed BGM → ending scene → end 画像表示
    assert events == [
        ("auto_filler_stop",),
        ("filler", "joyful"),
        ("content_set", "", False),
        ("bgm_switch", "ed"),
        ("scene_switch", "ending"),
        ("content_set", "end", True),
    ]


@pytest.mark.asyncio
async def test_handle_closing_holds_for_ending_duration(monkeypatch, tmp_path):
    """ending_duration > 0 なら asyncio.sleep(ending_duration) で待機する。"""
    monkeypatch.setenv("CLOSING_POOL_DIR", str(tmp_path))
    monkeypatch.setenv("BROADCAST_ENDING_DURATION", "30")

    sleep_calls = []

    import saint_graph.broadcast_loop as bl_mod

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(bl_mod.asyncio, "sleep", fake_sleep)

    ctx = _make_ctx()
    ctx.saint_graph.body.queue_filler = AsyncMock(return_value={"action_id": "f"})
    ctx.saint_graph.body.queue_content_set = AsyncMock(return_value={"action_id": "c"})
    ctx.saint_graph.body.queue_bgm_switch = AsyncMock(return_value={"action_id": "b"})
    ctx.saint_graph.body.queue_scene_switch = AsyncMock(return_value={"action_id": "s"})

    await handle_closing(ctx)

    assert 30.0 in sleep_calls or 30 in sleep_calls


@pytest.mark.asyncio
async def test_handle_qa_plays_prepared_news_finished_qa_intro_qa_first_on_first_entry():
    """QA 初回エントリで、 ctx の prepared_news_finished / prepared_qa_intro /
    prepared_qa_first が順に queue_speak で投入され、 ctx 側はクリアされる。
    content_image(qa, True) も queue される。
    """
    ctx = _make_ctx()
    ctx.prepared_news_finished = [
        {"file_path": "/tmp/finished.wav", "duration": 2.0, "style": "neutral", "text": "おしまい"}
    ]
    ctx.prepared_qa_intro = [
        {"file_path": "/tmp/qa_intro.wav", "duration": 2.0, "style": "joyful", "text": "コメントどうぞ"}
    ]
    ctx.prepared_qa_first = [
        {"file_path": "/tmp/qa_first.wav", "duration": 2.0, "style": "neutral", "text": "雑談1"}
    ]

    events = []

    async def queue_content_set(image, visible):
        events.append(("content_set", image, visible))
        return {"action_id": f"c-{image}"}

    async def queue_speak(text=None, style=None, speaker_id=None,
                         caption_title=None, caption_summary=None,
                         prepared_wav_path=None, prepared_duration=None):
        events.append(("speak", prepared_wav_path))
        return {"action_id": f"s-{prepared_wav_path}"}

    ctx.saint_graph.body.queue_content_set = AsyncMock(side_effect=queue_content_set)
    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)

    await handle_qa(ctx)

    # content_set(qa, True) → finished → qa_intro → qa_first の順
    assert events == [
        ("content_set", "qa", True),
        ("speak", "/tmp/finished.wav"),
        ("speak", "/tmp/qa_intro.wav"),
        ("speak", "/tmp/qa_first.wav"),
    ]
    # 消化後は None
    assert ctx.prepared_news_finished is None
    assert ctx.prepared_qa_intro is None
    assert ctx.prepared_qa_first is None


@pytest.mark.asyncio
async def test_handle_qa_waits_for_entry_speeches_before_returning():
    """QA 初回エントリは、定型発話（finished/intro/first）の再生完了を
    wait_for_queue_strict で待ってから return する。これを待たずに返すと、
    次サイクルのコメント反応キャプション（type=comment）が定型発話の合間に
    再生され「キャプション見失い」になる（YOS-52）。
    """
    ctx = _make_ctx()
    ctx.prepared_news_finished = [
        {"file_path": "/tmp/finished.wav", "duration": 2.0, "style": "neutral", "text": "おしまい"}
    ]
    ctx.prepared_qa_intro = [
        {"file_path": "/tmp/qa_intro.wav", "duration": 2.0, "style": "joyful", "text": "コメントどうぞ"}
    ]
    ctx.prepared_qa_first = [
        {"file_path": "/tmp/qa_first.wav", "duration": 2.0, "style": "neutral", "text": "雑談1"}
    ]

    async def queue_speak(text=None, style=None, speaker_id=None,
                          caption_title=None, caption_summary=None,
                          prepared_wav_path=None, prepared_duration=None):
        return {"action_id": f"s-{prepared_wav_path}"}

    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)
    ctx.saint_graph.body.wait_for_queue_strict = AsyncMock(return_value=True)

    await handle_qa(ctx)

    # 定型発話3件の action_id すべてを strict 待ちしてから return する
    ctx.saint_graph.body.wait_for_queue_strict.assert_awaited_once_with(
        action_ids=["s-/tmp/finished.wav", "s-/tmp/qa_intro.wav", "s-/tmp/qa_first.wav"]
    )


@pytest.mark.asyncio
async def test_handle_qa_second_entry_does_not_wait_for_entry_speeches():
    """2 回目以降の entry（phase_scene_initialized 済）は定型発話を積まないので、
    entry 用の wait_for_queue_strict は呼ばれない。"""
    from saint_graph.broadcast_loop import BroadcastPhase
    ctx = _make_ctx(comments=[])
    ctx.phase_scene_initialized.add(BroadcastPhase.QA)
    ctx.saint_graph.body.wait_for_queue_strict = AsyncMock(return_value=True)

    await handle_qa(ctx)

    ctx.saint_graph.body.wait_for_queue_strict.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_qa_first_entry_clears_news_caption_before_news_finished_speak():
    """QA 初回 entry で content_set(qa, True) の直後、 news_finished speak の前に
    queue_caption_clear が呼ばれる（最後の news caption が news_finished 再生中に
    残らないようにする）。
    """
    ctx = _make_ctx()
    ctx.prepared_news_finished = [
        {"file_path": "/tmp/f.wav", "duration": 1.0, "style": "neutral", "text": "おしまい"}
    ]

    events = []

    async def queue_content_set(image, visible):
        events.append(("content_set", image, visible))
        return {"action_id": "c"}

    async def queue_caption_clear():
        events.append(("caption_clear",))
        return {"action_id": "cc"}

    async def queue_speak(**kwargs):
        events.append(("speak", kwargs.get("prepared_wav_path")))
        return {"action_id": "s"}

    ctx.saint_graph.body.queue_content_set = AsyncMock(side_effect=queue_content_set)
    ctx.saint_graph.body.queue_caption_clear = AsyncMock(side_effect=queue_caption_clear)
    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)

    await handle_qa(ctx)

    # content_set(qa, True) → caption_clear → news_finished speak の順
    assert events[:3] == [
        ("content_set", "qa", True),
        ("caption_clear",),
        ("speak", "/tmp/f.wav"),
    ]


@pytest.mark.asyncio
async def test_handle_qa_skips_prepared_on_second_entry():
    """QA 2 回目以降の entry では prepared は既に None なので queue_speak されない。
    content_set(qa, True) も再投入されない（初回のみ）。
    """
    ctx = _make_ctx()
    # 初回処理済を模擬: phase_scene_initialized に QA 追加 + prepared は None
    ctx.phase_scene_initialized.add(BroadcastPhase.QA)
    ctx.prepared_news_finished = None
    ctx.prepared_qa_intro = None
    ctx.prepared_qa_first = None

    events = []

    async def queue_content_set(image, visible):
        events.append(("content_set", image, visible))
        return {"action_id": "c"}

    async def queue_speak(**kwargs):
        events.append(("speak", kwargs.get("prepared_wav_path")))
        return {"action_id": "s"}

    ctx.saint_graph.body.queue_content_set = AsyncMock(side_effect=queue_content_set)
    ctx.saint_graph.body.queue_speak = AsyncMock(side_effect=queue_speak)

    await handle_qa(ctx)

    # 初期化系は走らない
    assert ("content_set", "qa", True) not in events
    # prepared が無いので speak も 0 件（コメント反応合成は別ループ）
    speak_events = [e for e in events if e[0] == "speak"]
    assert speak_events == []


@pytest.mark.asyncio
async def test_handle_qa_silence_timeout_transitions_to_closing():
    """idle_counter が MAX_WAIT_CYCLES を超えると CLOSING へ遷移する。"""
    ctx = _make_ctx()
    ctx.phase_scene_initialized.add(BroadcastPhase.QA)
    ctx.idle_counter = MAX_WAIT_CYCLES

    phase = await handle_qa(ctx)

    assert phase == BroadcastPhase.CLOSING


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


