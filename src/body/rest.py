"""
Body Service REST API Base Class.
Ensures consistent REST interface across different body implementations.
"""
import logging
import json
from starlette.responses import JSONResponse
from starlette.requests import Request
from starlette.routing import Route
from .service import BodyServiceBase

logger = logging.getLogger(__name__)

class BodyApp:
    """
    Body サービスの REST API を管理する基底クラス。
    """

    def __init__(self, service: BodyServiceBase):
        self.service = service

    async def health_check(self, request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def speak_api(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            text = body.get("text", "")
            style = body.get("style", "neutral")
            speaker_id = body.get("speaker_id")
            result = await self.service.speak(text, style, speaker_id)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in speak API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def change_emotion_api(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            emotion = body.get("emotion", "neutral")
            result = await self.service.change_emotion(emotion)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in change_emotion API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def get_comments_api(self, request: Request) -> JSONResponse:
        try:
            result = await self.service.get_comments()
            comments = json.loads(result) if result else []
            return JSONResponse({"status": "ok", "comments": comments})
        except Exception as e:
            logger.error(f"Error in get_comments API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def inject_comment_api(self, request: Request) -> JSONResponse:
        """ローカル/テスト用: ダミーコメントを注入する。次の /api/comments 取得で返る。"""
        try:
            body = await request.json()
            author = body.get("author", "guest")
            message = body.get("message", "")
            if not message:
                return JSONResponse({"status": "error", "message": "missing 'message'"}, status_code=400)
            if not hasattr(self.service, "inject_comment"):
                return JSONResponse({"status": "error", "message": "not supported by this body"}, status_code=405)
            result = await self.service.inject_comment(author, message)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in inject_comment API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def start_broadcast_api(self, request: Request) -> JSONResponse:
        try:
            body = await request.json() if request.headers.get("content-type") == "application/json" else {}
            result = await self.service.start_broadcast(body)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in start_broadcast API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def stop_broadcast_api(self, request: Request) -> JSONResponse:
        try:
            result = await self.service.stop_broadcast()
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in stop_broadcast API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def wait_for_queue_api(self, request: Request) -> JSONResponse:
        try:
            result = await self.service.wait_for_queue()
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in wait_for_queue API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def bgm_switch_api(self, request: Request) -> JSONResponse:
        """指定 BGM に切替（他のループ系BGMは停止）。SEは触らない。"""
        try:
            body = await request.json()
            bgm_id = body.get("bgm_id")
            if not bgm_id:
                return JSONResponse({"status": "error", "message": "missing 'bgm_id'"}, status_code=400)
            result = await self.service.switch_bgm(bgm_id)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in bgm/switch API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def bgm_play_api(self, request: Request) -> JSONResponse:
        """指定 BGM を表示・先頭から再生。SEや効果音の単発トリガーにも使う。"""
        try:
            body = await request.json()
            bgm_id = body.get("bgm_id")
            restart = body.get("restart", True)
            if not bgm_id:
                return JSONResponse({"status": "error", "message": "missing 'bgm_id'"}, status_code=400)
            result = await self.service.play_bgm(bgm_id, restart=restart)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in bgm/play API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def bgm_stop_api(self, request: Request) -> JSONResponse:
        """指定 BGM を非表示にして停止。"""
        try:
            body = await request.json()
            bgm_id = body.get("bgm_id")
            if not bgm_id:
                return JSONResponse({"status": "error", "message": "missing 'bgm_id'"}, status_code=400)
            result = await self.service.stop_bgm(bgm_id)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in bgm/stop API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def filler_play_api(self, request: Request) -> JSONResponse:
        """category 指定でフィラー音声を1個ランダム再生する。"""
        try:
            body = await request.json()
            category = body.get("category")
            style = body.get("style", "neutral")
            if not category:
                return JSONResponse({"status": "error", "message": "missing 'category'"}, status_code=400)
            result = await self.service.play_filler(category, style)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in filler/play API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def chitchat_register_api(self, request: Request) -> JSONResponse:
        """雑談セリフのリストを登録し、auto-filler ループに混ぜる。"""
        try:
            body = await request.json()
            lines = body.get("lines", [])
            if not isinstance(lines, list):
                return JSONResponse({"status": "error", "message": "'lines' must be a list"}, status_code=400)
            result = await self.service.register_chitchat_lines(lines)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in chitchat/register API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    def get_routes(self) -> list[Route]:
        """共通のルート定義を返します。"""
        return [
            Route("/health", self.health_check, methods=["GET"]),
            Route("/api/speak", self.speak_api, methods=["POST"]),
            Route("/api/change_emotion", self.change_emotion_api, methods=["POST"]),
            Route("/api/comments", self.get_comments_api, methods=["GET"]),
            Route("/api/comments/inject", self.inject_comment_api, methods=["POST"]),
            Route("/api/broadcast/start", self.start_broadcast_api, methods=["POST"]),
            Route("/api/broadcast/stop", self.stop_broadcast_api, methods=["POST"]),
            Route("/api/queue/wait", self.wait_for_queue_api, methods=["POST"]),
            Route("/api/bgm/switch", self.bgm_switch_api, methods=["POST"]),
            Route("/api/bgm/play", self.bgm_play_api, methods=["POST"]),
            Route("/api/bgm/stop", self.bgm_stop_api, methods=["POST"]),
            Route("/api/filler/play", self.filler_play_api, methods=["POST"]),
            Route("/api/chitchat/register", self.chitchat_register_api, methods=["POST"]),
        ]
