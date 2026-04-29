"""
ニュースキャスター配信のステートマシン。

BroadcastPhase (Enum) と各フェーズのハンドラで構成されます。
各ハンドラは BroadcastContext を受け取り、次の BroadcastPhase を返します。
"""
import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

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


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

async def _poll_and_respond(ctx: BroadcastContext) -> bool:
    """
    コメントをポーリングし、あれば応答します。

    Returns:
        コメントがあり応答した場合 True
    """
    try:
        comments_data = await ctx.saint_graph.body.get_comments()

        if comments_data:
            comments_text = "\n".join(
                f"{c.get('author', 'User')}: {c.get('message', '')}"
                for c in comments_data
            )
            if comments_text:
                logger.info(f"Comments received: {comments_text}")
                await ctx.saint_graph.process_turn(comments_text)
                return True
    except Exception as e:
        logger.error(f"Error in polling/turn: {e}")
    return False


# ---------------------------------------------------------------------------
# フェーズハンドラ
# ---------------------------------------------------------------------------

async def handle_intro(ctx: BroadcastContext) -> BroadcastPhase:
    """INTRO: 配信開始の挨拶を行い、NEWS フェーズへ遷移する。"""
    await ctx.saint_graph.process_intro()
    return BroadcastPhase.NEWS


async def handle_news(ctx: BroadcastContext) -> BroadcastPhase:
    """
    NEWS: コメント優先で確認し、なければニュースを 1 本読み上げる。
    ニュース読み終わり毎に aizuchi 系のフィラーを 1 個キューに積み、
    次のニュースのセリフ生成中の沈黙を埋める。
    ニュースを全消化したら QA へ遷移する。
    """
    # コメント優先
    if await _poll_and_respond(ctx):
        ctx.idle_counter = 0
        return BroadcastPhase.NEWS

    # 次のニュースを読み上げ
    if ctx.news_service.has_next():
        # peek して使う（成功した場合にのみ進める）
        item = ctx.news_service.peek_current_item()
        if item:
            logger.info(f"Reading news item: {item.title}")
            await ctx.saint_graph.process_news_reading(title=item.title, content=item.content)
            # 成功したのでインデックスを進める
            ctx.news_service.get_next_item()
            # ニュース完了→次のセリフ生成中（Gemini応答待ち）の沈黙を埋めるため、
            # aizuchi 系の filler を 1 個キューに積む。voice キューに積まれるので
            # 次の発話が始まるまでに自然に再生される。
            try:
                await ctx.saint_graph.body.play_filler("aizuchi")
            except Exception as e:
                logger.warning(f"Failed to queue filler between news items: {e}")
            return BroadcastPhase.NEWS

    # ニュース全消化 → QA（コメント拾いコーナー）へ
    logger.info("All news items read. Moving to QA (comment corner).")
    await ctx.saint_graph.process_news_finished()
    return BroadcastPhase.QA


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
