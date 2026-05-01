import pytest
from body.cli.main import app
from body.cli.service import body_service
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient
import json

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_speak_api():
    with patch.object(body_service, "speak", new_callable=AsyncMock) as mock_speak:
        mock_speak.return_value = {"message": "Speaking completed", "action_id": "speak-1"}
        response = client.post("/api/speak", json={"text": "Hello Test", "style": "happy"})
        
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["result"] == "Speaking completed"
        assert response.json()["action_id"] == "speak-1"
        mock_speak.assert_called_once_with(
            "Hello Test",
            "happy",
            None,
            caption_title=None,
            caption_summary=None,
        )

def test_change_emotion_api():
    with patch.object(body_service, "change_emotion", new_callable=AsyncMock) as mock_change:
        mock_change.return_value = "Emotion changed to angry"
        response = client.post("/api/change_emotion", json={"emotion": "angry"})
        
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["result"] == "Emotion changed to angry"
        mock_change.assert_called_once_with("angry")

def test_peek_comments_api():
    with patch.object(body_service, "peek_comments", new_callable=AsyncMock) as mock_peek:
        mock_peek.return_value = json.dumps([
            {"author": "User", "message": "Test comment 1"},
            {"author": "User", "message": "Test comment 2"}
        ])
        response = client.get("/api/comments")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        comments = response.json()["comments"]
        assert len(comments) == 2
        assert comments[0]["author"] == "User"
        assert comments[0]["message"] == "Test comment 1"
        mock_peek.assert_called_once()

def test_peek_comments_empty_api():
    with patch.object(body_service, "peek_comments", new_callable=AsyncMock) as mock_peek:
        mock_peek.return_value = json.dumps([])
        response = client.get("/api/comments")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["comments"] == []
        mock_peek.assert_called_once()

def test_consume_comments_api():
    with patch.object(body_service, "consume_comments", new_callable=AsyncMock) as mock_consume:
        mock_consume.return_value = json.dumps([
            {"author": "User", "message": "Drained comment"}
        ])
        response = client.post("/api/comments/consume")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        comments = response.json()["comments"]
        assert len(comments) == 1
        assert comments[0]["message"] == "Drained comment"
        mock_consume.assert_called_once()

def test_start_broadcast_api():
    with patch.object(body_service, "start_broadcast", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = "CLI mode: broadcast start skipped"
        response = client.post("/api/broadcast/start", json={})
        
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "CLI mode" in response.json()["result"]

def test_stop_broadcast_api():
    with patch.object(body_service, "stop_broadcast", new_callable=AsyncMock) as mock_stop:
        mock_stop.return_value = "CLI mode: broadcast stop skipped"
        response = client.post("/api/broadcast/stop")
        
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "CLI mode" in response.json()["result"]
