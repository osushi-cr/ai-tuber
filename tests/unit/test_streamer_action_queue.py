import asyncio
import wave
import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("TTS_ENGINE", "miotts")

from body.streamer import service as service_module
from body.streamer.service import StreamerBodyService


def _write_empty_wav(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"")


@pytest.mark.asyncio
async def test_presentation_actions_are_processed_in_queue_order(monkeypatch, tmp_path):
    events = []
    filler_file = tmp_path / "filler_aizuchi_001.wav"
    _write_empty_wav(filler_file)
    monkeypatch.setattr(service_module, "FILLER_VOICE_DIR", tmp_path)

    async def switch_scene(scene):
        events.append(("scene_switch", scene))
        return True

    async def switch_bgm(bgm_id):
        events.append(("bgm_switch", bgm_id))
        return True

    async def play_bgm(bgm_id, restart=True):
        events.append(("bgm_play", bgm_id, restart))
        return True

    async def stop_bgm(bgm_id):
        events.append(("bgm_stop", bgm_id))
        return True

    async def play_audio(self, file_path, duration, style):
        events.append(("filler", file_path, style))
        return True

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.obs_adapter, "switch_scene", switch_scene)
    monkeypatch.setattr(service_module.obs_adapter, "switch_bgm", switch_bgm)
    monkeypatch.setattr(service_module.obs_adapter, "play_bgm", play_bgm)
    monkeypatch.setattr(service_module.obs_adapter, "stop_bgm", stop_bgm)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        results = [
            await svc.update_news_caption("Title", "Summary"),
            await svc.switch_scene("kurara_main"),
            await svc.switch_bgm("news"),
            await svc.play_bgm("se"),
            await svc.stop_bgm("se"),
            await svc.play_filler("aizuchi"),
        ]
        action_ids = [r["action_id"] for r in results]

        assert await svc.wait_for_queue_strict(action_ids) is True
        # caption_news は内部 _caption_state を更新するだけで OBS API を叩かないため
        # events には出ない。 worker キューが順序通り処理されたことは scene→bgm→filler の
        # 並びと、 末尾で _caption_state が反映されていることで確認する。
        assert events == [
            ("scene_switch", "kurara_main"),
            ("bgm_switch", "news"),
            ("bgm_play", "se", True),
            ("bgm_stop", "se"),
            ("filler", str(filler_file), "neutral"),
        ]
        state = svc.get_caption_state()
        assert state["type"] == "news"
        assert state["title"] == "Title"
        assert state["summary"] == "Summary"
        assert state["visible"] is True
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_auto_filler_start_and_stop_are_queue_actions(monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def fake_auto_filler_loop():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    svc = StreamerBodyService()
    svc._broadcasting = True
    monkeypatch.setattr(svc, "_auto_filler_loop", fake_auto_filler_loop)

    await svc.start_worker()
    try:
        start = await svc.start_auto_filler()
        assert await svc.wait_for_queue_strict([start["action_id"]]) is True
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert svc._auto_filler_task is not None

        stop = await svc.stop_auto_filler()
        assert await svc.wait_for_queue_strict([stop["action_id"]]) is True
        await asyncio.wait_for(stopped.wait(), timeout=1.0)
        assert svc._auto_filler_task is None
    finally:
        svc._broadcasting = False
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_speak_caption_is_updated_after_generation_before_playback(monkeypatch):
    """音声生成完了 → caption 内部 state 更新 → 再生 の順番（D6 caption-speak 同期）。"""
    events = []

    async def generate_and_save(text, style, speaker_id):
        events.append("generate")
        return "/tmp/test.wav", 0.0

    async def play_audio(self, file_path, duration, style):
        events.append(("play", svc.get_caption_state()["title"]))
        return True

    async def set_visible_source(emotion):
        events.append(("visible", emotion))
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        result = await svc.speak(
            "本文",
            style="neutral",
            caption_title="Title",
            caption_summary="Summary",
        )

        assert await svc.wait_for_queue_strict([result["action_id"]]) is True
        # generate → play の順、 play 時点で caption state が "Title" に更新済
        assert events[0] == "generate"
        assert events[1] == ("play", "Title")
        state = svc.get_caption_state()
        assert state["type"] == "news"
        assert state["title"] == "Title"
        assert state["summary"] == "Summary"
        assert state["visible"] is True
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_speak_does_not_switch_scene_or_start_auto_filler(monkeypatch):
    events = []

    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    async def switch_scene(scene):
        events.append(("scene_switch", scene))
        return True

    async def play_audio(self, file_path, duration, style):
        events.append("play")
        return True

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "switch_scene", switch_scene)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    svc._broadcasting = True
    await svc.start_worker()
    try:
        result = await svc.speak("本文", style="neutral")

        assert await svc.wait_for_queue_strict([result["action_id"]]) is True
        assert events == ["play"]
        assert svc._auto_filler_task is None
        assert not hasattr(svc, "_first_speech_done")
    finally:
        svc._broadcasting = False
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_wait_for_queue_strict_detects_failed_action(monkeypatch):
    async def switch_scene(scene):
        return False

    monkeypatch.setattr(service_module.obs_adapter, "switch_scene", switch_scene)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        result = await svc.switch_scene("missing_scene")

        assert await svc.wait_for_queue_strict([result["action_id"]]) is False
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_wait_for_queue_strict_returns_when_target_completes_even_while_other_actions_keep_queueing(
    monkeypatch, tmp_path
):
    """回帰テスト: auto_filler が並走して queue に action を投入し続けている状況でも、
    wait_for_queue_strict(action_ids=...) は指定 action だけが completed になれば即 return する。

    バグ再現: 修正前の実装は冒頭で `_action_queue.join()` を呼ぶため、 auto_filler が
    queue に常時投入していると queue は空にならず永久ハング。 結果 saint_graph 側の
    process_turn が完了せず handle_intro 末尾の kurara_main 切替に到達しない。
    """
    filler_file = tmp_path / "filler_aizuchi_001.wav"
    _write_empty_wav(filler_file)
    monkeypatch.setattr(service_module, "FILLER_VOICE_DIR", tmp_path)

    async def play_audio(self, file_path, duration, style):
        await asyncio.sleep(0.01)
        return True

    async def update_caption(title, summary):
        return True

    monkeypatch.setattr(service_module.obs_adapter, "update_news_caption", update_caption)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    await svc.start_worker()

    keep_filling = True

    async def background_filler():
        # queue に絶えず filler を入れ続けて _action_queue.join() を解かせない
        while keep_filling:
            await svc.play_filler("aizuchi")
            await asyncio.sleep(0.02)

    bg_task = asyncio.create_task(background_filler())
    try:
        # 監視対象の caption action を投入
        result = await svc.update_news_caption("Title", "Summary")
        target_id = result["action_id"]

        # 修正前: queue が空にならず asyncio.wait_for が timeout
        # 修正後: target_id が completed になり次第 True を返す
        ok = await asyncio.wait_for(
            svc.wait_for_queue_strict([target_id]),
            timeout=3.0,
        )
        assert ok is True
    finally:
        keep_filling = False
        bg_task.cancel()
        try:
            await bg_task
        except (asyncio.CancelledError, Exception):
            pass
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_start_broadcast_does_not_clear_caption_or_start_auto_filler(monkeypatch):
    async def start_obs_recording(self):
        return "OBS録画を開始しました。"

    async def clear_news_caption():
        raise AssertionError("start_broadcast must not clear captions")

    monkeypatch.setenv("STREAMING_MODE", "false")
    monkeypatch.setattr(StreamerBodyService, "start_obs_recording", start_obs_recording)
    monkeypatch.setattr(service_module.asyncio, "sleep", AsyncMock())

    svc = StreamerBodyService()
    monkeypatch.setattr(svc, "clear_news_caption", clear_news_caption)

    result = await svc.start_broadcast()

    assert result == "OBS録画を開始しました。"
    assert svc._broadcasting is True
    assert svc._auto_filler_task is None


@pytest.mark.asyncio
async def test_speak_caption_state_updated_before_playback(monkeypatch):
    """caption は OBS API 失敗の概念が無くなった（HTML overlay の in-memory state）。
    代わりに「再生開始時点で caption state が caption_title/summary に反映済」を保証する。"""
    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    captured = {}

    async def play_audio(self, file_path, duration, style):
        captured["state"] = svc.get_caption_state()
        return True

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        result = await svc.speak(
            "本文",
            style="neutral",
            caption_title="Title",
            caption_summary="Summary",
        )

        assert await svc.wait_for_queue_strict([result["action_id"]]) is True
        assert captured["state"]["type"] == "news"
        assert captured["state"]["title"] == "Title"
        assert captured["state"]["summary"] == "Summary"
        assert captured["state"]["visible"] is True
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_speak_action_fails_when_obs_audio_playback_returns_false(monkeypatch):
    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    async def play_media_with_emotion(source_name, file_path, emotion):
        return False

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "play_media_with_emotion", play_media_with_emotion)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        result = await svc.speak("本文", style="neutral")
        action_id = result["action_id"]

        assert await svc.wait_for_queue_strict([action_id]) is False
        assert svc._task_status[action_id]["status"] == "failed"
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_wait_for_queue_strict_detects_second_speak_failure(monkeypatch):
    play_results = [True, False]

    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    async def play_audio(self, file_path, duration, style):
        return play_results.pop(0)

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        first = await svc.speak("一文目", style="neutral")
        second = await svc.speak("二文目", style="joyful")
        action_ids = [first["action_id"], second["action_id"]]

        assert await svc.wait_for_queue_strict(action_ids) is False
        assert svc._task_status[action_ids[0]]["status"] == "completed"
        assert svc._task_status[action_ids[1]]["status"] == "failed"
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_prepare_speak_returns_wav_path_without_queue(monkeypatch):
    """`prepare_speak` は voice_adapter.generate_and_save を queue 外で即時実行し、
    file_path と duration を返す。 action queue には何も積まれない。
    """
    calls = []

    async def generate_and_save(text, style, speaker_id):
        calls.append((text, style, speaker_id))
        return "/tmp/prepared.wav", 12.34

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)

    svc = StreamerBodyService()
    result = await svc.prepare_speak("こんにちは", style="joyful", speaker_id=None)

    assert result == {"file_path": "/tmp/prepared.wav", "duration": 12.34}
    assert calls == [("こんにちは", "joyful", None)]
    # queue 外で実行されるため worker queue は空
    assert svc._action_queue.qsize() == 0


@pytest.mark.asyncio
async def test_speak_with_prepared_wav_skips_synthesis(monkeypatch, tmp_path):
    """speak action に prepared_wav_path / prepared_duration 指定時、
    _handle_speak_action は voice_adapter.generate_and_save を呼ばず、
    prepared_wav_path を直接再生する（waiting 中の先行合成結果を再生する経路）。
    """
    generated = []
    played = []

    async def generate_and_save(text, style, speaker_id):
        generated.append(text)
        return "/tmp/never_called.wav", 0.0

    async def play_audio(self, file_path, duration, style):
        played.append((file_path, duration, style))
        return True

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    prepared_wav = tmp_path / "prepared.wav"
    _write_empty_wav(prepared_wav)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        result = await svc.speak(
            "本文",
            style="neutral",
            prepared_wav_path=str(prepared_wav),
            prepared_duration=5.0,
        )

        assert await svc.wait_for_queue_strict([result["action_id"]]) is True
        # 合成は呼ばれない
        assert generated == []
        # 事前合成済 wav を直接再生
        assert played == [(str(prepared_wav), 5.0, "neutral")]
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_set_content_updates_state_and_visibility():
    """set_content() は queue に content_set task を積み、 worker が _content_state を
    更新する。 image 空のときは visible が強制 False。
    """
    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        initial = svc.get_content_state()
        assert initial == {"image": "", "visible": False, "updated_at": 0.0}

        r1 = await svc.set_content(image="intro")
        assert await svc.wait_for_queue_strict([r1["action_id"]]) is True
        state = svc.get_content_state()
        assert state["image"] == "intro"
        assert state["visible"] is True
        assert state["updated_at"] > 0.0

        r2 = await svc.set_content(image="qa")
        assert await svc.wait_for_queue_strict([r2["action_id"]]) is True
        assert svc.get_content_state()["image"] == "qa"

        # visible=False を渡すと image があっても visible False
        r3 = await svc.set_content(image="end", visible=False)
        assert await svc.wait_for_queue_strict([r3["action_id"]]) is True
        assert svc.get_content_state()["visible"] is False

        # image="" は visible 強制 False（クリア用途）
        r4 = await svc.set_content(image="", visible=True)
        assert await svc.wait_for_queue_strict([r4["action_id"]]) is True
        final = svc.get_content_state()
        assert final["image"] == ""
        assert final["visible"] is False
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_content_set_action_processed_in_queue_order(monkeypatch, tmp_path):
    """content_set が scene_switch / speak / bgm_switch と同じ worker queue で
    順序保証されること。 不具合③（intro 画像が news1 開始時に残る）の修正検証。
    """
    events = []
    filler_file = tmp_path / "filler_aizuchi_001.wav"
    _write_empty_wav(filler_file)
    monkeypatch.setattr(service_module, "FILLER_VOICE_DIR", tmp_path)

    async def switch_scene(scene):
        events.append(("scene_switch", scene))
        return True

    async def switch_bgm(bgm_id):
        events.append(("bgm_switch", bgm_id))
        return True

    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    async def play_audio(self, file_path, duration, style):
        events.append(("speak_played", svc.get_content_state()["image"]))
        return True

    async def set_visible_source(emotion):
        return "ok"

    monkeypatch.setattr(service_module.obs_adapter, "switch_scene", switch_scene)
    monkeypatch.setattr(service_module.obs_adapter, "switch_bgm", switch_bgm)
    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "set_visible_source", set_visible_source)
    monkeypatch.setattr(StreamerBodyService, "play_audio_with_sync_emotion", play_audio)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        # intro 画像表示 → intro 発話 → intro 画像を畳む → BGM 切替 → news1 発話
        # の順で enqueue。 視聴者目線では「news1 発話開始時点で intro 画像が消えてる」必要。
        results = [
            await svc.set_content(image="intro", visible=True),
            await svc.speak("こんにちは", style="joyful"),
            await svc.set_content(image="", visible=False),
            await svc.switch_bgm("news"),
            await svc.speak("ニュース本文", style="neutral"),
        ]
        action_ids = [r["action_id"] for r in results]

        assert await svc.wait_for_queue_strict(action_ids) is True
        # intro speak 時点で content_image="intro"、news1 speak 時点で content_image=""
        assert events == [
            ("speak_played", "intro"),
            ("bgm_switch", "news"),
            ("speak_played", ""),
        ]
    finally:
        await svc.stop_worker()
