"""MCP tools for body-streamer service"""
import os
import random
import time
from pathlib import Path
from typing import Optional, Dict, Any
import logging
import json
import asyncio

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


class StreamerBodyService(BodyServiceBase):
    """BodyStreamer サービスの実装。"""

    def __init__(self):
        self._youtube_live_adapter = None
        self._youtube_comment_adapter = None
        self._current_broadcast_id = None
        self._action_queue = asyncio.Queue()
        self._worker_task = None
        self._pending_broadcast_config = None
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
            try:
                task = await self._action_queue.get()
                task_type = task.get("type")
                
                if task_type == "speak":
                    text = task.get("text")
                    style = task.get("style")
                    speaker_id = task.get("speaker_id")
                    
                    try:
                        # 1. 音声生成（2〜3秒かかる）
                        file_path, duration = await voice_adapter.generate_and_save(text, style, speaker_id)
                        
                        # 2. 【配信開始の同期（初回のみ）】
                        if self._pending_broadcast_config is not None:
                            config = self._pending_broadcast_config
                            self._pending_broadcast_config = None
                            await self._execute_actual_broadcast_start(config)

                        # 3. 表情変更と音声再生を「同時」に開始（ズレをゼロに近づける）
                        await self.play_audio_with_sync_emotion(file_path, duration, style)
                        
                        # 4. 音声再生終了後、即座に口を閉じる
                        await obs_adapter.set_visible_source("silent")

                        logger.info(f"[Worker:speak] Completed: {text[:30]}...")
                    except Exception as e:
                        logger.error(f"Error in worker speak task: {e}")
                
                elif task_type == "change_emotion":
                    emotion = task.get("emotion")
                    try:
                        await obs_adapter.set_visible_source(emotion)
                        logger.info(f"[Worker:emotion] Changed to {emotion}")
                    except Exception as e:
                        logger.error(f"Error in worker emotion task: {e}")

                elif task_type == "filler":
                    file_path = task.get("file_path")
                    style = task.get("style", "neutral")
                    try:
                        import wave
                        with wave.open(file_path, "rb") as w:
                            duration = w.getnframes() / float(w.getframerate())
                        await self.play_audio_with_sync_emotion(file_path, duration, style)
                        await obs_adapter.set_visible_source("silent")
                        logger.info(f"[Worker:filler] Completed: {Path(file_path).name}")
                    except Exception as e:
                        logger.error(f"Error in worker filler task: {e}")

                self._action_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in action worker loop: {e}")
                await asyncio.sleep(1)

    async def speak(self, text: str, style: str = "neutral", speaker_id: Optional[int] = None) -> str:
        """視聴者に対してテキストを発話します (キューに追加して即時復帰)。"""
        await self._action_queue.put({
            "type": "speak",
            "text": text,
            "style": style,
            "speaker_id": speaker_id
        })
        logger.info(f"[speak:queued] '{text[:30]}...'")
        return "Speech queued"

    async def change_emotion(self, emotion: str) -> str:
        """アバターの表情（感情）を変更します (キューに追加して即時復帰)。"""
        await self._action_queue.put({
            "type": "change_emotion",
            "emotion": emotion
        })
        logger.info(f"[change_emotion:queued] {emotion}")
        return "Emotion change queued"

    async def play_filler(self, category: str, style: str = "neutral") -> str:
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
        await self._action_queue.put({
            "type": "filler",
            "file_path": str(chosen),
            "style": style,
        })
        logger.info(f"[filler:queued] category={category} file={chosen.name}")
        return f"Filler queued: {chosen.name}"

    async def play_bgm(self, bgm_id: str, restart: bool = True) -> str:
        """BGM ソースを表示し再生します（obs_adapter のラッパー）。"""
        ok = await obs_adapter.play_bgm(bgm_id, restart=restart)
        return f"BGM '{bgm_id}' started" if ok else f"Failed to play BGM '{bgm_id}'"

    async def stop_bgm(self, bgm_id: str) -> str:
        """BGM ソースを非表示にして停止します（obs_adapter のラッパー）。"""
        ok = await obs_adapter.stop_bgm(bgm_id)
        return f"BGM '{bgm_id}' stopped" if ok else f"Failed to stop BGM '{bgm_id}'"

    async def switch_bgm(self, bgm_id: str) -> str:
        """指定BGMへ切替（他のループ系BGMを停止）し、SE は触りません。"""
        ok = await obs_adapter.switch_bgm(bgm_id)
        return f"BGM switched to '{bgm_id}'" if ok else f"Failed to switch BGM to '{bgm_id}'"

    async def switch_scene(self, scene_name: str) -> str:
        """OBS のプログラムシーンを切り替える（waiting / kurara_main / ending 等）。"""
        ok = await obs_adapter.switch_scene(scene_name)
        return f"Scene switched to '{scene_name}'" if ok else f"Failed to switch scene to '{scene_name}'"

    async def get_comments(self) -> str:
        """コメントを取得します（YouTube live chat ＋ ダミー注入分の両方）。"""
        streaming_mode = os.getenv("STREAMING_MODE", "false").lower() == "true"

        comments: list[dict] = []
        try:
            if streaming_mode and self._youtube_comment_adapter:
                comments.extend(self._youtube_comment_adapter.get() or [])
        except Exception as e:
            logger.error(f"Error fetching YouTube comments: {e}")

        # Drain dummy comments (test/local).
        if self._dummy_comments:
            comments.extend(self._dummy_comments)
            self._dummy_comments = []

        if not comments:
            return json.dumps([])

        logger.info(f"[get_comments] Retrieved {len(comments)} comments")
        return json.dumps(comments, ensure_ascii=False)

    async def start_broadcast(self, config: Optional[Dict[str, Any]] = None) -> str:
        """配信または録画の開始を予約します（最初の発話時に同期して開始されます）。"""
        self._pending_broadcast_config = config or {}
        logger.info("[start_broadcast] Broadcast start deferred until first speech.")
        return "配信開始を予約しました。最初の発話に合わせて開始されます。"

    async def _execute_actual_broadcast_start(self, config: Dict[str, Any]) -> str:
        """実際に配信または録画を開始する内部メソッド。"""
        streaming_mode = os.getenv("STREAMING_MODE", "false").lower() == "true"

        try:
            if streaming_mode:
                result = await self._start_streaming(config)
                # ストリーミング開始後の安定化待機（YouTube側にデータが届き始めるまで数秒待つ）
                await asyncio.sleep(3)
            else:
                result = await self.start_obs_recording()
                # OBS録画開始後の安定化待機
                await asyncio.sleep(2)

            # 配信本編シーンへ自動切替（waiting → kurara_main）
            try:
                await obs_adapter.switch_scene(os.getenv("BROADCAST_MAIN_SCENE", "kurara_main"))
            except Exception as e:
                logger.warning(f"Failed to switch to main scene: {e}")

            # 配信中の沈黙を埋める自動 filler ループを起動
            self._broadcasting = True
            self._last_audio_end_time = time.time()
            if self._auto_filler_task is None or self._auto_filler_task.done():
                self._auto_filler_task = asyncio.create_task(self._auto_filler_loop())
                logger.info("Auto-filler loop started")
            return result
        except Exception as e:
            logger.error(f"Error in _execute_actual_broadcast_start: {e}")
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

    # --- ヘルパーメソッドおよび固有メソッド ---

    async def play_audio_with_sync_emotion(self, file_path: str, duration: float, emotion: str) -> str:
        """音声の装填を先に済ませ、表情切り替えと再生開始を同時に叩き込みます。"""
        try:
            logger.info(f"[play_audio_sync] Starting playback of {file_path} (duration: {duration:.1f}s) with emotion: {emotion}")
            # obs_adapter側の新メソッドを呼び出す（表情と音声を同時着火）
            await obs_adapter.play_media_with_emotion("voice", file_path, emotion)

            # 再生完了まで待機
            await asyncio.sleep(duration + 0.1)

            # 自動 filler ループが「voice 再生していない idle 時間」を測るための基準時刻
            self._last_audio_end_time = time.time()

            logger.info(f"[play_audio_sync] Completed playback ({duration:.1f}s)")
            return f"再生完了 ({duration:.1f}s)"
        except Exception as e:
            logger.error(f"Error in play_audio_sync: {e}")
            return f"再生エラー: {str(e)}"

    async def play_audio_file(self, file_path: str, duration: float) -> str:
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
