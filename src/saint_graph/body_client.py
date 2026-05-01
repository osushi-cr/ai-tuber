"""Body REST API Client for saint_graph.

Provides HTTP client for calling body-cli/body-streamer REST APIs.
"""
import httpx
import logging
from typing import Optional, List, Dict, Any

from .config import BODY_URL

logger = logging.getLogger(__name__)

# Default timeout for HTTP requests
DEFAULT_TIMEOUT = 30.0


class BodyClient:
    """REST API client for body services (CLI/Streamer)."""
    
    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize the body client.
        
        Args:
            base_url: Base URL for the body service. If not provided,
                      uses the BODY_URL from config.
        """
        self.base_url = (base_url or BODY_URL).rstrip("/")
        logger.info(f"BodyClient initialized with base_url: {self.base_url}")

    async def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict[str, Any]]:
        """共通のリクエスト処理。"""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                if method.upper() == "POST":
                    response = await client.post(url, json=payload)
                else:
                    response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except httpx.ConnectError as e:
                logger.error(
                    f"Error calling {path} API: Connection failed to {url} -- "
                    f"cause: {e.__cause__ or e} "
                    f"(Check DNS resolution, firewall rules, and that the body node is running)"
                )
                return None
            except httpx.TimeoutException as e:
                logger.error(
                    f"Error calling {path} API: Request timed out after {timeout}s to {url} -- "
                    f"{type(e).__name__}: {e}"
                )
                return None
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Error calling {path} API: HTTP {e.response.status_code} from {url} -- "
                    f"response body: {e.response.text[:500]}"
                )
                return None
            except Exception as e:
                logger.error(
                    f"Error calling {path} API: Unexpected {type(e).__name__}: {e}",
                    exc_info=True,
                )
                return None
    
    async def _queue_request(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """queue 投入系 endpoint のレスポンスをそのまま返す。"""
        data = await self._request("POST", path, payload)
        if data:
            return data
        return {"status": "error", "result": f"Error: Failed to call {path}"}

    async def queue_speak(
        self,
        text: str,
        style: Optional[str] = None,
        speaker_id: Optional[int] = None,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """発話 action を queue に投入し、action_id を含むレスポンスを返す。"""
        payload = {"text": text}
        if style:
            payload["style"] = style
        if speaker_id is not None:
            payload["speaker_id"] = speaker_id
        if caption_title is not None:
            payload["caption_title"] = caption_title
        if caption_summary is not None:
            payload["caption_summary"] = caption_summary
        return await self._queue_request("/api/speak", payload)

    async def speak(
        self,
        text: str,
        style: Optional[str] = None,
        speaker_id: Optional[int] = None,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
    ) -> str:
        """アバターに発話させます。"""
        data = await self.queue_speak(
            text,
            style=style,
            speaker_id=speaker_id,
            caption_title=caption_title,
            caption_summary=caption_summary,
        )
        return data.get("result", "Speaking completed")
    
    async def change_emotion(self, emotion: str) -> str:
        """アバターの表情を変更します。"""
        data = await self._request("POST", "/api/change_emotion", {"emotion": emotion})
        if data:
            return data.get("result", f"Emotion changed to {emotion}")
        return f"Error: Failed to change emotion to {emotion}"
    
    async def get_comments(self) -> List[Dict[str, Any]]:
        """直近のユーザーコメントを consume（取得＋buffer drain）します。"""
        data = await self._request("POST", "/api/comments/consume", {})
        if data:
            return data.get("comments", [])
        return []
    
    async def start_broadcast(self, config: Optional[Dict[str, Any]] = None) -> str:
        """配信または録画を開始します。"""
        data = await self._request("POST", "/api/broadcast/start", config or {})
        if data:
            return data.get("result", "Broadcast started")
        return "Error: Failed to start broadcast"
    
    async def stop_broadcast(self) -> str:
        """配信または録画を停止します。"""
        data = await self._request("POST", "/api/broadcast/stop")
        if data:
            return data.get("result", "Broadcast stopped")
        return "Error: Failed to stop broadcast"

    async def wait_for_queue(self, timeout: float = 300.0) -> str:
        """キュー内のすべての処理が完了するまで待機します。"""
        data = await self._request("POST", "/api/queue/wait", timeout=timeout)
        if data:
            return data.get("result", "Wait completed")
        return "Error: Failed to wait for queue"

    async def wait_for_queue_strict(
        self,
        action_ids: Optional[List[str]] = None,
        timeout: float = 300.0,
        recent_count: Optional[int] = None,
    ) -> bool:
        """キュー完了後、対象 action が failed なしで完了したか確認します。"""
        payload: Dict[str, Any] = {}
        if action_ids is not None:
            payload["action_ids"] = action_ids
        if recent_count is not None:
            payload["recent_count"] = recent_count
        data = await self._request("POST", "/api/queue/wait_strict", payload, timeout=timeout)
        if data:
            return bool(data.get("result", False))
        return False

    async def queue_bgm_switch(self, bgm_id: str) -> Dict[str, Any]:
        return await self._queue_request("/api/bgm/switch", {"bgm_id": bgm_id})

    async def switch_bgm(self, bgm_id: str) -> str:
        """指定 BGM へ切替（他のループ系 BGM は停止）。"""
        data = await self.queue_bgm_switch(bgm_id)
        return data.get("result", f"BGM switched to {bgm_id}")

    async def queue_bgm_play(self, bgm_id: str, restart: bool = True) -> Dict[str, Any]:
        return await self._queue_request(
            "/api/bgm/play", {"bgm_id": bgm_id, "restart": restart}
        )

    async def play_bgm(self, bgm_id: str, restart: bool = True) -> str:
        """指定 BGM を表示・先頭から再生。SE のような単発再生にも使う。"""
        data = await self.queue_bgm_play(bgm_id, restart=restart)
        return data.get("result", f"BGM {bgm_id} started")

    async def queue_bgm_stop(self, bgm_id: str) -> Dict[str, Any]:
        return await self._queue_request("/api/bgm/stop", {"bgm_id": bgm_id})

    async def stop_bgm(self, bgm_id: str) -> str:
        """指定 BGM ソースを非表示・停止する。"""
        data = await self.queue_bgm_stop(bgm_id)
        return data.get("result", f"BGM {bgm_id} stopped")

    async def play_filler(self, category: str, style: str = "neutral") -> str:
        """category 該当の filler wav をランダムで voice ソースに流す。"""
        data = await self._request(
            "POST", "/api/filler/play", {"category": category, "style": style}
        )
        if data:
            return data.get("result", f"Filler {category} queued")
        return f"Error: Failed to play filler {category}"

    async def register_chitchat_lines(self, lines: List[str]) -> str:
        """雑談セリフリストを body-streamer に登録し、auto-filler に混ぜる。"""
        data = await self._request("POST", "/api/chitchat/register", {"lines": lines})
        if data:
            return data.get("result", f"Registered {len(lines)} chitchat lines")
        return "Error: Failed to register chitchat lines"

    async def queue_scene_switch(self, scene_name: str) -> Dict[str, Any]:
        return await self._queue_request("/api/scene/switch", {"scene": scene_name})

    async def switch_scene(self, scene_name: str) -> str:
        """OBS のプログラムシーンを切り替える。"""
        data = await self.queue_scene_switch(scene_name)
        return data.get("result", f"Scene switched to {scene_name}")

    async def queue_caption_news(self, title: str, summary: str) -> Dict[str, Any]:
        return await self._queue_request(
            "/api/caption/news", {"title": title, "summary": summary}
        )

    async def update_news_caption(self, title: str, summary: str) -> str:
        """OBS のニュースキャプション（タイトル＋要約）を更新する。"""
        data = await self.queue_caption_news(title, summary)
        return data.get("result", "News caption updated")

    async def queue_caption_clear(self) -> Dict[str, Any]:
        return await self._queue_request("/api/caption/clear", {})

    async def clear_news_caption(self) -> str:
        """OBS のニュースキャプションを空にする。"""
        data = await self.queue_caption_clear()
        return data.get("result", "News caption cleared")

    async def health_check(self) -> bool:
        """Body サービスの稼働状態を確認します。"""
        url = f"{self.base_url}/health"
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(url)
                is_ok = response.status_code == 200
                if not is_ok:
                    logger.warning(f"health_check: {url} returned HTTP {response.status_code}")
                return is_ok
            except httpx.ConnectError as e:
                logger.warning(f"health_check: Cannot connect to {url} -- cause: {e.__cause__ or e}")
                return False
            except httpx.TimeoutException:
                logger.warning(f"health_check: Timed out connecting to {url}")
                return False
            except Exception as e:
                logger.warning(f"health_check: Unexpected error for {url}: {type(e).__name__}: {e}")
                return False
