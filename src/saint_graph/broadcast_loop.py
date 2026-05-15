"""
ニュースキャスター配信のステートマシン。

BroadcastPhase (Enum) と各フェーズのハンドラで構成されます。
各ハンドラは BroadcastContext を受け取り、次の BroadcastPhase を返します。
"""
import asyncio
import os
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import logger, POLL_INTERVAL, MAX_WAIT_CYCLES
from .saint_graph import SaintGraph
from .news_service import NewsService
from .body_client import BodyClient


class BroadcastPhase(Enum):
    """配信のフェーズを表す列挙型。"""
    WAITING = "waiting"   # 視聴者集合タイム（60秒）。 裏で intro / news1 を事前合成
    INTRO   = "intro"     # 開始挨拶
    NEWS    = "news"      # ニュース読み上げ中
    QA      = "qa"        # ニュース終了 → コメント拾いコーナー（促進セリフ＋コメント反応）
    CLOSING = "closing"   # 締めの挨拶 → 配信停止


# WAITING フェーズの滞在時間（秒）。 視聴者集合タイムとして OBS は waiting シーンを表示。
WAITING_DURATION = float(os.getenv("BROADCAST_WAITING_DURATION", "60.0"))


@dataclass
class BroadcastContext:
    """ハンドラ間で共有される配信コンテキスト。"""
    saint_graph: SaintGraph
    news_service: NewsService
    idle_counter: int = 0
    # WAITING フェーズで事前合成した intro / news1 の prepared sentences。
    # 各要素は {"file_path", "duration", "style", "text"} の dict。
    prepared_intro: Optional[List[Dict[str, Any]]] = None
    prepared_news1: Optional[List[Dict[str, Any]]] = None
    # NEWS フェーズで「現 news」「次 news」の lookahead 合成結果を保持する。
    # それぞれ {"item": NewsItem, "sentences": List[...]} の dict。
    # handle_news 入口で next → current に格上げし、 次の合成は再び next に格納。
    prepared_current_news: Optional[Dict[str, Any]] = None
    prepared_next_news: Optional[Dict[str, Any]] = None
    # 最後の news 再生中に裏で合成しておく「news 終わり」「QA 開始」「QA 初手雑談」の prepared。
    prepared_news_finished: Optional[List[Dict[str, Any]]] = None
    prepared_qa_intro: Optional[List[Dict[str, Any]]] = None
    prepared_qa_first: Optional[List[Dict[str, Any]]] = None
    # QA フェーズでの発話回数。qa（コメント促進）と qa_chitchat（自発雑談）を 1:2 で
    # 交互ローテーションするためのカウンタ。
    qa_speak_counter: int = 0
    closing_reason: Optional[str] = None
    phase_scene_initialized: set[BroadcastPhase] = field(default_factory=set)


async def handle_waiting(ctx: BroadcastContext) -> BroadcastPhase:
    """WAITING: 視聴者集合タイム（既定 60秒）。

    OBS は body の start_broadcast で waiting シーンに切替済。 視聴者がまだ集まる
    フェーズなので音声は流さず、 60秒待つ間に裏で intro / news1 の Gemini 生成 +
    TTS 合成（wav 化）まで完了させる。 経過の瞬間に音声を再生できる状態を作る。
    """
    intro_task = asyncio.create_task(_prepare_intro_speech(ctx))
    news1_task = asyncio.create_task(_prepare_news1_speech(ctx))
    wait_task = asyncio.create_task(asyncio.sleep(WAITING_DURATION))

    await asyncio.gather(intro_task, news1_task, wait_task)
    return BroadcastPhase.INTRO


async def _prepare_intro_speech(ctx: BroadcastContext) -> None:
    """intro セリフを Gemini 生成 → TTS 合成して ctx.prepared_intro に格納。"""
    try:
        sentences = await ctx.saint_graph.prepare_intro_text()
        if sentences:
            ctx.prepared_intro = await ctx.saint_graph.prepare_sentences_synth(sentences)
    except Exception as e:
        logger.warning(f"_prepare_intro_speech failed: {e}")


async def _prepare_news1_speech(ctx: BroadcastContext) -> None:
    """news1 セリフを Gemini 生成 → TTS 合成して ctx.prepared_news1 に格納。"""
    item = ctx.news_service.peek_current_item()
    if item is None:
        return
    try:
        sentences = await ctx.saint_graph.prepare_news_reading_text(
            title=item.title, content=item.content
        )
        if sentences:
            ctx.prepared_news1 = await ctx.saint_graph.prepare_sentences_synth(sentences)
    except Exception as e:
        logger.warning(f"_prepare_news1_speech failed: {e}")


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

# 「w」「ｗ」「草」や記号のみのノイズ系コメントを判定するパターン
_COMMENT_NOISE_PATTERN = re.compile(r"^[wｗ草!！?？\s\.,。、ー]+$")
_COMMENT_MIN_LEN = 3
_COMMENT_PICK_MAX = int(os.getenv("COMMENT_PICK_MAX", "3"))


def _filter_meaningful_comments(
    comments_data: List[Dict[str, Any]], max_count: int = _COMMENT_PICK_MAX
) -> List[Dict[str, Any]]:
    """くらら反応用に「有意義なコメント」を最大 max_count 件まで選別する。

    ルール:
    - 文字数 _COMMENT_MIN_LEN 未満は除外
    - "wwww" / "草" / "！？" のみのノイズ系は除外
    - 同じ author の連投は最新1件のみ残す
    - ? / ？ を含む質問系を優先し、それ以外を後ろにつける
    """
    if not comments_data:
        return []

    def _is_question(c: Dict[str, Any]) -> bool:
        msg = c.get("message") or ""
        return "?" in msg or "？" in msg

    # 1. ノイズ・極短コメント除外
    filtered: List[Dict[str, Any]] = []
    for c in comments_data:
        msg = (c.get("message") or "").strip()
        if len(msg) < _COMMENT_MIN_LEN:
            continue
        if _COMMENT_NOISE_PATTERN.match(msg):
            continue
        filtered.append(c)

    # 2. 同 author の重複排除: 質問あり > 質問なし、同種なら最新優先
    by_author: Dict[str, Dict[str, Any]] = {}
    for c in filtered:
        author = c.get("author") or "User"
        existing = by_author.get(author)
        if existing is None:
            by_author[author] = c
            continue
        existing_q = _is_question(existing)
        new_q = _is_question(c)
        # 既存が質問で新規が非質問の場合は既存を残す
        if existing_q and not new_q:
            continue
        # それ以外（新規が質問 or 両方同種）は新規で上書き＝最新優先
        by_author[author] = c
    deduped = list(by_author.values())

    # 3. 質問系を先頭に、その他を後ろに
    questions = [c for c in deduped if _is_question(c)]
    others = [c for c in deduped if not _is_question(c)]

    return (questions + others)[:max_count]


def _extract_action_id(response: Any) -> Optional[str]:
    if isinstance(response, dict):
        action_id = response.get("action_id")
        if isinstance(action_id, str):
            return action_id
    return None




async def _queue_and_wait_strict(ctx: BroadcastContext, queue_call, label: str) -> bool:
    """単一 presentation action を投入し、strict 成功を 1 回 retry 付きで確認する。"""
    for attempt in range(2):
        try:
            response = await queue_call()
            action_id = _extract_action_id(response)
            if not action_id:
                logger.warning(f"{label} did not return action_id")
                await ctx.saint_graph.body.wait_for_queue()
                ok = False
            else:
                ok = await ctx.saint_graph.body.wait_for_queue_strict([action_id])

            if ok:
                return True
            if attempt == 0:
                logger.warning(f"{label} failed on attempt 1; retrying once")
            else:
                logger.warning(f"{label} failed on attempt 2")
        except Exception as e:
            logger.warning(f"{label} error on attempt {attempt + 1}: {e}")

    return False


async def _queue_and_wait_strict_once(ctx: BroadcastContext, queue_call, label: str) -> bool:
    """補助 presentation action を投入し、strict 成功を retry なしで確認する。"""
    try:
        response = await queue_call()
        action_id = _extract_action_id(response)
        if not action_id:
            logger.warning(f"{label} did not return action_id")
            await ctx.saint_graph.body.wait_for_queue()
            return False

        ok = await ctx.saint_graph.body.wait_for_queue_strict([action_id])
        if not ok:
            logger.warning(f"{label} failed")
        return ok
    except Exception as e:
        logger.warning(f"{label} error: {e}")
        return False


async def _queue_scene_switch_strict(
    ctx: BroadcastContext, scene_name: str, label: str
) -> bool:
    return await _queue_and_wait_strict(
        ctx,
        lambda: ctx.saint_graph.body.queue_scene_switch(scene_name),
        label,
    )


async def _queue_caption_clear_strict(ctx: BroadcastContext) -> bool:
    return await _queue_and_wait_strict(
        ctx,
        ctx.saint_graph.body.queue_caption_clear,
        "broadcast startup caption_clear",
    )


async def _poll_and_respond(ctx: BroadcastContext) -> bool:
    """
    コメントをポーリングし、あれば応答します。

    反応 turn の流れは「①コメント作成 (Gemini) → ②音声生成 (MioTTS) → ③音声再生」。
    ① と ② の間は queue が空 / idle 状態なので auto_filler が「考え中」filler を出し、
    ③ で speak action が queue に積まれた瞬間に _auto_filler_loop が
    `if not self._action_queue.empty(): continue` で自動抑制される。
    手動で auto_filler_stop は呼ばないことで QA 中の沈黙感を解消する。

    Returns:
        コメントがあり応答した場合 True
    """
    try:
        comments_data = await ctx.saint_graph.body.get_comments()

        if not comments_data:
            return False

        picked = _filter_meaningful_comments(comments_data)
        if not picked:
            logger.info(f"[poll] {len(comments_data)} comments retrieved but all filtered as noise/short")
            return False

        comments_text = "\n".join(
            f"{c.get('author', 'User')}: {c.get('message', '')}"
            for c in picked
        )
        logger.info(
            f"Comments received ({len(picked)}/{len(comments_data)} picked): {comments_text}"
        )

        # コメント反応セリフ生成・再生中はそのコメントを caption に表示し続ける。
        # title=最初の視聴者名（複数なら「ほか N 名」付与）、 summary=本文のみ（連記時は改行）。
        first_author = picked[0].get("author", "User")
        if len(picked) > 1:
            caption_title = f"{first_author} ほか {len(picked) - 1} 名"
            # 複数 picking 時の summary は本文のみ改行連記（視聴者名の重複表示を避ける）
            caption_summary = "\n".join(c.get("message", "") for c in picked)
        else:
            caption_title = first_author
            caption_summary = picked[0].get("message", "")
        try:
            await ctx.saint_graph.body.set_caption(
                type="comment",
                title=caption_title,
                summary=caption_summary,
            )
        except Exception as e:
            logger.warning(f"Failed to update comment caption: {e}")

        try:
            await ctx.saint_graph.process_turn(comments_text)
        finally:
            # 反応完了後は caption をクリア。 次のニュース読み上げに上書きされる
            # 場合もあるが、 QA フェーズなど後続が無い場合の取り残しを防ぐ。
            try:
                await ctx.saint_graph.body.set_caption(visible=False)
            except Exception as e:
                logger.warning(f"Failed to clear comment caption: {e}")

        return True
    except Exception as e:
        logger.error(f"Error in polling/turn: {e}", exc_info=True)
    return False


# ---------------------------------------------------------------------------
# フェーズハンドラ
# ---------------------------------------------------------------------------

async def handle_intro(ctx: BroadcastContext) -> BroadcastPhase:
    """INTRO: WAITING で事前合成済みの intro / news1 を再生し、NEWS フェーズへ遷移する。

    順序保証のため scene / bgm / content / speak をすべて worker queue に積む。
    視聴者目線:
      1. kurara_main 切替（waiting → kurara）
      2. BGM op（intro 用）
      3. content_image(intro) 表示（kurara の右に intro overlay）
      4. intro speak 再生（事前合成 wav）
      5. content_image を畳む（news1 開始前）
      6. BGM news へ切替
      7. news1 speak 再生（事前合成 wav, caption 同期: タイトル + 要約）
      8. auto_filler 起動（NEWS 以降の沈黙埋め用）

    すべて queue に積むため、 saint_graph 側で wait はしない（順序は body queue が保証）。
    """
    main_scene = os.getenv("BROADCAST_MAIN_SCENE", "kurara_main")

    # 1. kurara_main 切替
    await ctx.saint_graph.body.queue_scene_switch(main_scene)
    # 2. BGM op（intro 用）
    await ctx.saint_graph.body.queue_bgm_switch("op")
    # 3. content_image(intro) 表示
    await ctx.saint_graph.body.queue_content_set(image="intro", visible=True)

    # 4. intro speak（prepared wav, caption なし）
    for sentence in ctx.prepared_intro or []:
        await ctx.saint_graph.body.queue_speak(
            text=sentence.get("text", ""),
            style=sentence.get("style"),
            prepared_wav_path=sentence.get("file_path"),
            prepared_duration=sentence.get("duration"),
        )

    # 5. content_image を畳む（news1 開始前に intro overlay を消す）
    await ctx.saint_graph.body.queue_content_set(image="", visible=False)
    # 6. BGM news へ切替
    await ctx.saint_graph.body.queue_bgm_switch("news")

    # 7. news1 speak（prepared wav, 最初の sentence に caption 同期）
    news1_item = ctx.news_service.peek_current_item()
    if ctx.prepared_news1 and news1_item is not None:
        for i, sentence in enumerate(ctx.prepared_news1):
            await ctx.saint_graph.body.queue_speak(
                text=sentence.get("text", ""),
                style=sentence.get("style"),
                prepared_wav_path=sentence.get("file_path"),
                prepared_duration=sentence.get("duration"),
                caption_title=news1_item.title if i == 0 else None,
                caption_summary=news1_item.content if i == 0 else None,
            )
        # news1 を消化済とみなしてカーソル前進（handle_news は news2 から扱う）
        ctx.news_service.get_next_item()

    # 8. auto_filler は INTRO 完了後に起動する（INTRO 中の chitchat 割り込みを避ける）。
    #    NEWS / QA フェーズの沈黙埋めはここから先で動く。
    try:
        await ctx.saint_graph.body.queue_auto_filler_start()
    except Exception as e:
        logger.warning(f"Failed to queue auto_filler_start: {e}")

    return BroadcastPhase.NEWS


async def handle_news(ctx: BroadcastContext) -> BroadcastPhase:
    """NEWS: prepared wav ベースで news をテンポ良く読み上げる。

    各ループで:
    - ctx.prepared_current_news（無ければ即時生成 + 合成）を queue_speak で投入
      （最初の sentence に caption_title / caption_summary を同期）
    - 裏で「次 news の Gemini 生成 + TTS 合成」を起動 → ctx.prepared_next_news に格納
    - 最後の news の場合は news_finished / qa_intro / qa_first_chitchat の合成も
      裏で起動して ctx.prepared_news_finished / qa_intro / qa_first に保存
    - news_service カーソル前進 + 次ループでは prepared_next_news を current に格上げ

    順序保証は body の worker queue が担う（speak / scene / bgm / content / caption が
    すべて同じ queue を通る）。
    """
    # 入口で「次 news の prepared」を「現 news の prepared」に格上げする。
    if ctx.prepared_next_news is not None:
        ctx.prepared_current_news = ctx.prepared_next_news
        ctx.prepared_next_news = None

    item = ctx.news_service.peek_current_item()
    if item is None:
        return BroadcastPhase.QA

    # 現 news の prepared を取得。 未準備 or item 不一致なら fallback でその場で生成。
    prepared = ctx.prepared_current_news
    if prepared is None or prepared.get("item") is not item:
        sentences = await ctx.saint_graph.prepare_news_reading_text(
            title=item.title, content=item.content
        )
        prepared_sentences = await ctx.saint_graph.prepare_sentences_synth(sentences)
        prepared = {"item": item, "sentences": prepared_sentences}

    # 現 news を queue_speak で順次投入（最初の sentence のみ caption 同期）
    for i, s in enumerate(prepared.get("sentences") or []):
        await ctx.saint_graph.body.queue_speak(
            text=s.get("text", ""),
            style=s.get("style"),
            prepared_wav_path=s.get("file_path"),
            prepared_duration=s.get("duration"),
            caption_title=item.title if i == 0 else None,
            caption_summary=item.content if i == 0 else None,
        )

    # 消化フラグ: news_service のカーソル前進＋ ctx.prepared_current_news クリア
    ctx.news_service.get_next_item()
    ctx.prepared_current_news = None

    # 裏で「次 news」 or 「news 終了系」の合成を進める
    await _preload_after_current_news(ctx)

    if ctx.news_service.peek_current_item() is None and not ctx.news_service.has_next():
        # 次が無い ＝ 最後の news を読み終えた → QA へ
        return BroadcastPhase.QA
    return BroadcastPhase.NEWS


async def _preload_after_current_news(ctx: BroadcastContext) -> None:
    """現 news を読み終えた直後に、 「次 news」 or 「news_finished / qa_intro /
    qa_first_chitchat」の Gemini 生成 + TTS 合成を裏で進める。

    視聴者を待たせないため、 これらの合成は現 news 再生中に並行進行する想定。
    fail-soft: 個別失敗は warning ログのみで継続。
    """
    if ctx.news_service.has_next():
        # 次 news を prepare
        next_item = ctx.news_service.peek_current_item()
        if next_item is None:
            return
        try:
            sentences = await ctx.saint_graph.prepare_news_reading_text(
                title=next_item.title, content=next_item.content
            )
            prepared_sentences = await ctx.saint_graph.prepare_sentences_synth(sentences)
            ctx.prepared_next_news = {"item": next_item, "sentences": prepared_sentences}
        except Exception as e:
            logger.warning(f"prepare next news failed: {e}")
        return

    # 次 news が無い ＝ 直前の current が最後の news → 終わり系を裏で合成
    try:
        finished = await ctx.saint_graph.prepare_news_finished_text()
        if finished:
            ctx.prepared_news_finished = await ctx.saint_graph.prepare_sentences_synth(finished)
    except Exception as e:
        logger.warning(f"prepare news_finished failed: {e}")

    try:
        qa_intro = await ctx.saint_graph.prepare_qa_intro_text()
        if qa_intro:
            ctx.prepared_qa_intro = await ctx.saint_graph.prepare_sentences_synth(qa_intro)
    except Exception as e:
        logger.warning(f"prepare qa_intro failed: {e}")

    try:
        recent_titles = [it.title for it in (ctx.news_service.items or [])]
        qa_first = await ctx.saint_graph.prepare_qa_chitchat_text(
            recent_titles=recent_titles
        )
        if qa_first:
            ctx.prepared_qa_first = await ctx.saint_graph.prepare_sentences_synth(qa_first)
    except Exception as e:
        logger.warning(f"prepare qa_first failed: {e}")


# QA で促進セリフを発する間隔（poll サイクル数）。1cycle = POLL_INTERVAL 秒。
_QA_PROMPT_EVERY = int(os.getenv("BROADCAST_QA_PROMPT_EVERY", "5"))


async def handle_qa(ctx: BroadcastContext) -> BroadcastPhase:
    """QA: コメント拾いコーナー。

    - 初回 entry: content_image(qa, True) を queue し、
      ctx.prepared_news_finished / prepared_qa_intro / prepared_qa_first を順に
      queue_speak で投入する（handle_news が最後の news 再生中に裏で合成済）。
    - ループ: コメント反応 or 自発雑談を行い、 沈黙が MAX_WAIT_CYCLES 続いたら
      CLOSING へ遷移する。
    """
    # 初回 entry の準備系（content_image + 事前合成済 finished/intro/first）
    if BroadcastPhase.QA not in ctx.phase_scene_initialized:
        try:
            await ctx.saint_graph.body.queue_content_set(image="qa", visible=True)
        except Exception as e:
            logger.warning(f"Failed to queue qa content_set: {e}")

        # 最後の news の caption が news_finished speak 中に残らないよう、 先に
        # caption clear を queue。 body queue は順次処理のため news_finished
        # speak の前に確実に caption が消える。
        try:
            await ctx.saint_graph.body.queue_caption_clear()
        except Exception as e:
            logger.warning(f"Failed to queue caption clear at QA entry: {e}")

        for sentence in ctx.prepared_news_finished or []:
            await ctx.saint_graph.body.queue_speak(
                text=sentence.get("text", ""),
                style=sentence.get("style"),
                prepared_wav_path=sentence.get("file_path"),
                prepared_duration=sentence.get("duration"),
            )
        for sentence in ctx.prepared_qa_intro or []:
            await ctx.saint_graph.body.queue_speak(
                text=sentence.get("text", ""),
                style=sentence.get("style"),
                prepared_wav_path=sentence.get("file_path"),
                prepared_duration=sentence.get("duration"),
            )
        for sentence in ctx.prepared_qa_first or []:
            await ctx.saint_graph.body.queue_speak(
                text=sentence.get("text", ""),
                style=sentence.get("style"),
                prepared_wav_path=sentence.get("file_path"),
                prepared_duration=sentence.get("duration"),
            )

        ctx.prepared_news_finished = None
        ctx.prepared_qa_intro = None
        ctx.prepared_qa_first = None
        ctx.phase_scene_initialized.add(BroadcastPhase.QA)
        return BroadcastPhase.QA

    # 沈黙が続いたら CLOSING
    if ctx.idle_counter >= MAX_WAIT_CYCLES:
        logger.info(
            f"Silence timeout ({MAX_WAIT_CYCLES} cycles) reached in QA. Closing."
        )
        return BroadcastPhase.CLOSING

    # コメント反応 or 自発雑談
    if await _poll_and_respond(ctx):
        ctx.idle_counter = 0
        return BroadcastPhase.QA

    ctx.idle_counter += 1

    # 一定サイクル毎に発話。3 回中 1 回はコメント促進（qa）、残り 2 回は自発雑談（qa_chitchat）。
    if ctx.idle_counter % _QA_PROMPT_EVERY == 1:
        ctx.qa_speak_counter += 1
        if ctx.qa_speak_counter % 3 == 0:
            try:
                await ctx.saint_graph.process_qa()
            except Exception as e:
                logger.warning(f"Failed to run QA prompt: {e}")
        else:
            recent_titles = (
                [item.title for item in ctx.news_service.items[-3:]]
                if getattr(ctx.news_service, "items", None) else None
            )
            try:
                await ctx.saint_graph.process_qa_chitchat(recent_titles=recent_titles)
            except Exception as e:
                logger.warning(f"Failed to run QA chitchat: {e}")

    return BroadcastPhase.QA


async def handle_closing(ctx: BroadcastContext) -> BroadcastPhase:
    """CLOSING: 〆コメント（事前録音 wav）→ QA 画像非表示 → ending BGM → ending シーン
    → end 画像表示 → 60s 余韻 → ループ終了。

    すべて worker queue を通して順序保証する。 auto_filler は CLOSING 突入時に停止
    （closing 再生 / 60s 余韻に chitchat が割り込むのを防ぐ）。
    """
    # auto_filler 停止
    try:
        await ctx.saint_graph.body.queue_auto_filler_stop()
    except Exception as e:
        logger.warning(f"Failed to queue auto_filler_stop at CLOSING: {e}")

    # 〆コメント（事前録音 wav）を再生。 プール (`CLOSING_POOL_DIR`) からランダム選択。
    closings_dir = Path(os.getenv("CLOSING_POOL_DIR", "data/mind/kurara/closings"))
    candidates = (
        sorted(closings_dir.glob("closing_*.wav")) if closings_dir.exists() else []
    )
    if candidates:
        chosen = random.choice(candidates)
        logger.info(f"Closing: playing pre-generated wav: {chosen.name}")
        try:
            await ctx.saint_graph.body.queue_filler(
                file_path=str(chosen), style="joyful"
            )
        except Exception as e:
            logger.warning(f"Closing pool playback failed: {e}")
    else:
        logger.info("Closing pool empty; skip wav playback (queue order is still preserved)")

    # QA 画像非表示（〆 wav が終わった頃に画面から QA overlay が消える）
    try:
        await ctx.saint_graph.body.queue_content_set(image="", visible=False)
    except Exception as e:
        logger.warning(f"Failed to queue qa content hide: {e}")

    # ending BGM へ切替
    try:
        await ctx.saint_graph.body.queue_bgm_switch("ed")
    except Exception as e:
        logger.warning(f"Failed to queue bgm 'ed': {e}")

    # ending シーン切替
    ending_scene = os.getenv("BROADCAST_ENDING_SCENE", "ending")
    try:
        await ctx.saint_graph.body.queue_scene_switch(ending_scene)
    except Exception as e:
        logger.warning(f"Failed to queue ending scene_switch: {e}")

    # end 画像表示（ending シーンに移ってから表示）
    try:
        await ctx.saint_graph.body.queue_content_set(image="end", visible=True)
    except Exception as e:
        logger.warning(f"Failed to queue end content_set: {e}")

    # 余韻 sleep（環境変数で 0 にすると即終了）
    ending_duration = float(os.getenv("BROADCAST_ENDING_DURATION", "60"))
    if ending_duration > 0:
        logger.info(f"Holding ending scene with BGM for {ending_duration}s before exit")
        await asyncio.sleep(ending_duration)

    return None  # ループ終了


# ---------------------------------------------------------------------------
# ディスパッチテーブル & メインループ
# ---------------------------------------------------------------------------

_HANDLERS = {
    BroadcastPhase.WAITING: handle_waiting,
    BroadcastPhase.INTRO:   handle_intro,
    BroadcastPhase.NEWS:    handle_news,
    BroadcastPhase.QA:      handle_qa,
    BroadcastPhase.CLOSING: handle_closing,
}

# フェーズと BGM の対応。 obs_adapter.BGM_SOURCES の bgm_id と一致させる。
_PHASE_BGM = {
    # WAITING / INTRO / NEWS / CLOSING の BGM は各ハンドラ内で queue 経由で切替するため None。
    # WAITING は OBS の waiting シーン側で chitchat BGM を流す前提（body は触らない）。
    # INTRO は handle_intro 冒頭で op → 終盤で news を queue に積む。
    # CLOSING は handle_closing 内で〆 wav 再生後に ed BGM を queue に積む。
    BroadcastPhase.WAITING: None,
    BroadcastPhase.INTRO:   None,
    BroadcastPhase.NEWS:    None,
    BroadcastPhase.QA:      "chitchat",
    BroadcastPhase.CLOSING: None,
}

async def _switch_bgm_for_phase(
    ctx: BroadcastContext, phase: BroadcastPhase, *, with_se: bool = False
) -> None:
    """フェーズに対応する BGM へクロスフェードで切替する。失敗してもループは継続させる。

    BGM 自体が `obs_adapter.switch_bgm` 内で BGM_FADE_DURATION 秒のクロスフェード
    （旧 BGM フェードアウト ＋ 新 BGM フェードイン並行）を行うため、 シーン切替 SE は
    挟まない。 互換のため `with_se` 引数は残しているが無視する。
    """
    bgm_id = _PHASE_BGM.get(phase)
    if not bgm_id:
        return
    try:
        await ctx.saint_graph.body.switch_bgm(bgm_id)
    except Exception as e:
        logger.warning(f"Failed to switch BGM for phase {phase.value}: {e}")


async def run_broadcast_loop(ctx: BroadcastContext) -> None:
    """
    ステートマシンのメインループ。

    WAITING から始まり、各ハンドラが返す次フェーズに従って遷移します。
    フェーズ遷移時に対応 BGM へ切り替えます。
    ハンドラが None を返すとループを終了します。
    """
    phase = BroadcastPhase.WAITING
    logger.info("Entering Broadcast Loop (state machine)...")

    try:
        if not await _queue_caption_clear_strict(ctx):
            ctx.closing_reason = "technical_failure"
            phase = BroadcastPhase.CLOSING
        else:
            # 雑談セリフを body-streamer に登録（auto-filler が idle 時に混ぜる）。
            # auto_filler 自体の起動は handle_intro 末尾まで遅延させて
            # INTRO 挨拶への chitchat 割り込みを防ぐ。
            await ctx.saint_graph.register_chitchat()

            # WAITING フェーズの BGM は OBS の waiting シーンに紐付いた音源を流す
            # 設計（body 側からは触らない）。 _switch_bgm_for_phase は no-op。

        while phase is not None:
            try:
                handler = _HANDLERS[phase]
                next_phase = await handler(ctx)

                if next_phase is not None:
                    if next_phase != phase:
                        logger.info(f"Phase transition: {phase.value} -> {next_phase.value}")
                        # フェーズ遷移時はシーン切替 SE を入れて BGM を切り替える
                        await _switch_bgm_for_phase(ctx, next_phase, with_se=True)
                    phase = next_phase
                    await asyncio.sleep(POLL_INTERVAL)
                else:
                    # CLOSING ハンドラが None を返した → 終了
                    logger.info(f"Phase {phase.value} completed. Exiting loop.")
                    phase = None

            except Exception as e:
                logger.error(f"Unexpected error in phase {phase.value}: {e}", exc_info=True)
                await asyncio.sleep(5)
            except BaseException as e:
                logger.critical(f"Critical System Error in phase {phase.value}: {e}", exc_info=True)
                raise
    finally:
        await _queue_and_wait_strict_once(
            ctx,
            ctx.saint_graph.body.queue_auto_filler_stop,
            "broadcast shutdown auto_filler_stop",
        )
