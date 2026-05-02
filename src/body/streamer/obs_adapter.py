"""OBS WebSocket adapter for scene and source control"""
import os
import logging
import unicodedata
from typing import Optional
import asyncio

try:
    from obswebsocket import obsws, requests as obs_requests, events as obs_events
except ImportError:
    obs_requests = None
    obsws = None
    obs_events = None


logger = logging.getLogger(__name__)

# OBS configuration from environment
OBS_HOST = os.getenv("OBS_HOST", "obs-studio")
OBS_PORT = int(os.getenv("OBS_PORT", "4455"))
OBS_PASSWORD = os.getenv("OBS_PASSWORD", "")

# Emotion to source name mapping (matching main branch OBS scene collection)
EMOTION_MAP = {
    "neutral": "normal",
    "happy": "joyful",
    "joyful": "joyful",
    "fun": "fun",
    "sad": "sad",
    "sorrow": "sad",
    "angry": "angry",
    "silent": "silent",
}

# リップシンク調整：音声が鳴り始めるまでのOBS内部遅延をミリ秒で指定
# 0.5s〜1s遅れるとのことなので、デフォルトを400ms〜800ms程度で調整可能にします
LIP_SYNC_ADJUST_MS = int(os.getenv("LIP_SYNC_ADJUST_MS", "500"))

# ニュースキャプション用テキストソース名（kurara_main シーン内）
NEWS_CAPTION_TITLE_SOURCE = os.getenv("NEWS_CAPTION_TITLE_SOURCE", "news_caption_title")
NEWS_CAPTION_SUMMARY_SOURCE = os.getenv("NEWS_CAPTION_SUMMARY_SOURCE", "news_caption_summary")
NEWS_CAPTION_SCENE = os.getenv("BROADCAST_MAIN_SCENE", "kurara_main")
NEWS_CAPTION_INPUT_KIND = os.getenv("NEWS_CAPTION_INPUT_KIND", "text_ft2_source_v2")
# 改行幅（全角=2、半角=1 として数えた桁数）。OBS GUI の表示幅に合わせて調整
NEWS_CAPTION_TITLE_WRAP = int(os.getenv("NEWS_CAPTION_TITLE_WRAP", "30"))
NEWS_CAPTION_SUMMARY_WRAP = int(os.getenv("NEWS_CAPTION_SUMMARY_WRAP", "36"))
_news_caption_initialized = False


def _wrap_jp(text: str, width: int) -> str:
    """全角文字を2幅として扱って width 桁で折り返す。既存の改行は段落区切りとして保持する。"""
    if width <= 0 or not text:
        return text
    out_lines = []
    for paragraph in text.split("\n"):
        line = ""
        line_w = 0
        for ch in paragraph:
            ch_w = 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1
            if line_w + ch_w > width and line:
                out_lines.append(line)
                line = ch
                line_w = ch_w
            else:
                line += ch
                line_w += ch_w
        out_lines.append(line)
    return "\n".join(out_lines)

# Global WebSocket client
ws_client: Optional[obsws] = None
_playback_event = asyncio.Event()
_main_loop: Optional[asyncio.AbstractEventLoop] = None


# Scene Item ID Cache (to avoid redundant API calls)
_source_id_cache = {}
_current_scene_name = None


def _is_transient_obs_error(error: BaseException) -> bool:
    """OBS websocket の一時切断・タイムアウトとして扱う例外を判定する。"""
    return isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError))


async def call_with_transient_retry(func, *args, **kwargs) -> bool:
    """OBS 操作を実行し、一時的な接続不良なら 1 回だけ再接続して retry する。"""
    for attempt in range(2):
        try:
            ok = await func(*args, **kwargs)
            if ok:
                return True
            if attempt == 0:
                logger.warning(f"{func.__name__} returned false; reconnecting OBS and retrying once")
                await disconnect()
                await asyncio.sleep(0.2)
                continue
            return False
        except Exception as e:
            if attempt == 0 and _is_transient_obs_error(e):
                logger.warning(f"{func.__name__} transient OBS error; retrying once: {e}")
                await disconnect()
                await asyncio.sleep(0.2)
                continue
            raise
    return False

def _on_media_start(event):
    """OBSからのメディア再生開始イベントを受け取るコールバック"""
    try:
        source_name = event.getInputName()
        if source_name == "voice":
            logger.info("OBS Event: 'voice' playback actually started!")
            # メインスレッドのEventをスレッドセーフにセット
            if _main_loop and _main_loop.is_running():
                _main_loop.call_soon_threadsafe(_playback_event.set)
            else:
                # フォールバック
                _playback_event.set()
    except Exception as e:
        logger.error(f"Error in OBS callback: {e}")


async def connect() -> bool:
    """OBS WebSocketに接続し、イベントリスナーを登録します。"""
    global ws_client
    
    # 接続確認
    if ws_client is not None:
        try:
            ws_client.call(obs_requests.GetVersion())
            return True
        except Exception:
            ws_client = None
    
    try:
        global _main_loop
        _main_loop = asyncio.get_running_loop()

        ws_client = obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD)
        ws_client.connect()
        
        # 再生開始イベント（v5）を購読
        ws_client.register(_on_media_start, obs_events.MediaInputPlaybackStarted)

        
        logger.info("Connected to OBS WebSocket and registered event listeners")
        return True
    except Exception as e:
        logger.debug(f"Failed to connect to OBS: {e}")
        ws_client = None
        return False


async def disconnect():
    """OBS WebSocketから切断します。"""
    global ws_client
    
    if ws_client is not None:
        try:
            ws_client.disconnect()
            logger.info("Disconnected from OBS WebSocket")
        except Exception as e:
            logger.error(f"Error disconnecting from OBS: {e}")
        finally:
            ws_client = None


async def set_source_visibility(source_name: str, visible: bool, scene_name: Optional[str] = None) -> bool:
    """
    ソースの表示/非表示を切り替えます（キャッシュを活用して高速化）。
    """
    global _source_id_cache, _current_scene_name
    
    if not await connect():
        return False
    
    try:
        # シーン名の取得とキャッシュのリフレッシュ
        if scene_name is None:
            if _current_scene_name is None:
                resp = ws_client.call(obs_requests.GetCurrentProgramScene())
                _current_scene_name = resp.getSceneName()
            scene_name = _current_scene_name

        # キャッシュの確認
        cache_key = f"{scene_name}:{source_name}"
        scene_item_id = _source_id_cache.get(cache_key)

        if scene_item_id is None:
            # キャッシュにない場合は取得
            scene_items = ws_client.call(obs_requests.GetSceneItemList(sceneName=scene_name))
            for item in scene_items.getSceneItems():
                item_name = item["sourceName"]
                item_id = item["sceneItemId"]
                _source_id_cache[f"{scene_name}:{item_name}"] = item_id
                if item_name == source_name:
                    scene_item_id = item_id
        
        if scene_item_id is None:
            logger.warning(f"Source '{source_name}' not found in scene '{scene_name}'")
            return False
            
        # シーンアイテムの表示/非表示を設定
        ws_client.call(obs_requests.SetSceneItemEnabled(
            sceneName=scene_name,
            sceneItemId=scene_item_id,
            sceneItemEnabled=visible
        ))
        return True
    except Exception as e:
        logger.error(f"Error setting source visibility for '{source_name}': {e}")
        # エラー時はキャッシュをクリアして次回リトライ
        _source_id_cache = {}
        _current_scene_name = None
        return False


async def set_visible_source(emotion: str) -> str:
    """
    指定された感情に対応する立ち絵ソースを表示します。
    """
    source_name = EMOTION_MAP.get(emotion, EMOTION_MAP["neutral"])
    if not await connect():
        return f"OBS接続エラー"
    
    try:
        # スレッドセーフかつ高速に実行するため、直列に実行します（キャッシュがあるので十分早いです）
        # 1. まずターゲットを表示
        await set_source_visibility(source_name, True)

        # 2. 他を非表示
        for emo_source in set(EMOTION_MAP.values()):
            if emo_source != source_name:
                await set_source_visibility(emo_source, False)
        
        return f"表情変更: {emotion}"
    except Exception as e:
        logger.error(f"Error changing emotion: {e}")
        return f"表情変更エラー: {str(e)}"


async def refresh_media_source(source_name: str, file_path: str) -> bool:
    """
    指定されたメディアソースのファイルを更新し、再生を開始します。
    
    Args:
        source_name: メディアソース名
        file_path: 新しいファイルパス
        
    Returns:
        成功の場合True
    """
    # 接続を確実にする
    if not await connect():
        logger.error(f"Cannot refresh media source '{source_name}': OBS not connected")
        return False
    
    # パスが絶対パスであることを確認
    abs_path = os.path.abspath(file_path)
    
    try:
        # 1. メディアソースの設定を更新
        ws_client.call(obs_requests.SetInputSettings(
            inputName=source_name,
            inputSettings={"local_file": abs_path},
            overlay=True
        ))
        
        # 2. 音量をリセットし、ミュートを解除 (v5 API)
        try:
            ws_client.call(obs_requests.SetInputVolume(inputName=source_name, inputVolumeMul=1.0))
            ws_client.call(obs_requests.SetInputMute(inputName=source_name, inputMuted=False))
        except Exception:
            pass

        # 3. OBSが設定を反映するのをわずかに待つ（高速化のため短縮）
        await asyncio.sleep(0.05)
        
        # 4. 再生をリスタート (v5 API)
        try:
            ws_client.call(obs_requests.TriggerMediaInputAction(
                inputName=source_name,
                mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
            ))
            logger.info(f"Triggered restart for media source '{source_name}'")
        except Exception as e:
            logger.warning(f"Failed to trigger media restart (might be v4 protocol): {e}")
            try:
                ws_client.call(obs_requests.RestartMedia(sourceName=source_name))
            except:
                pass

        logger.info(f"Refreshed media source '{source_name}' with file: {abs_path}")
        return True
    except Exception as e:
        logger.warning(f"Error refreshing media source (first attempt): {e}")
        
        # 接続が切れた可能性があるので再接続を試みる
        await disconnect()
        if await connect():
            try:
                ws_client.call(obs_requests.SetInputSettings(
                    inputName=source_name,
                    inputSettings={"local_file": abs_path},
                    overlay=True
                ))
                logger.info(f"Refreshed media source '{source_name}' on second attempt")
                return True
            except Exception as e2:
                logger.error(f"Error refreshing media source (final attempt): {e2}")
        
        return False


async def start_recording() -> bool:
    """OBSの録画を開始します。"""
    if not await connect():
        return False
    
    try:
        response = ws_client.call(obs_requests.StartRecord())
        if response.status:
            logger.info(f"Started OBS recording: {response.status}")
            return True
        else:
            logger.warning(f"Failed to start OBS recording: {response}")
            return False
    except Exception as e:
        logger.error(f"Error starting OBS recording: {e}")
        return False


async def stop_recording() -> bool:
    """OBSの録画を停止します。"""
    if not await connect():
        return False
    
    try:
        ws_client.call(obs_requests.StopRecord())
        logger.info("Stopped OBS recording")
        return True
    except Exception as e:
        logger.error(f"Error stopping OBS recording: {e}")
        return False


async def get_record_status() -> bool:
    """録画中かどうかを確認します。"""
    if not await connect():
        return False
    
    try:
        response = ws_client.call(obs_requests.GetRecordStatus())
        return response.getOutputActive()
    except Exception as e:
        logger.error(f"Error getting OBS recording status: {e}")
        return False


async def start_streaming(stream_key: str) -> bool:
    """
    OBSのストリーミングを開始します。
    
    Args:
        stream_key: YouTube RTMP stream key
        
    Returns:
        成功の場合True
    """
    if not await connect():
        return False
    
    try:
        # Use custom RTMP for better reliability
        custom_settings = {
            "server": "rtmp://a.rtmp.youtube.com/live2",
            "key": stream_key,
            "use_auth": False
        }
        
        ws_client.call(obs_requests.SetStreamServiceSettings(
            streamServiceType="rtmp_custom",
            streamServiceSettings=custom_settings
        ))
        logger.info(f"Updated OBS stream settings with Custom RTMP and key")
        logger.info(f"Updated OBS stream settings with new key")
        
        # Start streaming
        ws_client.call(obs_requests.StartStream())
        logger.info("Started OBS streaming")
        return True
    except Exception as e:
        logger.error(f"Error starting OBS streaming: {e}")
        return False


async def stop_streaming() -> bool:
    """OBSのストリーミングを停止します。"""
    if not await connect():
        return False
    
    try:
        ws_client.call(obs_requests.StopStream())
        logger.info("Stopped OBS streaming")
        return True
    except Exception as e:
        logger.error(f"Error stopping OBS streaming: {e}")
        return False


async def get_streaming_status() -> bool:
    """ストリーミング中かどうかを確認します。"""
    if not await connect():
        return False
    
    try:
        response = ws_client.call(obs_requests.GetStreamStatus())
        return response.getOutputActive()
    except Exception as e:
        logger.error(f"Error getting OBS streaming status: {e}")
        return False

BGM_SOURCES = {
    "chitchat": "bgm_loop_chitchat",
    "news": "bgm_loop_news",
    "op": "bgm_op",
    "ed": "bgm_ed",
    "se": "bgm_se_transition",
}


async def play_bgm(bgm_id: str, restart: bool = True) -> bool:
    """指定BGMを表示し、必要なら先頭から再生する。"""
    source = BGM_SOURCES.get(bgm_id)
    if source is None:
        logger.warning(f"Unknown bgm_id: {bgm_id}")
        return False
    if not await connect():
        return False

    await set_source_visibility(source, True)
    if restart:
        try:
            ws_client.call(obs_requests.TriggerMediaInputAction(
                inputName=source,
                mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
            ))
        except Exception as e:
            logger.warning(f"Failed to restart bgm '{source}': {e}")
    logger.info(f"play_bgm: {bgm_id} ({source}, restart={restart})")
    return True


async def stop_bgm(bgm_id: str) -> bool:
    """指定BGMを非表示にして停止する。"""
    source = BGM_SOURCES.get(bgm_id)
    if source is None:
        logger.warning(f"Unknown bgm_id: {bgm_id}")
        return False
    ok = await set_source_visibility(source, False)
    if ok:
        logger.info(f"stop_bgm: {bgm_id} ({source})")
    return ok


_BGM_FADE_DURATION = float(os.getenv("BGM_FADE_DURATION", "1.5"))
_BGM_FADE_STEPS = max(1, int(os.getenv("BGM_FADE_STEPS", "15")))

# BGM のフェードイン後の最終音量（dB）。 OBS のソース音量と独立してコード側で制御。
# OBS のソース音量はミキサー絞り、 こちらは「BGM 全体のセリフに対する相対音量」を担当する。
_BGM_TARGET_DB = float(os.getenv("BGM_TARGET_DB", "-15.0"))
_BGM_TARGET_MUL = 10 ** (_BGM_TARGET_DB / 20.0)


def _set_input_volume_sync(source: str, mul: float) -> None:
    """OBS Input の音量倍率を同期で設定する（mul は 0.0-1.0）。"""
    try:
        ws_client.call(obs_requests.SetInputVolume(inputName=source, inputVolumeMul=max(0.0, min(1.0, mul))))
    except Exception as e:
        logger.warning(f"Failed to set volume for {source}: {e}")


async def _fade_volume(source: str, start: float, end: float, duration: float) -> None:
    """指定ソースの音量を start から end まで duration 秒かけて変化させる。"""
    if duration <= 0:
        _set_input_volume_sync(source, end)
        return
    steps = _BGM_FADE_STEPS
    interval = duration / steps
    for i in range(1, steps + 1):
        v = start + (end - start) * (i / steps)
        _set_input_volume_sync(source, v)
        await asyncio.sleep(interval)


async def switch_bgm(to_id: str) -> bool:
    """指定BGMにクロスフェードで切替。 旧 BGM をフェードアウトしながら新 BGM をフェードイン。 SEは触らない。

    フェード時間は環境変数 `BGM_FADE_DURATION`（既定 1.5 秒）、 ステップ数は
    `BGM_FADE_STEPS`（既定 15 = 100ms 間隔）。
    """
    target = BGM_SOURCES.get(to_id)
    if target is None:
        logger.warning(f"Unknown bgm_id: {to_id}")
        return False
    if not await connect():
        return False

    # 旧 BGM ソース（target / SE 以外）をフェードアウト対象として収集
    fade_out_sources = [
        source for bgm_id, source in BGM_SOURCES.items()
        if bgm_id != to_id and bgm_id != "se"
    ]

    # 新 BGM を音量 0 で表示開始 → RESTART で先頭再生
    _set_input_volume_sync(target, 0.0)
    await set_source_visibility(target, True)
    try:
        ws_client.call(obs_requests.TriggerMediaInputAction(
            inputName=target,
            mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
        ))
    except Exception as e:
        logger.warning(f"Failed to restart bgm '{target}': {e}")

    # クロスフェード: 旧 BGM target_mul→0、 新 BGM 0→target_mul を並行で
    fade_tasks = [
        _fade_volume(src, _BGM_TARGET_MUL, 0.0, _BGM_FADE_DURATION)
        for src in fade_out_sources
    ]
    fade_tasks.append(_fade_volume(target, 0.0, _BGM_TARGET_MUL, _BGM_FADE_DURATION))
    await asyncio.gather(*fade_tasks)

    # フェードアウト完了後、 旧 BGM を非表示にして次回再表示時の音量を target_mul に戻す
    for src in fade_out_sources:
        await set_source_visibility(src, False)
        _set_input_volume_sync(src, _BGM_TARGET_MUL)

    logger.info(
        f"switch_bgm: -> {to_id} (cross-fade {_BGM_FADE_DURATION}s, "
        f"target {_BGM_TARGET_DB:+.1f}dB / mul={_BGM_TARGET_MUL:.3f})"
    )
    return True


async def play_se() -> bool:
    """シーン切替SEを単発再生する（再生終了時の自動非表示はOBS側設定に依存）。"""
    return await play_bgm("se", restart=True)


async def switch_scene(scene_name: str) -> bool:
    """OBS のプログラムシーンを切り替える。配信待ち画面・配信本編・終了画面の遷移用。"""
    global _current_scene_name, _source_id_cache
    if not await connect():
        return False
    try:
        ws_client.call(obs_requests.SetCurrentProgramScene(sceneName=scene_name))
        # シーン切替時はソース ID キャッシュをクリア（シーンごとにアイテム ID が異なる）
        _current_scene_name = scene_name
        _source_id_cache = {}
        logger.info(f"switch_scene: -> {scene_name}")
        return True
    except Exception as e:
        logger.error(f"Error switching to scene '{scene_name}': {e}")
        return False


async def play_media_with_emotion(audio_source: str, file_path: str, emotion: str) -> bool:
    """
    音声の再生開始と表情の切り替えを、可能な限り同時に実行します。
    """
    if not await connect():
        return False
        
    abs_path = os.path.abspath(file_path)

    try:
        # --- 準備フェーズ ---
        # 1. 音声ソースを必ず「表示」状態にする（ミキサー消失防止）
        await set_source_visibility(audio_source, True)

        # 2. 音声ファイルの「装填」を済ませる
        ws_client.call(obs_requests.SetInputSettings(
            inputName=audio_source,
            inputSettings={"local_file": abs_path},
            overlay=True
        ))
        
        # 3. 音量/ミュート設定
        try:
            ws_client.call(obs_requests.SetInputVolume(inputName=audio_source, inputVolumeMul=1.0))
            ws_client.call(obs_requests.SetInputMute(inputName=audio_source, inputMuted=False))
        except Exception:
            pass
            
        # 4. OBS側での読み込み完了を待つ (0.1s)
        await asyncio.sleep(0.1)
        
        # --- 発火フェーズ ---
        # 5. イベントフラグをリセット
        _playback_event.clear()

        # 6. 音声再生トリガーを引く
        ws_client.call(obs_requests.TriggerMediaInputAction(
            inputName=audio_source,
            mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
        ))
        
        # 7. OBSから「再生が始まったよ！」というイベントが来るのを待つ（最大5秒）
        # これにより内部のバッファリング時間を完璧に同期させます
        try:
            logger.info("Waiting for OBS playback event...")
            await asyncio.wait_for(_playback_event.wait(), timeout=5.0)
            logger.info(f"Playback event received! Delaying {LIP_SYNC_ADJUST_MS}ms before showing mouth.")
            
            # リップシンク微調整：イベント受信から実際に表示を切り替えるまで待機
            # 映像より音声が遅れる場合はここを増やす
            if LIP_SYNC_ADJUST_MS > 0:
                await asyncio.sleep(LIP_SYNC_ADJUST_MS / 1000.0)
                
            logger.info("Showing mouth movement now.")
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for OBS playback event. Showing mouth anyway.")


        # 8. 表情変更（口パク開始）を実行
        await set_visible_source(emotion)

        return True
    except Exception as e:
        logger.error(f"Error in play_media_with_emotion: {e}")
        return False


def _scene_has_source(scene_name: str, source_name: str) -> bool:
    if not ws_client:
        return False
    try:
        items = ws_client.call(obs_requests.GetSceneItemList(sceneName=scene_name)).getSceneItems()
        return any(it.get("sourceName") == source_name for it in items)
    except Exception as e:
        logger.warning(f"GetSceneItemList failed for '{scene_name}': {e}")
        return False


def _ensure_news_caption_sources() -> bool:
    """kurara_main シーンにキャプション用テキストソース2つを存在保証する。

    既にあれば何もしない。無ければ CreateInput でデフォルト設定で作成する。
    位置・色・フォントサイズはお兄ちゃんがOBSのGUIで微調整する前提なので
    最小設定だけ入れて、見た目はOBS側で整える。
    """
    global _news_caption_initialized
    if _news_caption_initialized:
        return True
    if not ws_client:
        return False

    title_default = {
        "text": "",
        "font": {"face": "Hiragino Sans", "size": 56, "style": "Bold", "flags": 0},
    }
    summary_default = {
        "text": "",
        "font": {"face": "Hiragino Sans", "size": 32, "style": "Regular", "flags": 0},
    }

    for src_name, settings in (
        (NEWS_CAPTION_TITLE_SOURCE, title_default),
        (NEWS_CAPTION_SUMMARY_SOURCE, summary_default),
    ):
        if _scene_has_source(NEWS_CAPTION_SCENE, src_name):
            continue
        try:
            ws_client.call(obs_requests.CreateInput(
                sceneName=NEWS_CAPTION_SCENE,
                inputName=src_name,
                inputKind=NEWS_CAPTION_INPUT_KIND,
                inputSettings=settings,
                sceneItemEnabled=True,
            ))
            logger.info(f"Created news caption source '{src_name}' in scene '{NEWS_CAPTION_SCENE}'")
        except Exception as e:
            logger.warning(f"CreateInput '{src_name}' failed (may already exist as another kind): {e}")

    _news_caption_initialized = True
    return True


async def update_news_caption(title: str, summary: str) -> bool:
    """ニュースキャプションのテキストを更新する。

    title が現在読み上げ中の記事タイトル、summary が3行要約の本文。
    ソースが存在しなければ初回呼び出しで自動作成する。
    """
    if not await connect():
        return False
    try:
        _ensure_news_caption_sources()
        wrapped_title = _wrap_jp(title or "", NEWS_CAPTION_TITLE_WRAP)
        wrapped_summary = _wrap_jp(summary or "", NEWS_CAPTION_SUMMARY_WRAP)
        ws_client.call(obs_requests.SetInputSettings(
            inputName=NEWS_CAPTION_TITLE_SOURCE,
            inputSettings={"text": wrapped_title},
            overlay=True,
        ))
        ws_client.call(obs_requests.SetInputSettings(
            inputName=NEWS_CAPTION_SUMMARY_SOURCE,
            inputSettings={"text": wrapped_summary},
            overlay=True,
        ))
        logger.info(f"News caption updated: title='{title[:30]}...'")
        return True
    except Exception as e:
        logger.error(f"Failed to update news caption: {e}")
        return False


async def clear_news_caption() -> bool:
    """ニュースキャプションを空にする（NEWSフェーズ終了時など）。"""
    return await update_news_caption("", "")
