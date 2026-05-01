import wave
import os

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

    async def update_caption(title, summary):
        events.append(("caption_news", title, summary))
        return True

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

    monkeypatch.setattr(service_module.obs_adapter, "update_news_caption", update_caption)
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
        assert events == [
            ("caption_news", "Title", "Summary"),
            ("scene_switch", "kurara_main"),
            ("bgm_switch", "news"),
            ("bgm_play", "se", True),
            ("bgm_stop", "se"),
            ("filler", str(filler_file), "neutral"),
        ]
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_speak_caption_is_updated_after_generation_before_playback(monkeypatch):
    events = []

    async def generate_and_save(text, style, speaker_id):
        events.append("generate")
        return "/tmp/test.wav", 0.0

    async def update_caption(title, summary):
        events.append(("caption", title, summary))
        return True

    async def play_audio(self, file_path, duration, style):
        events.append("play")
        return True

    async def set_visible_source(emotion):
        events.append(("visible", emotion))
        return "ok"

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "update_news_caption", update_caption)
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
        assert events[:3] == ["generate", ("caption", "Title", "Summary"), "play"]
    finally:
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
async def test_speak_action_fails_when_caption_update_fails(monkeypatch):
    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    async def update_caption(title, summary):
        return False

    async def play_audio(self, file_path, duration, style):
        return True

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "update_news_caption", update_caption)
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

        action_id = result["action_id"]
        assert await svc.wait_for_queue_strict([action_id]) is False
        assert svc._task_status[action_id]["status"] == "failed"
    finally:
        await svc.stop_worker()


@pytest.mark.asyncio
async def test_wait_for_queue_strict_returns_false_for_speak_caption_failure(monkeypatch):
    async def generate_and_save(text, style, speaker_id):
        return "/tmp/test.wav", 0.0

    async def update_caption(title, summary):
        return False

    monkeypatch.setattr(service_module.voice_adapter, "generate_and_save", generate_and_save)
    monkeypatch.setattr(service_module.obs_adapter, "update_news_caption", update_caption)

    svc = StreamerBodyService()
    await svc.start_worker()
    try:
        result = await svc.speak(
            "本文",
            style="neutral",
            caption_title="Title",
            caption_summary="Summary",
        )

        assert await svc.wait_for_queue_strict([result["action_id"]]) is False
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
