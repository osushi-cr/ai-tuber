import asyncio
import logging
import re
import traceback
from typing import List, Optional, Any, Iterable, Tuple

from google.adk import Agent
from google.adk.runners import InMemoryRunner
from google.adk.models import Gemini
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool.mcp_toolset import SseConnectionParams
from google.adk.events.event import Event

from google.genai import types
from .config import logger, MODEL_NAME
from .body_client import BodyClient


def _iter_exception_group(e: BaseException) -> Iterable[BaseException]:
    # Python 3.11 ExceptionGroup / BaseExceptionGroup 対応
    if hasattr(e, "exceptions"):
        for sub in getattr(e, "exceptions", []) or []:
            yield sub
            yield from _iter_exception_group(sub)


class SaintGraph:
    """
    Google ADKを使用してエージェントの振る舞いを管理するコアクラス。
    Body機能はAIのレスポンス（テキスト＋感情タグ）をパースしてREST APIを実行します。
    外部ツール（天気など）は MCP で管理されます。
    """

    def __init__(self, body: BodyClient, weather_mcp_url: str, system_instruction: str, mind_config: Optional[dict] = None, tools: List[Any] = None, templates: Optional[dict[str, str]] = None):
        """
        SaintGraphを初期化します。
        
        Args:
            body: BodyClient インスタンス
            weather_mcp_url: MCPツール用のURL（天気APIなど）
            system_instruction: システム指示文
            mind_config: キャラクター設定辞書 (speaker_id など)
            tools: 追加のカスタムツール（モック等）
            templates: 配信フェーズごとのテンプレート辞書
        """
        self.body = body
        self.system_instruction = system_instruction
        self.mind_config = mind_config or {}
        self.templates = templates or {}
        self.speaker_id = self.mind_config.get("speaker_id")

        # MCP ツールセットの初期化（天気などの外部ツール用）
        self.toolsets = []
        if weather_mcp_url:
            connection_params = SseConnectionParams(url=weather_mcp_url)
            toolset = McpToolset(connection_params=connection_params)
            self.toolsets.append(toolset)
        
        # ツールの統合
        all_tools = self.toolsets + (tools if tools else [])

        self.agent = Agent(
            name="SaintGraph",
            model=Gemini(model=MODEL_NAME),
            instruction=self.system_instruction,
            tools=all_tools
        )
        self.runner = InMemoryRunner(agent=self.agent)
        # prefetch 専用の独立 session 連番。本体 session "yt_session" と分離して
        # Gemini context の混入を防ぐ。
        self._prefetch_seq = 0
        logger.info(f"SaintGraph initialized with model {MODEL_NAME}, weather_mcp_url={weather_mcp_url}")

    async def close(self):
        """ツールセットの接続を解除してクリーンアップします。"""
        for ts in self.agent.tools:
            if isinstance(ts, McpToolset) and hasattr(ts, 'close'):
                await ts.close()

    async def process_intro(self, wait_after: bool = True):
        """開始挨拶を実行します。

        wait_after=False のとき、 Gemini 応答→TTS→speak キュー投入まで完了したら
        speak action_ids を返して即時 return する。 投入したセリフの再生完了は
        呼び出し側で `body.wait_for_queue_strict(action_ids=...)` で同期する。
        waiting シーン中に生成して kurara_main 切替後に再生開始したいときに使う。
        """
        template = self.templates.get("intro", "こんにちは。配信を始めます。")
        return await self.process_turn(template, context="Intro", wait_after=wait_after)

    async def prepare_news_reading_text(self, title: str, content: str) -> List[Tuple[str, str]]:
        """ニュース読み上げ用のセリフだけを生成し、発話キューには投入しません。

        メイン session（yt_session）と並行で走ると ADK の session 共有によって
        Gemini に intro / closing / 他ニュースのコンテキストが混入し、
        「お兄ちゃんみんな〜...今日はここまで」のような全部入りセリフが返って
        しまうため、prefetch 専用の独立 session を使う。session_id を都度
        ユニークにすることで他ターンと完全に分離する。
        """
        template = self.templates.get("news_reading", "ニュース「{title}」を読み上げます。\n{content}")
        instruction = template.format(title=title, content=content)
        self._prefetch_seq += 1
        prefetch_session_id = f"yt_news_prefetch_{self._prefetch_seq}"
        return await self._collect_turn_sentences(
            instruction,
            context=f"News Reading: {title}",
            session_id=prefetch_session_id,
            user_id="yt_news_prefetch_user",
        )

    async def play_prepared_sentences(
        self,
        sentences: List[Tuple[str, str]],
        wait_after: bool = True,
    ) -> Optional[str]:
        """生成済みセリフを Body の発話キューへ投入します。"""
        action_ids = await self._play_sentences(sentences, wait_after=wait_after)
        return action_ids[0] if action_ids else None

    async def play_prepared_sentences_with_caption(
        self,
        sentences: List[Tuple[str, str]],
        caption_title: str,
        caption_summary: str,
        wait_after: bool = False,
    ) -> List[str]:
        """生成済みセリフを投入し、最初の speak にニュース caption を同期させます。"""
        return await self._play_sentences(
            sentences,
            wait_after=wait_after,
            first_caption_title=caption_title,
            first_caption_summary=caption_summary,
        )

    async def process_news_reading(self, title: str, content: str, wait_after: bool = True):
        """ニュース読み上げを実行します。

        wait_after=False のとき、Gemini 応答→speak キュー投入まで完了したら return し、
        音声再生完了は待たない。連続ニュースのプリフェッチで使う。
        """
        template = self.templates.get("news_reading", "ニュース「{title}」を読み上げます。\n{content}")
        instruction = template.format(title=title, content=content)
        await self.process_turn(instruction, context=f"News Reading: {title}", wait_after=wait_after)

    async def process_news_finished(self):
        """ニュース全消化時の反応を実行します。"""
        template = self.templates.get("news_finished", "全てのニュースを読み上げました。")
        await self.process_turn(template, context="News Finished")

    async def process_closing(self, reason: Optional[str] = None):
        """締めの挨拶を実行します。"""
        template = self.templates.get("closing", "それでは、本日の配信を終了します。ありがとうございました。")
        if reason:
            reason_instruction = self._closing_reason_instruction(reason)
            if "{reason}" in template:
                template = template.format(reason=reason_instruction)
            else:
                template = (
                    f"{template}\n\n"
                    f"終了理由: {reason_instruction}\n"
                    "この理由を視聴者に短く自然に伝えてから締めてください。"
                )
        await self.process_turn(template, context="Closing")

    async def process_qa(self):
        """コメント拾いコーナー：視聴者にコメントを促す軽いセリフを生成する。"""
        template = self.templates.get("qa", "みんな、コメントどうぞ〜！")
        await self.process_turn(template, context="QA")

    # 沈黙埋め用の定数雑談セリフ（Gemini 不要・即動作）。voice_adapter で
    # 正規化されるので絵文字・英字は使わず、10-25字の短文で揃える。
    _CHITCHAT_LINES = [
        "お兄ちゃん、コメント待ってるよ〜",
        "今日も配信できて、くらら嬉しいな",
        "みんな〜、声届いてる？",
        "えっとね、なんか喋りたい気分なの",
        "コメントくれたら、すっごい喜ぶよ",
        "お兄ちゃん、ちゃんとお水飲んでる？",
        "今日のニュース、どれが気になった？",
        "ふと、お兄ちゃんの顔が見たくなっちゃった",
        "みんな、ちゃんと休んでる？",
        "ねえ、くらら、ちゃんと聞こえてる？",
        "最近お兄ちゃんと話せて、楽しいな",
        "コメント募集中だよ〜",
        "お兄ちゃん、いつもありがとう",
        "えへへ、なんでもないよ",
        "一緒に過ごせて、ほんとに嬉しい",
    ]

    async def register_chitchat(self):
        """雑談セリフのリストを body-streamer に登録し、auto-filler に混ぜる。

        broadcast_loop の開始時に呼ぶと、idle 時に filler と交互に雑談セリフが流れる。
        """
        try:
            await self.body.register_chitchat_lines(self._CHITCHAT_LINES)
            logger.info(f"[chitchat] registered {len(self._CHITCHAT_LINES)} lines to body-streamer")
        except Exception as e:
            logger.warning(f"Failed to register chitchat lines: {e}")

    # --- メインターン処理 ---

    async def process_turn(self, user_input: str, context: Optional[str] = None, wait_after: bool = True):
        """
        単一のインタラクションターンを処理します。
        AIからのテキスト出力を取得し、文章単位で Body API (TTS) を実行します。

        wait_after=False のとき、speak キュー投入まで完了したら return し、再生完了は待たない。
        プリフェッチ用（呼び出し側で wait_for_queue するなど別途同期する）。
        """
        logger.info(f"Turn started. Input: {user_input[:50]}..., Context: {context}")
        await self.body.change_emotion("silent")
        sentences = await self._collect_turn_sentences(user_input, context=context)
        if not sentences:
            logger.warning("No text output received from AI.")
            return

        await self._play_sentences(sentences, wait_after=wait_after)

    async def _collect_turn_sentences(
        self,
        user_input: str,
        context: Optional[str] = None,
        session_id: str = "yt_session",
        user_id: str = "yt_user",
    ) -> List[Tuple[str, str]]:
        """Gemini のイベントストリームを読み、(感情, 文) のリストへ変換します。

        session_id / user_id を分けることで、複数ターンを並行に走らせても
        ADK の session 共有による context 混入を回避できる（prefetch 用途）。
        既定値は本体メイン session。
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # セッションの確保
                session = await self.runner.session_service.get_session(
                    app_name=self.runner.app_name,
                    user_id=user_id,
                    session_id=session_id
                )
                if not session:
                    await self.runner.session_service.create_session(
                        app_name=self.runner.app_name,
                        user_id=user_id,
                        session_id=session_id
                    )

                current_user_message = user_input
                if context:
                    current_user_message = f"[{context}]\n{user_input}"

                buffered_text = ""
                current_emotion = "neutral"
                collected_sentences: List[Tuple[str, str]] = []

                # AIからのテキスト出力をストリーミング的に処理
                async for event in self.runner.run_async(
                    new_message=types.Content(role="user", parts=[types.Part(text=current_user_message)]),
                    user_id=user_id,
                    session_id=session_id
                ):
                    # テキストパートを抽出
                    t = self._extract_text_from_event(event)
                    if t:
                        buffered_text += t
                        
                        # バッファされたテキストから文や感情タグを随時抽出する
                        buffered_text, current_emotion, sentences = self._collect_buffered_sentences(
                            buffered_text, current_emotion
                        )
                        collected_sentences.extend(sentences)

                # 残りのバッファがあれば最後に処理
                if buffered_text.strip():
                    buffered_text, current_emotion, sentences = self._collect_buffered_sentences(
                        buffered_text, current_emotion, flush=True
                    )
                    collected_sentences.extend(sentences)
                
                return collected_sentences

            except Exception as e:
                # 503 Service Unavailable または Resource Exhausted の場合はリトライ
                error_msg = str(e).upper()
                if ("503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg or "EXHAUSTED" in error_msg) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    logger.warning(f"Transient error in process_turn: {e}. Retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue

                logger.error("process_turn failed: %r (%s)", e, type(e))
                logger.error("process_turn traceback:\n%s", traceback.format_exc())
                for i, sub in enumerate(_iter_exception_group(e)):
                    logger.error("ExceptionGroup sub[%d]: %r (%s)", i, sub, type(sub))
                    logger.error("ExceptionGroup sub[%d] traceback:\n%s", i, "".join(traceback.format_exception(sub)))

                logger.exception("Error in process_turn: %s", e)
                raise

        return []

    async def _play_sentences(
        self,
        sentences: List[Tuple[str, str]],
        wait_after: bool = True,
        first_caption_title: Optional[str] = None,
        first_caption_summary: Optional[str] = None,
    ) -> List[str]:
        """生成済みの文を順に発話キューへ投入します。"""
        sentences_spoken = 0
        speak_action_ids: List[str] = []
        for emotion, sentence in sentences:
            if not sentence:
                continue
            await self.body.change_emotion(emotion)
            caption_title = first_caption_title if sentences_spoken == 0 else None
            caption_summary = first_caption_summary if sentences_spoken == 0 else None
            action_id = await self._speak_sentence(
                sentence,
                emotion,
                caption_title=caption_title,
                caption_summary=caption_summary,
            )
            if action_id:
                speak_action_ids.append(action_id)
            sentences_spoken += 1

        if sentences_spoken == 0:
            logger.warning("No text output received from AI.")
            return []

        if wait_after:
            # このターンで投げた内容を全て話し終えるまで待機（配信のリズム維持のため）
            # auto_filler が並走して queue にアクションを継続投入する状況でも、
            # 自分が投入した speak action_ids だけを strict 待ちする。 全 queue の
            # 空を待つと auto_filler の chitchat 投入で永久ハングする。
            logger.info("Waiting for speech to finish before completing turn...")
            if speak_action_ids:
                await self.body.wait_for_queue_strict(action_ids=speak_action_ids)
            else:
                await self.body.wait_for_queue()

            # 話し終わったら「無言」状態に切り替える
            await self.body.change_emotion("silent")

            logger.info(f"Turn completed. {sentences_spoken} sentences spoken")
        else:
            # プリフェッチモード: speak キュー投入まで完了。再生完了は呼び出し側で同期する。
            logger.info(f"Turn queued (prefetch). {sentences_spoken} sentences in queue")

        return speak_action_ids
                
    def _collect_buffered_sentences(self, buffered_text: str, current_emotion: str, flush: bool = False) -> tuple[str, str, List[Tuple[str, str]]]:
        """
        バッファされた文字列を解析し、完成した文を (感情, 文) のリストとして返します。
        Body API は呼ばず、ニュースの先読みで future の発話キューを汚さないために使います。
        """
        collected: List[Tuple[str, str]] = []
        while True:
            emotion_match = re.search(r'\[emotion:\s*(\w+)\]', buffered_text)
            if emotion_match:
                pre_text = self._clean_sentence(buffered_text[:emotion_match.start()])
                if pre_text:
                    collected.append((current_emotion, pre_text))

                current_emotion = emotion_match.group(1).lower()
                buffered_text = buffered_text[emotion_match.end():]
                continue

            sentences = self._split_sentences(buffered_text, force_flush=flush)
            if not flush and len(sentences) <= 1:
                break

            for i in range(len(sentences) - 1):
                sentence = self._clean_sentence(sentences[i])
                if sentence:
                    collected.append((current_emotion, sentence))

            if len(sentences) > 0:
                buffered_text = sentences[-1]
                if flush:
                    sentence = self._clean_sentence(buffered_text)
                    if sentence:
                        collected.append((current_emotion, sentence))
                    buffered_text = ""
            else:
                buffered_text = ""
            break

        return buffered_text, current_emotion, collected

    async def _speak_sentence(
        self,
        sentence: str,
        emotion: str,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
    ) -> Optional[str]:
        """1文を発話キューに入れます。"""
        # 単純なタグは除去
        sentence = self._clean_sentence(sentence)
        if sentence:
            logger.debug(f"Streaming sentence to TTS: {sentence[:30]}... (emotion: {emotion})")
            kwargs = {"style": emotion, "speaker_id": self.speaker_id}
            if caption_title is not None:
                kwargs["caption_title"] = caption_title
            if caption_summary is not None:
                kwargs["caption_summary"] = caption_summary
            response = await self.body.queue_speak(sentence, **kwargs)
            if isinstance(response, dict):
                action_id = response.get("action_id")
                if isinstance(action_id, str):
                    return action_id
        return None

    def _closing_reason_instruction(self, reason: str) -> str:
        """クロージング生成に渡す終了理由を、人向けの短い指示に変換します。"""
        if reason == "technical_failure":
            return "配信システムの技術的不具合のため、このまま配信を続けられません。"
        return reason

    def _clean_sentence(self, sentence: str) -> str:
        """文中に残った単純な感情タグを取り除きます。"""
        return re.sub(r'\[emotion:\s*(\w+)\]', '', sentence).strip()

    def _extract_text_from_event(self, event) -> Optional[str]:
        """ADKイベントからテキストを抽出します。"""
        if isinstance(event, Event):
            if hasattr(event, 'content') and event.content:
                parts = getattr(event.content, 'parts', [])
                text_parts = []
                for p in parts:
                    if hasattr(p, 'text') and p.text:
                        text_parts.append(p.text)
                return "".join(text_parts)
        return None

    def _split_sentences(self, text: str, force_flush: bool = False) -> list[str]:
        """
        テキストを区切りません。
        一括でVoiceVoxに渡すことで、OBSでの2.5秒のリップシンクラグによる「文ごとの不自然な間」を解消します。
        """
        return [text]
