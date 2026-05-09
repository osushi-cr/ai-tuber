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
    INTRO   = "intro"     # 開始挨拶
    NEWS    = "news"      # ニュース読み上げ中
    QA      = "qa"        # ニュース終了 → コメント拾いコーナー（促進セリフ＋コメント反応）
    CLOSING = "closing"   # 締めの挨拶 → 配信停止


@dataclass
class BroadcastContext:
    """ハンドラ間で共有される配信コンテキスト。"""
    saint_graph: SaintGraph
    news_service: NewsService
    idle_counter: int = 0
    # 次ニュースのプリフェッチ task（Gemini 生成済みセリフだけを保持し、speak は積まない）
    next_news_task: Optional[asyncio.Task[List[Tuple[str, str]]]] = None
    # handle_intro で news1 を speak まで先回り投入したときの action_ids と原ニュース。
    # handle_news 冒頭でこれを検知して二重投入を回避し、再生完了待ち＋次 prefetch 仕込みだけ行う。
    preloaded_news_action_ids: Optional[List[str]] = None
    preloaded_news_item: Optional[Any] = None
    # QA フェーズでの発話回数。qa（コメント促進）と qa_chitchat（自発雑談）を 1:2 で
    # 交互ローテーションするためのカウンタ。
    qa_speak_counter: int = 0
    closing_reason: Optional[str] = None
    phase_scene_initialized: set[BroadcastPhase] = field(default_factory=set)


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


async def _preload_next_news(ctx: BroadcastContext) -> None:
    """次のニュースを Gemini 取得→speak action 先行 enqueue まで一気に進める。

    現ニュース再生中に裏で呼ぶことで、body の「speak enqueue 時点で TTS 合成 task 背景開始」
    機構が活きる。enqueue が早いほど現再生終了時点で次合成が完了している割合が増え、
    ニュース間の沈黙が縮む。失敗してもループは継続させる（次ループの通常ルートで再試行）。
    """
    if not ctx.news_service.has_next():
        return
    next_item = ctx.news_service.peek_current_item()
    if next_item is None:
        return

    logger.info(f"Pre-queueing next news during current playback: {next_item.title}")
    try:
        next_sentences = await ctx.saint_graph.prepare_news_reading_text(
            title=next_item.title, content=next_item.content
        )
        if not next_sentences:
            logger.warning("next news prefetch returned empty sentences")
            return
        next_action_ids = await ctx.saint_graph.play_prepared_sentences_with_caption(
            next_sentences,
            caption_title=next_item.title,
            caption_summary=next_item.content,
            wait_after=False,
        )
        if not next_action_ids:
            logger.warning("next news pre-queue did not return action_ids")
            return
        ctx.preloaded_news_action_ids = next_action_ids
        ctx.preloaded_news_item = next_item
    except Exception as e:
        logger.warning(f"Pre-queue next news failed: {e}")


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
    """INTRO: 配信開始の挨拶を行い、NEWS フェーズへ遷移する。

    演出順序:
        1. waiting シーン切替 (best-effort) ＋ chitchat BGM
        2. news1 prefetch をバックグラウンドで開始
        3. waiting 中に prepare_intro_text() で Gemini からテキストだけ取得
           （TTS / 発話キュー投入はまだしない）
        4. kurara_main シーン strict 切替
        5. op (intro) BGM へ切替
        6. play_prepared_sentences() で TTS + 再生を speak queue へ投入し、
           completed まで strict 待ち
        7. auto_filler 起動（NEWS 以降の沈黙埋め用）
        8. NEWS フェーズへ

    Gemini 応答取得（重い）は waiting 中に済ませ、 TTS + 再生は kurara_main 切替後の
    キューに乗せる。 worker キューは順次処理なので「scene 切替 → 挨拶 TTS → 再生」
    の順序が確実に保たれ、 視聴者には kurara_main で挨拶が始まるように見える。

    auto_filler は INTRO 完了後に起動するため、 INTRO 中に chitchat が割り込まない。
    """
    # waiting scene は配信開始前演出のための補助 scene で、 失敗しても fatal ではない。
    waiting_scene = os.getenv("BROADCAST_WAITING_SCENE", "waiting")
    await _queue_and_wait_strict_once(
        ctx,
        lambda: ctx.saint_graph.body.queue_scene_switch(waiting_scene),
        "INTRO start waiting_scene",
    )

    # waiting 中の BGM。 失敗しても致命ではない。
    try:
        await ctx.saint_graph.body.switch_bgm("chitchat")
    except Exception as e:
        logger.warning(f"Failed to switch waiting BGM (chitchat): {e}")

    # news1 prefetch をバックグラウンドで仕込む（INTRO テキスト取得と並行）
    if ctx.news_service.has_next():
        first_item = ctx.news_service.peek_current_item()
        if first_item:
            logger.info(f"Prefetching news1: {first_item.title}")
            ctx.next_news_task = asyncio.create_task(
                ctx.saint_graph.prepare_news_reading_text(
                    title=first_item.title, content=first_item.content
                )
            )

    # waiting 中に Gemini で挨拶テキストを取得（TTS と発話キュー投入はしない）。
    # ここが重い処理（Gemini API + retry）なので chitchat BGM で見せる。
    intro_sentences = await ctx.saint_graph.prepare_intro_text()

    # news1 prefetch も完了するまで waiting で待つ（演出時間の確保）。
    # gemini-3.1-flash-lite は intro/news が両方とも数秒で返るため、待たないと waiting
    # シーンの chitchat BGM が一瞬で終わってしまう。最大 15s で打ち切り、間に合わなければ
    # NEWS フェーズで通常の prefetch 待ちに任せる。
    if ctx.next_news_task is not None and not ctx.next_news_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(ctx.next_news_task), timeout=15.0)
        except asyncio.TimeoutError:
            logger.info("news1 prefetch did not finish within waiting window (15s); proceeding")
        except Exception as e:
            logger.warning(f"news1 prefetch errored during waiting wait: {e}")

    # kurara_main へ strict 切替（挨拶はキャラ画面で流す）
    main_scene = os.getenv("BROADCAST_MAIN_SCENE", "kurara_main")
    ok = await _queue_scene_switch_strict(
        ctx,
        main_scene,
        "INTRO end scene_switch",
    )
    if not ok:
        ctx.closing_reason = "technical_failure"
        return BroadcastPhase.CLOSING

    # kurara_main に切替えてから op BGM
    try:
        await ctx.saint_graph.body.switch_bgm("op")
    except Exception as e:
        logger.warning(f"Failed to switch INTRO BGM (op): {e}")

    # intro 画像 overlay をくららの右に表示（挨拶中ずっと出す）
    try:
        await ctx.saint_graph.body.set_content_image(image="intro")
    except Exception as e:
        logger.warning(f"Failed to set intro content image: {e}")

    # 取得済セリフを TTS + 再生 queue に投入。 worker は kurara_main 切替後に
    # この speak action を処理するため、 kurara_main 画面で挨拶が始まる。
    if intro_sentences:
        intro_action_ids = await ctx.saint_graph.play_prepared_sentences(
            intro_sentences, wait_after=False
        )

        # news1 voice 先回り投入: intro 再生中に news1 の合成を裏で進める。
        # body の改修🅐 で speak は enqueue 時点で合成 task が背景開始するため、
        # intro 再生終了時には news1 合成が完了済 → ギャップなしで news1 が始まる。
        news1_item = ctx.news_service.peek_current_item()
        if news1_item is not None and ctx.next_news_task is not None:
            try:
                news1_sentences = await ctx.next_news_task
            except Exception as e:
                logger.warning(f"news1 prefetch failed during intro queueing: {e}")
                news1_sentences = None
            ctx.next_news_task = None

            if news1_sentences:
                logger.info(f"Pre-queueing news1 speak during intro: {news1_item.title}")
                news1_action_ids = await ctx.saint_graph.play_prepared_sentences_with_caption(
                    news1_sentences,
                    caption_title=news1_item.title,
                    caption_summary=news1_item.content,
                    wait_after=False,
                )
                ctx.preloaded_news_action_ids = news1_action_ids
                ctx.preloaded_news_item = news1_item

        if intro_action_ids:
            await ctx.saint_graph.body.wait_for_queue_strict(
                action_ids=intro_action_ids
            )

    # 挨拶完了。 NEWS フェーズに渡す前に intro 画像を畳む。
    try:
        await ctx.saint_graph.body.set_content_image(visible=False)
    except Exception as e:
        logger.warning(f"Failed to clear intro content image: {e}")

    # auto_filler は INTRO 完了後に起動する（INTRO 中の chitchat 割り込みを避ける）。
    # NEWS / QA フェーズの沈黙埋めはここから先で動く。
    await _queue_and_wait_strict_once(
        ctx,
        ctx.saint_graph.body.queue_auto_filler_start,
        "INTRO end auto_filler_start",
    )

    return BroadcastPhase.NEWS


async def handle_news(ctx: BroadcastContext) -> BroadcastPhase:
    """
    NEWS: ニュースをテンポ良く読み上げる。 コメント反応は **次ニュースの
    prefetch が完了していないとき限定** で時間稼ぎとして挟む。

    プリフェッチ最適化:
    - 現ニュースのセリフは ctx.next_news_task に既に生成されているはず（無ければ即時生成）
    - 現ニュース再生開始前に「次のニュース」のセリフ生成 task をキック
    - 再生完了と caption/音声の成否を wait_for_queue_strict で確認 → 次ループへ

    コメント反応の挟み込み方針:
    - 次ニュースの prefetch task が完了していれば → コメント拾いをスキップして即ニュース
    - 次ニュースの prefetch task が進行中なら → 待ち時間にコメントを返す（時間稼ぎ）
    - ニュース全消化後は QA フェーズに遷移してコメント反応コーナーへ

    ニュースを全消化したら QA へ遷移する。
    """
    # 先行投入済みのニュース（intro または前ループ末尾の _preload_next_news で enqueue 済）が
    # あれば、それを再生して、再生中に「次の次」を先行 enqueue する。これで全 news が連鎖する。
    # scene/caption は先行投入時に同梱済なので追加処理は不要。
    if ctx.preloaded_news_action_ids is not None:
        item = ctx.preloaded_news_item
        action_ids = ctx.preloaded_news_action_ids
        ctx.preloaded_news_action_ids = None
        ctx.preloaded_news_item = None

        logger.info(f"Reading pre-queued news: {item.title if item else '(unknown)'}")

        # 現ニュースの index を進める（peek 済みアイテムを advance）
        ctx.news_service.get_next_item()

        # 次ニュースを先行 enqueue（現再生中に裏で Gemini 取得＋ TTS 合成 task が裏で進む）
        preload_task = asyncio.create_task(_preload_next_news(ctx))

        ok = await ctx.saint_graph.body.wait_for_queue_strict(action_ids=action_ids)
        if not ok:
            preload_task.cancel()
            try:
                await preload_task
            except (asyncio.CancelledError, Exception):
                pass
            ctx.closing_reason = "technical_failure"
            return BroadcastPhase.CLOSING

        # 並走させた preload を念のため待つ（既に終わっていれば即 return）
        try:
            await preload_task
        except Exception as e:
            logger.warning(f"Preload task error after current playback: {e}")

        try:
            await ctx.saint_graph.body.play_filler("aizuchi")
        except Exception as e:
            logger.warning(f"Failed to queue filler between news items: {e}")

        return BroadcastPhase.NEWS

    # ニュース全消化 → QA（コメント拾いコーナー）へ
    if not ctx.news_service.has_next():
        logger.info("All news items read. Moving to QA (comment corner).")
        try:
            await ctx.saint_graph.body.clear_news_caption()
        except Exception as e:
            logger.warning(f"Failed to clear news caption: {e}")
        await ctx.saint_graph.process_news_finished()
        return BroadcastPhase.QA

    # 次ニュースの prefetch が終わっていなければ、 待ち時間にコメント反応で繋ぐ。
    # done() なら即ニュース読み上げに進む（コメントは QA フェーズで拾う）。
    next_ready = (
        ctx.next_news_task is not None and ctx.next_news_task.done()
    )
    if not next_ready:
        if await _poll_and_respond(ctx):
            ctx.idle_counter = 0
            return BroadcastPhase.NEWS

    item = ctx.news_service.peek_current_item()
    if not item:
        return BroadcastPhase.QA

    logger.info(f"Reading news item: {item.title}")

    # 現ニュース: prefetch 済 task があれば生成結果を使い、無ければ即時生成
    if ctx.next_news_task is not None:
        try:
            sentences = await ctx.next_news_task
        except Exception as e:
            logger.warning(f"Prefetched news task failed, falling back to inline generation: {e}")
            sentences = await ctx.saint_graph.prepare_news_reading_text(
                title=item.title, content=item.content
            )
        ctx.next_news_task = None
    else:
        sentences = await ctx.saint_graph.prepare_news_reading_text(
            title=item.title, content=item.content
        )

    # 現ニュースの index を進める
    ctx.news_service.get_next_item()

    # 現ニュース scene 切替（strict）
    main_scene = os.getenv("BROADCAST_MAIN_SCENE", "kurara_main")
    if not await _queue_scene_switch_strict(ctx, main_scene, "NEWS entry scene_switch"):
        ctx.closing_reason = "technical_failure"
        return BroadcastPhase.CLOSING

    # 現ニュースを speak action queue に enqueue（wait なし）
    try:
        current_action_ids = await ctx.saint_graph.play_prepared_sentences_with_caption(
            sentences,
            caption_title=item.title,
            caption_summary=item.content,
            wait_after=False,
        )
        if not current_action_ids:
            logger.warning("NEWS speak did not return action_id")
            ctx.closing_reason = "technical_failure"
            return BroadcastPhase.CLOSING
    except Exception as e:
        logger.warning(f"NEWS speak/caption action error: {e}")
        ctx.closing_reason = "technical_failure"
        return BroadcastPhase.CLOSING

    # 次ニュースを先行 enqueue（現再生中に裏で Gemini 取得＋ TTS 合成 task が裏で進む）
    preload_task = asyncio.create_task(_preload_next_news(ctx))

    # 現ニュースの再生完了を strict に確認する（speak は retry なし、部分再生→ retry 二重発話回避）
    ok = await ctx.saint_graph.body.wait_for_queue_strict(current_action_ids)
    if not ok:
        logger.warning("NEWS speak/caption action failed (no retry; falling back to CLOSING)")
        preload_task.cancel()
        try:
            await preload_task
        except (asyncio.CancelledError, Exception):
            pass
        ctx.closing_reason = "technical_failure"
        return BroadcastPhase.CLOSING

    # 並走させた preload を念のため待つ（既に終わっていれば即 return）
    try:
        await preload_task
    except Exception as e:
        logger.warning(f"Preload task error after current playback: {e}")

    # ニュース完了 → aizuchi 系 filler を 1 個積んで次のセリフ生成中の沈黙を埋める
    try:
        await ctx.saint_graph.body.play_filler("aizuchi")
    except Exception as e:
        logger.warning(f"Failed to queue filler between news items: {e}")

    return BroadcastPhase.NEWS


# QA で促進セリフを発する間隔（poll サイクル数）。1cycle = POLL_INTERVAL 秒。
_QA_PROMPT_EVERY = int(os.getenv("BROADCAST_QA_PROMPT_EVERY", "5"))


async def handle_qa(ctx: BroadcastContext) -> BroadcastPhase:
    """
    QA: コメント拾いコーナー。コメントがあれば反応し、無いときは
    `_QA_PROMPT_EVERY` サイクル毎に「コメント募集」促進セリフを発する。
    沈黙が `MAX_WAIT_CYCLES` 続いたら CLOSING へ遷移する。
    """
    if BroadcastPhase.QA not in ctx.phase_scene_initialized:
        main_scene = os.getenv("BROADCAST_MAIN_SCENE", "kurara_main")
        ok = await _queue_scene_switch_strict(
            ctx,
            main_scene,
            "QA entry scene_switch",
        )
        if not ok:
            ctx.closing_reason = "technical_failure"
            return BroadcastPhase.CLOSING
        ctx.phase_scene_initialized.add(BroadcastPhase.QA)

        # QA 画像 overlay をくららの右に表示（QA 中ずっと出す）。
        # CLOSING に抜ける際は handle_closing 冒頭で end 画像へ上書きされる。
        try:
            await ctx.saint_graph.body.set_content_image(image="qa")
        except Exception as e:
            logger.warning(f"Failed to set qa content image: {e}")

    if await _poll_and_respond(ctx):
        ctx.idle_counter = 0
        return BroadcastPhase.QA

    ctx.idle_counter += 1
    if ctx.idle_counter > MAX_WAIT_CYCLES:
        logger.info(
            f"Silence timeout ({MAX_WAIT_CYCLES} cycles) reached in QA. Closing."
        )
        return BroadcastPhase.CLOSING

    # 一定サイクル毎に発話。3 回中 1 回はコメント促進（qa）、残り 2 回は自発雑談（qa_chitchat）。
    # 雑談優位にすることで「コメント来てね」連発の単調さを避け、配信が自然に流れる。
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
    """CLOSING: 事前生成 closing wav プールからランダム選択して再生する。

    プール (`CLOSING_POOL_DIR` / data/mind/kurara/closings/closing_*.wav) が
    空の場合は従来通り Gemini で生成して再生する。
    None を返しループ終了。

    auto_filler は CLOSING 突入時に停止する。 closing speech / ending 60s
    の余韻に chitchat が割り込むのを防ぐ。
    """
    # auto_filler を即停止（chitchat 割り込み防止）。 失敗しても致命ではない。
    try:
        await ctx.saint_graph.body.queue_auto_filler_stop()
    except Exception as e:
        logger.warning(f"Failed to queue auto_filler_stop at CLOSING: {e}")

    # end 画像 overlay をくららの右に表示（closing pool 再生中のみ。
    # ending シーン切替直前に clear する）。 QA 画像は上書きされる。
    try:
        await ctx.saint_graph.body.set_content_image(image="end")
    except Exception as e:
        logger.warning(f"Failed to set end content image: {e}")

    closings_dir = Path(os.getenv("CLOSING_POOL_DIR", "data/mind/kurara/closings"))
    candidates = (
        sorted(closings_dir.glob("closing_*.wav")) if closings_dir.exists() else []
    )

    if candidates:
        chosen = random.choice(candidates)
        logger.info(f"Closing: playing pre-generated wav: {chosen.name}")
        try:
            result = await ctx.saint_graph.body.queue_filler(
                file_path=str(chosen), style="joyful"
            )
            action_id = (
                result.get("action_id") if isinstance(result, dict) else None
            )
            if action_id:
                await ctx.saint_graph.body.wait_for_queue_strict(
                    action_ids=[action_id]
                )
        except Exception as e:
            logger.warning(
                f"Closing pool playback failed ({e}), falling back to Gemini"
            )
            await ctx.saint_graph.process_closing(reason=ctx.closing_reason)
    else:
        logger.info("Closing pool not found, generating via Gemini")
        await ctx.saint_graph.process_closing(reason=ctx.closing_reason)

    # ending シーン切替前に end 画像 overlay を畳む（ending では画像出さない）。
    try:
        await ctx.saint_graph.body.set_content_image(visible=False)
    except Exception as e:
        logger.warning(f"Failed to clear end content image: {e}")

    # 配信終了画面へシーン切替（ending イラスト＋BGM）
    ending_scene = os.getenv("BROADCAST_ENDING_SCENE", "ending")
    await _queue_and_wait_strict_once(
        ctx,
        lambda: ctx.saint_graph.body.queue_scene_switch(ending_scene),
        "CLOSING ending scene_switch",
    )

    # ending BGM (ed) を一定時間流して余韻を残してから配信終了。
    # CLOSING フェーズ突入時に _switch_bgm_for_phase で既に "ed" が再生されているので、
    # ここでは指定秒数だけ画面を保持するだけで良い。
    ending_duration = float(os.getenv("BROADCAST_ENDING_DURATION", "60"))
    if ending_duration > 0:
        logger.info(f"Holding ending scene with BGM for {ending_duration}s before exit")
        await asyncio.sleep(ending_duration)

    return None  # ループ終了のシグナル


# ---------------------------------------------------------------------------
# ディスパッチテーブル & メインループ
# ---------------------------------------------------------------------------

_HANDLERS = {
    BroadcastPhase.INTRO:   handle_intro,
    BroadcastPhase.NEWS:    handle_news,
    BroadcastPhase.QA:      handle_qa,
    BroadcastPhase.CLOSING: handle_closing,
}

# フェーズと BGM の対応。 obs_adapter.BGM_SOURCES の bgm_id と一致させる。
_PHASE_BGM = {
    # INTRO は handle_intro 内で waiting=chitchat → kurara_main=op を動的切替するため None。
    # run_broadcast_loop 冒頭の自動 BGM 切替はスキップさせる。
    BroadcastPhase.INTRO:   None,
    BroadcastPhase.NEWS:    "news",
    BroadcastPhase.QA:      "chitchat",
    BroadcastPhase.CLOSING: "ed",
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


async def _cancel_pending_tasks(ctx: BroadcastContext) -> None:
    """ループ終了時に未完了の prefetch task をキャンセルしてリーク防止する。"""
    if ctx.next_news_task is not None and not ctx.next_news_task.done():
        ctx.next_news_task.cancel()
        try:
            await ctx.next_news_task
        except (asyncio.CancelledError, Exception):
            pass
    ctx.next_news_task = None


async def run_broadcast_loop(ctx: BroadcastContext) -> None:
    """
    ステートマシンのメインループ。

    INTRO から始まり、各ハンドラが返す次フェーズに従って遷移します。
    フェーズ遷移時に対応 BGM へ切り替えます。
    ハンドラが None を返すとループを終了します。
    """
    phase = BroadcastPhase.INTRO
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

            # 配信開始時の最初のフェーズ（INTRO）BGM は handle_intro 内で
            # waiting=chitchat → kurara_main=op の順に動的切替する。 ここでは
            # _switch_bgm_for_phase を呼ばない（_PHASE_BGM[INTRO] が None のため
            # no-op だが、 意図を明確にする）。

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
        await _cancel_pending_tasks(ctx)
