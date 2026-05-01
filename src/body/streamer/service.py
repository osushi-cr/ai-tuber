"""MCP tools for body-streamer service"""
import os
import random
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Dict, Any
import logging
import json
import asyncio
from uuid import uuid4

# TTS engine selection
#   TTS_ENGINE=irodori: Mac native Irodori-TTS (in-process, 16.7s/utterance)
#   TTS_ENGINE=miotts:  MioTTS HTTP API client (out-of-process, ~2.2s/30char)
#   default:            legacy VoiceVox HTTP adapter
TTS_ENGINE = os.getenv("TTS_ENGINE", "voicevox")
if TTS_ENGINE == "irodori":
    from . import voice_adapter_irodori as voice_adapter
elif TTS_ENGINE == "miotts":
    from . import voice_adapter_miotts as voice_adapter
else:
    from . import voice_adapter
from . import obs_adapter
from ..service import BodyServiceBase

logger = logging.getLogger(__name__)


# フィラー音声（相槌・思考・繋ぎ等）の wav ライブラリのパス。
# voice_library/kurara/presets/ には filler_<category>_<variant>.wav 命名で配置されている。
# 主な category: aizuchi（相槌） / thinking（考え中） / reaction（驚き） / intro（出だし） / outro（締め）
FILLER_VOICE_DIR = Path(os.getenv(
    "FILLER_VOICE_DIR",
    str(Path.home() / "src/personal/Irodori-TTS/voice_library/kurara/presets")
))
ACTION_STATUS_MAX = int(os.getenv("ACTION_STATUS_MAX", "200"))
WAIT_STRICT_RECENT_LIMIT = int(os.getenv("WAIT_STRICT_RECENT_LIMIT", "20"))


class StreamerBodyService(BodyServiceBase):
    """BodyStreamer サービスの実装。"""

    def __init__(self):
        self._youtube_live_adapter = None
        self._youtube_comment_adapter = None
        self._current_broadcast_id = None
        self._action_queue = asyncio.Queue()
        self._task_status: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._worker_task = None
        # Test/local injected comments (drained on each get_comments call).
        # In production this is empty and YouTube live chat fills the response.
        self._dummy_comments: list[dict] = []
        self._dummy_comment_seq = 0
        # 自動 filler ループ用: 直近の voice 再生終了時刻（broadcast 中の沈黙監視）
        self._last_audio_end_time = 0.0
        self._broadcasting = False
        self._auto_filler_task = None
        # idle 中に saint_graph から事前登録された雑談セリフを混ぜる用
        self._chitchat_pool: list[str] = []
        # 初回 speak で waiting → kurara_main へ切替＋auto-filler 起動するため
        self._first_speech_done = False

    async def inject_comment(self, author: str, message: str) -> str:
        """テスト用にダミーコメントを注入します。次の get_comments() で返ります。"""
        self._dummy_comment_seq += 1
        self._dummy_comments.append({
            "id": f"dummy_{self._dummy_comment_seq}",
            "author": author or "guest",
            "message": message or "",
        })
        logger.info(f"[inject_comment] {author}: {message}")
        return "Comment injected"

    def _remember_task_status(
        self,
        action_id: str,
        status: str,
        task_type: str,
        payload: Dict[str, Any],
        error: Optional[str] = None,
    ) -> None:
        """action_id ごとの状態を最新 ACTION_STATUS_MAX 件だけ保持する。"""
        self._task_status[action_id] = {
            "status": status,
            "type": task_type,
            "payload": payload,
            "error": error,
            "updated_at": time.time(),
        }
        self._task_status.move_to_end(action_id)
        while len(self._task_status) > ACTION_STATUS_MAX:
            self._task_status.popitem(last=False)

    async def _enqueue_action(
        self,
        task_type: str,
        payload: Dict[str, Any],
        message: str,
    ) -> Dict[str, Any]:
        action_id = str(uuid4())
        task = {"action_id": action_id, "type": task_type, **payload}
        self._remember_task_status(action_id, "pending", task_type, payload)
        await self._action_queue.put(task)
        logger.info(f"[{task_type}:queued] action_id={action_id}")
        return {"message": message, "action_id": action_id}

    def _mark_action_running(self, task: Dict[str, Any]) -> None:
        action_id = task["action_id"]
        task_type = task.get("type", "unknown")
        payload = {k: v for k, v in task.items() if k not in {"action_id", "type"}}
        self._remember_task_status(action_id, "running", task_type, payload)

    def _mark_action_completed(self, task: Dict[str, Any]) -> None:
        action_id = task["action_id"]
        task_type = task.get("type", "unknown")
        payload = {k: v for k, v in task.items() if k not in {"action_id", "type"}}
        self._remember_task_status(action_id, "completed", task_type, payload)

    def _mark_action_failed(self, task: Dict[str, Any], error: BaseException) -> None:
        action_id = task["action_id"]
        task_type = task.get("type", "unknown")
        payload = {k: v for k, v in task.items() if k not in {"action_id", "type"}}
        error_text = str(error)
        self._remember_task_status(action_id, "failed", task_type, payload, error_text)
        logger.warning(
            f"[action_id={action_id} type={task_type} payload={payload}] failed: {error_text}"
        )

    def _mark_action_cancelled(self, task: Dict[str, Any]) -> None:
        action_id = task["action_id"]
        task_type = task.get("type", "unknown")
        payload = {k: v for k, v in task.items() if k not in {"action_id", "type"}}
        self._remember_task_status(action_id, "cancelled", task_type, payload, "cancelled")

    async def start_worker(self):
        """バックグラウンドワーカーを開始します。"""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._action_worker())
            logger.info("Action worker started")

    async def stop_worker(self):
        """バックグラウンドワーカーを停止します。"""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
            logger.info("Action worker stopped")

    async def _action_worker(self):
        """キューからタスクを取り出して順次実行するワーカー。"""
        logger.info("Action worker loop entered")
        while True:
            task = None
            task_done_called = False
            try:
                task = await self._action_queue.get()
                self._mark_action_running(task)
                await self._handle_action(task)
                self._mark_action_completed(task)
            except asyncio.CancelledError:
                if task is not None:
                    self._mark_action_cancelled(task)
                    self._action_queue.task_done()
                    task_done_called = True
                break
            except Exception as e:
                if task is None:
                    logger.error(f"Error in action worker loop: {e}")
                    await asyncio.sleep(1)
                    continue
                self._mark_action_failed(task, e)
            finally:
                if task is not None and not task_done_called:
                    self._action_queue.task_done()

    async def _handle_action(self, task: Dict[str, Any]) -> None:
        task_type = task.get("type")

        if task_type == "speak":
            await self._handle_speak_action(task)
        elif task_type == "change_emotion":
            await self._handle_change_emotion_action(task)
        elif task_type == "filler":
            await self._handle_filler_action(task)
        elif task_type == "caption_news":
            ok = await obs_adapter.call_with_transient_retry(
                obs_adapter.update_news_caption,
                task.get("title", ""),
                task.get("summary", ""),
            )
            if not ok:
                raise RuntimeError("caption_news failed")
        elif task_type == "caption_clear":
            ok = await obs_adapter.call_with_transient_retry(obs_adapter.clear_news_caption)
            if not ok:
                raise RuntimeError("caption_clear failed")
        elif task_type == "scene_switch":
            ok = await obs_adapter.call_with_transient_retry(
                obs_adapter.switch_scene,
                task.get("scene"),
            )
            if not ok:
                raise RuntimeError(f"scene_switch failed: {task.get('scene')}")
        elif task_type == "bgm_switch":
            ok = await obs_adapter.call_with_transient_retry(
                obs_adapter.switch_bgm,
                task.get("bgm_id"),
            )
            if not ok:
                raise RuntimeError(f"bgm_switch failed: {task.get('bgm_id')}")
        elif task_type == "bgm_play":
            ok = await obs_adapter.call_with_transient_retry(
                obs_adapter.play_bgm,
                task.get("bgm_id"),
                restart=task.get("restart", True),
            )
            if not ok:
                raise RuntimeError(f"bgm_play failed: {task.get('bgm_id')}")
        elif task_type == "bgm_stop":
            ok = await obs_adapter.call_with_transient_retry(
                obs_adapter.stop_bgm,
                task.get("bgm_id"),
            )
            if not ok:
                raise RuntimeError(f"bgm_stop failed: {task.get('bgm_id')}")
        else:
            raise RuntimeError(f"unknown action type: {task_type}")

    async def _handle_speak_action(self, task: Dict[str, Any]) -> None:
        text = task.get("text")
        style = task.get("style")
        speaker_id = task.get("speaker_id")
        caption_title = task.get("caption_title")
        caption_summary = task.get("caption_summary")

        # 1. 音声生成（2〜3秒かかる）
        file_path, duration = await voice_adapter.generate_and_save(text, style, speaker_id)

        # 2. 【初回 speak 時に waiting → kurara_main 切替 ＋ auto-filler 起動】
        if self._broadcasting and not self._first_speech_done:
            self._first_speech_done = True
            try:
                await obs_adapter.call_with_transient_retry(
                    obs_adapter.switch_scene,
                    os.getenv("BROADCAST_MAIN_SCENE", "kurara_main"),
                )
            except Exception as e:
                logger.warning(f"Failed to switch to main scene on first speech: {e}")
            self._last_audio_end_time = time.time()
            if self._auto_filler_task is None or self._auto_filler_task.done():
                self._auto_filler_task = asyncio.create_task(self._auto_filler_loop())
                logger.info("Auto-filler loop started on first speech")

        # 3. caption 付き speak は、音声生成完了後・再生開始直前に更新する。
        if caption_title is not None or caption_summary is not None:
            ok = await obs_adapter.call_with_transient_retry(
                obs_adapter.update_news_caption,
                caption_title or "",
                caption_summary or "",
            )
            if not ok:
                raise RuntimeError("caption update before speak failed")

        # 4. 表情変更と音声再生を「同時」に開始（ズレをゼロに近づける）
        ok = await self.play_audio_with_sync_emotion(file_path, duration, style)
        if not ok:
            raise RuntimeError("audio playback failed")

        # 5. 音声再生終了後、即座に口を閉じる
        await obs_adapter.set_visible_source("silent")

        logger.info(f"[Worker:speak] Completed: {text[:30]}...")

    async def _handle_change_emotion_action(self, task: Dict[str, Any]) -> None:
        emotion = task.get("emotion")
        await obs_adapter.set_visible_source(emotion)
        logger.info(f"[Worker:emotion] Changed to {emotion}")

    async def _handle_filler_action(self, task: Dict[str, Any]) -> None:
        file_path = task.get("file_path")
        style = task.get("style", "neutral")
        import wave

        with wave.open(file_path, "rb") as w:
            duration = w.getnframes() / float(w.getframerate())
        ok = await self.play_audio_with_sync_emotion(file_path, duration, style)
        if not ok:
            raise RuntimeError("filler audio playback failed")
        await obs_adapter.set_visible_source("silent")
        logger.info(f"[Worker:filler] Completed: {Path(file_path).name}")

    async def speak(
        self,
        text: str,
        style: str = "neutral",
        speaker_id: Optional[int] = None,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """視聴者に対してテキストを発話します (キューに追加して即時復帰)。"""
        result = await self._enqueue_action("speak", {
            "text": text,
            "style": style,
            "speaker_id": speaker_id,
            "caption_title": caption_title,
            "caption_summary": caption_summary,
        }, "Speech queued")
        logger.info(f"[speak:queued] '{text[:30]}...'")
        return result

    async def change_emotion(self, emotion: str) -> Dict[str, Any]:
        """アバターの表情（感情）を変更します (キューに追加して即時復帰)。"""
        result = await self._enqueue_action(
            "change_emotion",
            {"emotion": emotion},
            "Emotion change queued",
        )
        logger.info(f"[change_emotion:queued] {emotion}")
        return result

    async def play_filler(self, category: str, style: str = "neutral") -> Dict[str, Any] | str:
        """category 該当の filler wav をランダム選択して voice ソースで再生します。

        category: "aizuchi" / "thinking" / "reaction" / "intro" / "outro" 等。
        FILLER_VOICE_DIR 配下の `filler_<category>_*.wav` から1つランダム選択する。
        """
        if not FILLER_VOICE_DIR.exists():
            return f"FILLER_VOICE_DIR not found: {FILLER_VOICE_DIR}"
        candidates = sorted(FILLER_VOICE_DIR.glob(f"filler_{category}_*.wav"))
        if not candidates:
            return f"No filler wav found for category: {category}"
        chosen = random.choice(candidates)
        result = await self._enqueue_action("filler", {
            "file_path": str(chosen),
            "style": style,
        }, f"Filler queued: {chosen.name}")
        logger.info(f"[filler:queued] category={category} file={chosen.name}")
        return result

    async def play_bgm(self, bgm_id: str, restart: bool = True) -> Dict[str, Any]:
        """BGM ソースを表示し再生します（presentation queue に投入）。"""
        return await self._enqueue_action(
            "bgm_play",
            {"bgm_id": bgm_id, "restart": restart},
            f"BGM '{bgm_id}' queued",
        )

    async def stop_bgm(self, bgm_id: str) -> Dict[str, Any]:
        """BGM ソースを非表示にして停止します（presentation queue に投入）。"""
        return await self._enqueue_action(
            "bgm_stop",
            {"bgm_id": bgm_id},
            f"BGM '{bgm_id}' stop queued",
        )

    async def switch_bgm(self, bgm_id: str) -> Dict[str, Any]:
        """指定BGMへ切替（他のループ系BGMを停止）し、SE は触りません。"""
        return await self._enqueue_action(
            "bgm_switch",
            {"bgm_id": bgm_id},
            f"BGM switch to '{bgm_id}' queued",
        )

    async def switch_scene(self, scene_name: str) -> Dict[str, Any]:
        """OBS のプログラムシーンを切り替える（waiting / kurara_main / ending 等）。"""
        return await self._enqueue_action(
            "scene_switch",
            {"scene": scene_name},
            f"Scene switch to '{scene_name}' queued",
        )

    async def update_news_caption(self, title: str, summary: str) -> Dict[str, Any]:
        """OBS のニュースキャプション（タイトル＋要約）を更新する。"""
        return await self._enqueue_action(
            "caption_news",
            {"title": title, "summary": summary},
            "News caption queued",
        )

    async def clear_news_caption(self) -> Dict[str, Any]:
        """OBS のニュースキャプションを空にする。"""
        return await self._enqueue_action(
            "caption_clear",
            {},
            "News caption clear queued",
        )

    async def peek_comments(self) -> str:
        """OBS overlay 表示用にコメントを peek する（buffer を破壊しない）。

        YouTube live chat 由来は adapter の buffer をスナップショット、
        ダミー注入分はそのまま返す（こちらも破壊しない）。
        """
        streaming_mode = os.getenv("STREAMING_MODE", "false").lower() == "true"

        comments: list[dict] = []
        try:
            if streaming_mode and self._youtube_comment_adapter:
                comments.extend(self._youtube_comment_adapter.peek() or [])
        except Exception as e:
            logger.error(f"Error fetching YouTube comments (peek): {e}")

        if self._dummy_comments:
            comments.extend(self._dummy_comments)

        if not comments:
            return json.dumps([])

        return json.dumps(comments, ensure_ascii=False)

    async def consume_comments(self) -> str:
        """saint_graph リアクション用にコメントを consume する（buffer を drain）。

        YouTube live chat 由来とダミー注入分の両方を drain して返す。
        """
        streaming_mode = os.getenv("STREAMING_MODE", "false").lower() == "true"

        comments: list[dict] = []
        try:
            if streaming_mode and self._youtube_comment_adapter:
                comments.extend(self._youtube_comment_adapter.consume() or [])
        except Exception as e:
            logger.error(f"Error fetching YouTube comments (consume): {e}")

        if self._dummy_comments:
            comments.extend(self._dummy_comments)
            self._dummy_comments = []

        if not comments:
            return json.dumps([])

        logger.info(f"[consume_comments] Drained {len(comments)} comments")
        return json.dumps(comments, ensure_ascii=False)

    async def start_broadcast(self, config: Optional[Dict[str, Any]] = None) -> str:
        """配信または録画を即時開始します。

        OBS は呼び出し時のシーン（通常 waiting）のまま視聴者へ配信される。
        最初の speak が来た時点で kurara_main シーンへ自動切替し auto-filler が起動する。
        """
        config = config or {}
        streaming_mode = os.getenv("STREAMING_MODE", "false").lower() == "true"

        # 前回配信時のニュースキャプションが残らないよう、配信開始時に必ず初期化する。
        try:
            clear_result = await self.clear_news_caption()
            ok = await self.wait_for_queue_strict([clear_result["action_id"]])
            if not ok:
                logger.warning("Failed to clear news caption at broadcast start")
        except Exception as e:
            logger.warning(f"Failed to clear news caption at broadcast start: {e}")

        try:
            if streaming_mode:
                result = await self._start_streaming(config)
                # ストリーミング開始後の安定化待機（YouTube側にデータが届き始めるまで数秒待つ）
                await asyncio.sleep(3)
            else:
                result = await self.start_obs_recording()
                # OBS録画開始後の安定化待機
                await asyncio.sleep(2)

            self._broadcasting = True
            self._first_speech_done = False
            logger.info("[start_broadcast] Broadcast started. Streaming the current scene (typically 'waiting').")
            return result
        except Exception as e:
            logger.error(f"Error in start_broadcast: {e}")
            return f"配信開始エラー: {str(e)}"

    async def _auto_filler_loop(self):
        """配信中、voice 再生していない idle が閾値超えたら filler / chitchat を自動投入する。

        - `FILLER_AUTO_IDLE_SECONDS`（既定2.0秒）以上 idle になったら投入
        - `FILLER_AUTO_INTERVAL`（既定2.0秒）毎にチェック
        - filler カテゴリ（aizuchi/thinking/reaction）と chitchat（雑談セリフ）を交互に巡回
        - キューに何か積まれている間は投入しない（saint_graph 本セリフを優先）
        """
        idle_threshold = float(os.getenv("FILLER_AUTO_IDLE_SECONDS", "2.0"))
        interval = float(os.getenv("FILLER_AUTO_INTERVAL", "2.0"))
        categories = ["aizuchi", "thinking", "reaction"]
        i = 0
        while self._broadcasting:
            await asyncio.sleep(interval)
            if not self._broadcasting:
                break
            if not self._action_queue.empty():
                continue
            idle = time.time() - self._last_audio_end_time
            if idle < idle_threshold:
                continue
            # chitchat_pool が空なら filler のみ。pool ありなら filler/chitchat を交互に。
            if self._chitchat_pool and i % 2 == 1:
                line = random.choice(self._chitchat_pool)
                try:
                    await self.speak(line, style="neutral")
                    logger.info(f"[auto-filler:chitchat] '{line}'")
                except Exception as e:
                    logger.warning(f"Auto-filler chitchat failed: {e}")
            else:
                cat = categories[(i // 2) % len(categories)] if self._chitchat_pool else categories[i % len(categories)]
                try:
                    await self.play_filler(cat)
                except Exception as e:
                    logger.warning(f"Auto-filler failed ({cat}): {e}")
            i += 1
        logger.info("Auto-filler loop exited")

    async def register_chitchat_lines(self, lines: list) -> str:
        """saint_graph 等から雑談セリフのリストを登録し、auto-filler に混ぜる。"""
        cleaned = [s for s in lines if isinstance(s, str) and s.strip()]
        self._chitchat_pool = cleaned
        logger.info(f"[chitchat] registered {len(cleaned)} chitchat lines")
        return f"Registered {len(cleaned)} chitchat lines"

    async def stop_broadcast(self) -> str:
        """配信または録画を停止します。"""
        # 自動 filler ループを止める（残発話完了待ち中に新規 filler が積まれないようにする）
        self._broadcasting = False

        # すべての発話が完了するまで待機してから停止する
        await self.wait_for_queue()

        # OBS/YouTube の音声バッファがドレインするまでの猶予時間
        # BROADCAST_STOP_DELAY で調整可能（デフォルト 3秒）
        stop_delay = float(os.getenv("BROADCAST_STOP_DELAY", "5.0"))
        logger.info(f"Queue empty. Waiting {stop_delay}s before stopping broadcast...")
        await asyncio.sleep(stop_delay)

        streaming_mode = os.getenv("STREAMING_MODE", "false").lower() == "true"

        try:
            if streaming_mode:
                return await self._stop_streaming()
            else:
                return await self.stop_obs_recording()
        except Exception as e:
            logger.error(f"Error in stop_broadcast: {e}")
            return f"配信停止エラー: {str(e)}"

    async def wait_for_queue(self) -> str:
        """キューが空になるまで待機します。"""
        logger.info("Waiting for action queue to be empty...")
        await self._action_queue.join()
        logger.info("Action queue is empty")
        return "All queued actions completed"

    async def wait_for_queue_strict(
        self,
        action_ids: Optional[list[str]] = None,
        recent_count: Optional[int] = None,
    ) -> bool:
        """キュー消化後、指定 action がすべて completed か検査する。"""
        logger.info("Waiting for action queue to be empty (strict)...")
        await self._action_queue.join()
        target_ids = action_ids
        if target_ids is None:
            limit = recent_count or WAIT_STRICT_RECENT_LIMIT
            target_ids = list(self._task_status.keys())[-limit:]

        for action_id in target_ids:
            status = self._task_status.get(action_id)
            if status is None:
                logger.warning(f"wait_for_queue_strict: unknown action_id={action_id}")
                return False
            if status.get("status") != "completed":
                logger.warning(
                    f"wait_for_queue_strict: action_id={action_id} status={status.get('status')}"
                )
                return False
        return True

    # --- ヘルパーメソッドおよび固有メソッド ---

    async def play_audio_with_sync_emotion(self, file_path: str, duration: float, emotion: str) -> bool:
        """音声の装填を先に済ませ、表情切り替えと再生開始を同時に叩き込みます。"""
        try:
            logger.info(f"[play_audio_sync] Starting playback of {file_path} (duration: {duration:.1f}s) with emotion: {emotion}")
            # obs_adapter側の新メソッドを呼び出す（表情と音声を同時着火）
            ok = await obs_adapter.play_media_with_emotion("voice", file_path, emotion)
            if not ok:
                raise RuntimeError("play_media_with_emotion returned false")

            # 再生完了まで待機
            await asyncio.sleep(duration + 0.1)

            # 自動 filler ループが「voice 再生していない idle 時間」を測るための基準時刻
            self._last_audio_end_time = time.time()

            logger.info(f"[play_audio_sync] Completed playback ({duration:.1f}s)")
            return True
        except Exception as e:
            logger.error(f"Error in play_audio_sync: {e}")
            raise

    async def play_audio_file(self, file_path: str, duration: float) -> bool:
        """（互換性用）通常の音声再生。内部的に同期再生を使用します。"""
        return await self.play_audio_with_sync_emotion(file_path, duration, "neutral")


    async def start_obs_recording(self) -> str:
        """OBSの録画を開始します。"""
        try:
            success = await obs_adapter.start_recording()
            if success:
                logger.info("[start_obs_recording] Success")
                return "OBS録画を開始しました。"
            else:
                logger.warning("[start_obs_recording] Failed")
                return "OBS録画の開始に失敗しました。接続を確認してください。"
        except Exception as e:
            logger.error(f"Error in start_obs_recording tool: {e}")
            return f"録画開始エラー: {str(e)}"

    async def stop_obs_recording(self) -> str:
        """OBSの録画を停止します。"""
        try:
            success = await obs_adapter.stop_recording()
            if success:
                logger.info("[stop_obs_recording] Success")
                return "OBS録画を停止しました。"
            else:
                logger.warning("[stop_obs_recording] Failed")
                return "OBS録画の停止に失敗しました。接続を確認してください。"
        except Exception as e:
            logger.error(f"Error in stop_obs_recording tool: {e}")
            return f"録画停止エラー: {str(e)}"

    async def _start_streaming(self, config: dict) -> str:
        """YouTube Live 配信を開始する内部関数。"""
        from .youtube_live_adapter import YoutubeLiveAdapter
        
        self._youtube_live_adapter = YoutubeLiveAdapter()
        youtube_client, _ = self._youtube_live_adapter.authenticate_youtube()
        
        title = config.get("title", "AI Tuber Live Stream")
        description = config.get("description", "")
        scheduled_start_time = config.get("scheduled_start_time", "")
        thumbnail_path = config.get("thumbnail_path")
        privacy_status = config.get("privacy_status", "private")
        
        logger.info(f"Creating YouTube Live broadcast: {title}")
        live_response = self._youtube_live_adapter.create_live(
            youtube_client, title, description, scheduled_start_time,
            thumbnail_path, privacy_status
        )
        
        stream_key = live_response['stream']['cdn']['ingestionInfo']['streamName']
        stream_id = live_response['stream']['id']
        self._current_broadcast_id = live_response['broadcast']['id']

        logger.info("Starting OBS streaming with YouTube stream key")
        success = await obs_adapter.start_streaming(stream_key)

        if not success:
            return "OBSストリーミングの開始に失敗しました。"

        # OBS から RTMP が届いて liveStream が active になるのを待ってから broadcast を live に遷移する。
        # この遷移を行わないと broadcast は status=ready のまま視聴者には公開されない。
        try:
            became_active = await asyncio.to_thread(
                self._youtube_live_adapter.wait_for_stream_active,
                youtube_client, stream_id, 30
            )
            if became_active:
                self._youtube_live_adapter.start_live(youtube_client, self._current_broadcast_id)
                logger.info(f"Transitioned broadcast {self._current_broadcast_id} to live")
            else:
                logger.warning(
                    f"liveStream did not become active within 30s. broadcast {self._current_broadcast_id} stays in 'ready'. "
                    "YouTube Studio で手動「配信を開始」が必要。"
                )
        except Exception as e:
            logger.error(f"Failed to transition broadcast to live: {e}")

        from .youtube_comment_adapter import YouTubeCommentAdapter
        self._youtube_comment_adapter = YouTubeCommentAdapter(self._current_broadcast_id)

        logger.info(f"[start_streaming] Success - Broadcast ID: {self._current_broadcast_id}")
        return f"YouTube Live配信を開始しました。ブロードキャストID: {self._current_broadcast_id}"

    async def _stop_streaming(self) -> str:
        """YouTube Live 配信を停止する内部関数。

        OBS Stop と YouTube transition('complete') と CommentAdapter close は
        互いに独立した責務なので、いずれかが失敗しても他は確実に実行されるように
        個別に try/except で囲む（quota 超過時に OBS が止まらない事故を防ぐ）。
        """
        # 1. OBS の RTMP 送信を確実に止める（一番優先度が高い）
        try:
            logger.info("Stopping OBS streaming")
            await obs_adapter.stop_streaming()
        except Exception as e:
            logger.error(f"Failed to stop OBS streaming: {e}")

        # 2. YouTube broadcast を complete に遷移（quota 超過等で失敗しても OBS は既に止まっている）
        if self._youtube_live_adapter and self._current_broadcast_id:
            try:
                youtube_client, _ = self._youtube_live_adapter.authenticate_youtube()
                self._youtube_live_adapter.stop_live(youtube_client, self._current_broadcast_id)
                logger.info(f"Stopped YouTube broadcast: {self._current_broadcast_id}")
            except Exception as e:
                logger.error(
                    f"Failed to transition broadcast {self._current_broadcast_id} to 'complete': {e}. "
                    "YouTube Studio で手動「配信を終了」が必要かもしれません。"
                )

        # 3. コメント取得サブプロセスを終了
        if self._youtube_comment_adapter:
            try:
                self._youtube_comment_adapter.close()
            except Exception as e:
                logger.error(f"Failed to close YouTubeCommentAdapter: {e}")
            self._youtube_comment_adapter = None

        self._current_broadcast_id = None

        logger.info("[stop_streaming] Success")
        return "YouTube Live配信を停止しました。"


# Singleton インスタンス
body_service = StreamerBodyService()
