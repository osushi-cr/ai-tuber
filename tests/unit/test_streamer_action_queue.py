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
async def test_set_content_updates_state_and_visibility():
    """set_content() で _content_state が更新され、 image 空のときは visible が強制 False。"""
    svc = StreamerBodyService()

    initial = svc.get_content_state()
    assert initial == {"image": "", "visible": False, "updated_at": 0.0}

    await svc.set_content(image="intro")
    state = svc.get_content_state()
    assert state["image"] == "intro"
    assert state["visible"] is True
    assert state["updated_at"] > 0.0

    await svc.set_content(image="qa")
    assert svc.get_content_state()["image"] == "qa"

    # visible=False を渡すと image があっても visible False
    await svc.set_content(image="end", visible=False)
    assert svc.get_content_state()["visible"] is False

    # image="" は visible 強制 False（クリア用途）
    await svc.set_content(image="", visible=True)
    assert svc.get_content_state() == {
        "image": "",
        "visible": False,
        "updated_at": svc.get_content_state()["updated_at"],
    }
