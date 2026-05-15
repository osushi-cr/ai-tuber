"""saint_graph.body_client.BodyClient の HTTP ラッパーのユニットテスト。

`_request` / `_queue_request` は httpx 経由で body REST を叩くだけなので、
ここでは「BodyClient の各 public メソッドが正しい path / payload で内部の
`_request` 系を呼ぶか」を AsyncMock で検証する。
"""
import pytest
from unittest.mock import AsyncMock

from saint_graph.body_client import BodyClient


@pytest.mark.asyncio
async def test_prepare_speak_calls_speak_prepare_api(monkeypatch):
    """body_client.prepare_speak() が /api/speak/prepare に text/style/speaker_id を
    POST し、 body 側の {file_path, duration} レスポンスをそのまま dict で返す。
    """
    captured = {}

    async def fake_request(self, method, path, payload=None, timeout=30.0):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {"file_path": "/tmp/prepared.wav", "duration": 7.5}

    monkeypatch.setattr(BodyClient, "_request", fake_request)

    client = BodyClient(base_url="http://test")
    result = await client.prepare_speak("こんにちは", style="joyful", speaker_id=None)

    assert result == {"file_path": "/tmp/prepared.wav", "duration": 7.5}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/speak/prepare"
    assert captured["payload"] == {
        "text": "こんにちは",
        "style": "joyful",
        "speaker_id": None,
    }


@pytest.mark.asyncio
async def test_queue_content_set_calls_content_set_api(monkeypatch):
    """body_client.queue_content_set() が /api/content/set に image/visible を POST し、
    queue 系レスポンス（{status, result, action_id}）をそのまま dict で返す。
    """
    captured = {}

    async def fake_queue_request(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {
            "status": "ok",
            "result": "content set to 'intro' (visible=True) queued",
            "action_id": "test-action-id",
        }

    monkeypatch.setattr(BodyClient, "_queue_request", fake_queue_request)

    client = BodyClient(base_url="http://test")
    result = await client.queue_content_set(image="intro", visible=True)

    assert result["action_id"] == "test-action-id"
    assert captured["path"] == "/api/content/set"
    assert captured["payload"] == {"image": "intro", "visible": True}


@pytest.mark.asyncio
async def test_update_content_image_returns_result_message(monkeypatch):
    """body_client.update_content_image() は queue_content_set のラッパーで、 result
    メッセージだけを文字列で返す（既存 queue 系 API と同じ命名規則）。
    """

    async def fake_queue_request(self, path, payload):
        return {
            "status": "ok",
            "result": "content set to 'qa' (visible=True) queued",
            "action_id": "abc",
        }

    monkeypatch.setattr(BodyClient, "_queue_request", fake_queue_request)

    client = BodyClient(base_url="http://test")
    msg = await client.update_content_image(image="qa", visible=True)

    assert msg == "content set to 'qa' (visible=True) queued"
