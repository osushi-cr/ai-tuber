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

    def _ok_result(self, result) -> JSONResponse:
        """queue 投入系の action_id を REST レスポンスへ展開する。"""
        if isinstance(result, dict):
            payload = {
                "status": "ok",
                "result": result.get("message", result),
            }
            if "action_id" in result:
                payload["action_id"] = result["action_id"]
            return JSONResponse(payload)
        return JSONResponse({"status": "ok", "result": result})

    async def health_check(self, request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def speak_api(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            text = body.get("text", "")
            style = body.get("style", "neutral")
            speaker_id = body.get("speaker_id")
            caption_title = body.get("caption_title")
            caption_summary = body.get("caption_summary")
            result = await self.service.speak(
                text,
                style,
                speaker_id,
                caption_title=caption_title,
                caption_summary=caption_summary,
            )
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in speak API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def change_emotion_api(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            emotion = body.get("emotion", "neutral")
            result = await self.service.change_emotion(emotion)
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in change_emotion API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def peek_comments_api(self, request: Request) -> JSONResponse:
        """OBS overlay 表示用: コメントを buffer から peek（破壊しない）。"""
        try:
            result = await self.service.peek_comments()
            comments = json.loads(result) if result else []
            return JSONResponse({"status": "ok", "comments": comments})
        except Exception as e:
            logger.error(f"Error in peek_comments API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def consume_comments_api(self, request: Request) -> JSONResponse:
        """saint_graph リアクション用: コメントを buffer から consume（drain）。"""
        try:
            result = await self.service.consume_comments()
            comments = json.loads(result) if result else []
            return JSONResponse({"status": "ok", "comments": comments})
        except Exception as e:
            logger.error(f"Error in consume_comments API: {e}")
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

    async def wait_for_queue_strict_api(self, request: Request) -> JSONResponse:
        try:
            body = await request.json() if request.headers.get("content-type") == "application/json" else {}
            action_ids = body.get("action_ids")
            recent_count = body.get("recent_count")
            if action_ids is not None and not isinstance(action_ids, list):
                return JSONResponse({"status": "error", "message": "'action_ids' must be a list"}, status_code=400)
            result = await self.service.wait_for_queue_strict(action_ids, recent_count=recent_count)
            return JSONResponse({"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"Error in wait_for_queue_strict API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def bgm_switch_api(self, request: Request) -> JSONResponse:
        """指定 BGM に切替（他のループ系BGMは停止）。SEは触らない。"""
        try:
            body = await request.json()
            bgm_id = body.get("bgm_id")
            if not bgm_id:
                return JSONResponse({"status": "error", "message": "missing 'bgm_id'"}, status_code=400)
            result = await self.service.switch_bgm(bgm_id)
            return self._ok_result(result)
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
            return self._ok_result(result)
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
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in bgm/stop API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def filler_play_api(self, request: Request) -> JSONResponse:
        """category または file_path 指定でフィラー音声を再生する。"""
        try:
            body = await request.json()
            category = body.get("category")
            file_path = body.get("file_path")
            style = body.get("style", "neutral")
            if not category and not file_path:
                return JSONResponse(
                    {"status": "error", "message": "missing 'category' or 'file_path'"},
                    status_code=400,
                )
            result = await self.service.play_filler(
                category=category, style=style, file_path=file_path
            )
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in filler/play API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def auto_filler_start_api(self, request: Request) -> JSONResponse:
        """auto-filler ループ開始を presentation queue に投入する。"""
        try:
            result = await self.service.start_auto_filler()
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in auto_filler/start API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def auto_filler_stop_api(self, request: Request) -> JSONResponse:
        """auto-filler ループ停止を presentation queue に投入する。"""
        try:
            result = await self.service.stop_auto_filler()
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in auto_filler/stop API: {e}")
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

    async def scene_switch_api(self, request: Request) -> JSONResponse:
        """OBS のプログラムシーンを切り替える（waiting / kurara_main / ending 等）。"""
        try:
            body = await request.json()
            scene_name = body.get("scene")
            if not scene_name:
                return JSONResponse({"status": "error", "message": "missing 'scene'"}, status_code=400)
            result = await self.service.switch_scene(scene_name)
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in scene/switch API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def caption_news_api(self, request: Request) -> JSONResponse:
        """OBS のニュースキャプション（タイトル＋要約）を更新する。"""
        try:
            body = await request.json()
            title = body.get("title", "")
            summary = body.get("summary", "")
            result = await self.service.update_news_caption(title, summary)
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in caption/news API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def caption_clear_api(self, request: Request) -> JSONResponse:
        """OBS のニュースキャプションを空にする。"""
        try:
            result = await self.service.clear_news_caption()
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in caption/clear API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def caption_state_api(self, request: Request) -> JSONResponse:
        """現在の caption 状態を返す。 OBS ブラウザソースから 1 秒間隔で fetch される。"""
        state = self.service.get_caption_state()
        # OBS ブラウザソース（file:// or http:// 起点）からのアクセス用に CORS を許す
        return JSONResponse(
            state,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store",
            },
        )

    async def caption_set_api(self, request: Request) -> JSONResponse:
        """caption 状態を任意の type で更新する（intro / news / comment / closing 等）。"""
        try:
            body = await request.json()
            result = await self.service.set_caption(
                type=body.get("type", ""),
                title=body.get("title", ""),
                summary=body.get("summary", ""),
                visible=body.get("visible", True),
            )
            return self._ok_result(result)
        except Exception as e:
            logger.error(f"Error in caption/set API: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    def get_routes(self) -> list[Route]:
        """共通のルート定義を返します。"""
        return [
            Route("/health", self.health_check, methods=["GET"]),
            Route("/api/speak", self.speak_api, methods=["POST"]),
            Route("/api/change_emotion", self.change_emotion_api, methods=["POST"]),
            Route("/api/comments", self.peek_comments_api, methods=["GET"]),
            Route("/api/comments/consume", self.consume_comments_api, methods=["POST"]),
            Route("/api/comments/inject", self.inject_comment_api, methods=["POST"]),
            Route("/api/broadcast/start", self.start_broadcast_api, methods=["POST"]),
            Route("/api/broadcast/stop", self.stop_broadcast_api, methods=["POST"]),
            Route("/api/queue/wait", self.wait_for_queue_api, methods=["POST"]),
            Route("/api/queue/wait_strict", self.wait_for_queue_strict_api, methods=["POST"]),
            Route("/api/bgm/switch", self.bgm_switch_api, methods=["POST"]),
            Route("/api/bgm/play", self.bgm_play_api, methods=["POST"]),
            Route("/api/bgm/stop", self.bgm_stop_api, methods=["POST"]),
            Route("/api/filler/play", self.filler_play_api, methods=["POST"]),
            Route("/api/auto_filler/start", self.auto_filler_start_api, methods=["POST"]),
            Route("/api/auto_filler/stop", self.auto_filler_stop_api, methods=["POST"]),
            Route("/api/chitchat/register", self.chitchat_register_api, methods=["POST"]),
            Route("/api/scene/switch", self.scene_switch_api, methods=["POST"]),
            Route("/api/caption/news", self.caption_news_api, methods=["POST"]),
            Route("/api/caption/clear", self.caption_clear_api, methods=["POST"]),
            Route("/api/caption/state", self.caption_state_api, methods=["GET"]),
            Route("/api/caption/set", self.caption_set_api, methods=["POST"]),
        ]
