"""
ニュースキャスター配信のステートマシン。

BroadcastPhase (Enum) と各フェーズのハンドラで構成されます。
各ハンドラは BroadcastContext を受け取り、次の BroadcastPhase を返します。
"""
import asyncio
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

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
    # 次ニュースのプリフェッチ task（speak キュー投入まで完了させて保持）
    next_news_task: Optional[asyncio.Task] = None


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

    # 1. ノイズ・極短コメント除外
    filtered: List[Dict[str, Any]] = []
    for c in comments_data:
        msg = (c.get("message") or "").strip()
        if len(msg) < _COMMENT_MIN_LEN:
            continue
        if _COMMENT_NOISE_PATTERN.match(msg):
            continue
        filtered.append(c)

    # 2. 同 author は最新（後勝ち）の1件にまとめる
    by_author: Dict[str, Dict[str, Any]] = {}
    for c in filtered:
        author = c.get("author") or "User"
        by_author[author] = c
    deduped = list(by_author.values())

    # 3. 質問系を先頭に、その他を後ろに
    questions = [
        c for c in deduped
        if "?" in (c.get("message") or "") or "？" in (c.get("message") or "")
    ]
    others = [c for c in deduped if c not in questions]

    return (questions + others)[:max_count]


async def _poll_and_respond(ctx: BroadcastContext) -> bool:
    """
    コメントをポーリングし、あれば応答します。

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
        await ctx.saint_graph.process_turn(comments_text)
        return True
    except Exception as e:
        logger.error(f"Error in polling/turn: {e}", exc_info=True)
    return False


# ---------------------------------------------------------------------------
# フェーズハンドラ
# ---------------------------------------------------------------------------

async def handle_intro(ctx: BroadcastContext) -> BroadcastPhase:
    """INTRO: 配信開始の挨拶を行い、NEWS フェーズへ遷移する。

    intro→news1 間の沈黙を最小化するため、intro セリフ生成・再生と並行して
    news1 のセリフ生成（speak キュー投入まで）を先取りする。
    生成済 task は ctx.next_news_task に乗せて handle_news に引き継ぐ。
    """
    intro_task = asyncio.create_task(ctx.saint_graph.process_intro())

    if ctx.news_service.has_next():
        first_item = ctx.news_service.peek_current_item()
        if first_item:
            logger.info(f"Prefetching news1: {first_item.title}")
            ctx.next_news_task = asyncio.create_task(
                ctx.saint_graph.process_news_reading(
                    title=first_item.title, content=first_item.content, wait_after=False
                )
            )

    await intro_task

    return BroadcastPhase.NEWS


async def handle_news(ctx: BroadcastContext) -> BroadcastPhase:
    """
    NEWS: コメント優先で確認し、なければニュースを 1 本読み上げる。

    プリフェッチ最適化:
    - 現ニュースの音声合成は ctx.next_news_task に既に積まれているはず（無ければ即時生成）
    - 現ニュース再生開始と同時に「次のニュース」のセリフ生成 task をキック
    - 再生完了を wait_for_queue で待機 → 次ループへ

    ニュースを全消化したら QA へ遷移する。
    """
    # コメント優先
    if await _poll_and_respond(ctx):
        ctx.idle_counter = 0
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

    item = ctx.news_service.peek_current_item()
    if not item:
        return BroadcastPhase.QA

    logger.info(f"Reading news item: {item.title}")
    try:
        await ctx.saint_graph.body.update_news_caption(item.title, item.content)
    except Exception as e:
        logger.warning(f"Failed to update news caption: {e}")

    # 現ニュース: prefetch 済 task があれば await（既にキュー投入済 or 投入直前）、無ければ即時生成
    if ctx.next_news_task is not None:
        try:
            await ctx.next_news_task
        except Exception as e:
            logger.warning(f"Prefetched news task failed, falling back to inline generation: {e}")
            await ctx.saint_graph.process_news_reading(title=item.title, content=item.content, wait_after=False)
        ctx.next_news_task = None
    else:
        await ctx.saint_graph.process_news_reading(title=item.title, content=item.content, wait_after=False)

    # 現ニュースの index を進める
    ctx.news_service.get_next_item()

    # 次ニュースの prefetch を仕込む（現ニュース再生中に並行してセリフ生成）
    if ctx.news_service.has_next():
        next_item = ctx.news_service.peek_current_item()
        if next_item:
            logger.info(f"Prefetching next news: {next_item.title}")
            ctx.next_news_task = asyncio.create_task(
                ctx.saint_graph.process_news_reading(
                    title=next_item.title, content=next_item.content, wait_after=False
                )
            )

    # 現ニュースの音声再生完了を待つ（auto-filler 起動条件にも影響するので必須）
    await ctx.saint_graph.body.wait_for_queue()

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
    if await _poll_and_respond(ctx):
        ctx.idle_counter = 0
        return BroadcastPhase.QA

    ctx.idle_counter += 1
    if ctx.idle_counter > MAX_WAIT_CYCLES:
        logger.info(
            f"Silence timeout ({MAX_WAIT_CYCLES} cycles) reached in QA. Closing."
        )
        return BroadcastPhase.CLOSING

    # 一定サイクル毎に促進セリフ
    if ctx.idle_counter % _QA_PROMPT_EVERY == 1:
        try:
            await ctx.saint_graph.process_qa()
        except Exception as e:
            logger.warning(f"Failed to run QA prompt: {e}")

    return BroadcastPhase.QA


async def handle_closing(ctx: BroadcastContext) -> BroadcastPhase:
    """CLOSING: 締めの挨拶をしてリソースを解放する。None を返しループ終了。"""
    await ctx.saint_graph.process_closing()

    # すべての発話が完了するまで待機（キューの消化待機）
    logger.info("Waiting for final speech to complete...")
    await ctx.saint_graph.body.wait_for_queue()

    # 配信終了画面へシーン切替（ending イラスト＋BGM）
    try:
        ending_scene = os.getenv("BROADCAST_ENDING_SCENE", "ending")
        await ctx.saint_graph.body.switch_scene(ending_scene)
    except Exception as e:
        logger.warning(f"Failed to switch to ending scene: {e}")

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
    BroadcastPhase.INTRO:   "op",
    BroadcastPhase.NEWS:    "news",
    BroadcastPhase.QA:      "chitchat",
    BroadcastPhase.CLOSING: "ed",
}

# 切替 SE が鳴り終わるまで次の BGM 切替を遅らせる秒数。
# SE と次のBGMが同時再生にならないよう、bgm_se_transition の長さに合わせる。
_SE_HOLD_SECONDS = float(os.getenv("BROADCAST_SE_HOLD_SECONDS", "2.0"))


async def _switch_bgm_for_phase(
    ctx: BroadcastContext, phase: BroadcastPhase, *, with_se: bool = False
) -> None:
    """フェーズに対応する BGM へ切替する。失敗してもループは継続させる。

    `with_se=True` の場合はシーン切替 SE を先に鳴らしてから BGM を切り替える。
    通常はフェーズ遷移時のみ True、配信開始の最初の INTRO 投入時は False で呼ぶ。
    """
    bgm_id = _PHASE_BGM.get(phase)
    if not bgm_id:
        return
    if with_se:
        try:
            await ctx.saint_graph.body.play_bgm("se")
            # SE が鳴り終わるまで次の BGM 切替を遅らせる（同時再生回避）
            await asyncio.sleep(_SE_HOLD_SECONDS)
            # switch_bgm は SE を停止しない（設計上）ため、明示的に SE を止める
            await ctx.saint_graph.body.stop_bgm("se")
        except Exception as e:
            logger.warning(f"Failed to play transition SE: {e}")
    try:
        await ctx.saint_graph.body.switch_bgm(bgm_id)
    except Exception as e:
        logger.warning(f"Failed to switch BGM for phase {phase.value}: {e}")


async def run_broadcast_loop(ctx: BroadcastContext) -> None:
    """
    ステートマシンのメインループ。

    INTRO から始まり、各ハンドラが返す次フェーズに従って遷移します。
    フェーズ遷移時に対応 BGM へ切り替えます。
    ハンドラが None を返すとループを終了します。
    """
    phase = BroadcastPhase.INTRO
    logger.info("Entering Broadcast Loop (state machine)...")

    # 雑談セリフを body-streamer に登録（auto-filler が idle 時に混ぜる）
    await ctx.saint_graph.register_chitchat()

    # 配信開始時に最初のフェーズ（INTRO）の BGM を流す（SEなしでスタート）
    await _switch_bgm_for_phase(ctx, phase, with_se=False)

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
