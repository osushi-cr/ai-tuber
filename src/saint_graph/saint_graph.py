import asyncio
import logging
import re
import traceback
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
        """開始挨拶を実行します。"""
        template = self.templates.get("intro", "こんにちは。配信を始めます。")
        return await self.process_turn(template, context="Intro", wait_after=wait_after)

    async def prepare_intro_text(self) -> List[Tuple[str, str]]:
        """挨拶セリフだけを Gemini で生成し、 発話キューには投入しません。

        prepare_news_reading_text と同じく独立 session で叩いてメイン yt_session
        への文脈混入を避ける。 戻り値の sentences を play_prepared_sentences に
        渡すと TTS と再生が start するため、 シーン切替後に音声開始するための
        分離が可能になる。
        """
        template = self.templates.get("intro", "こんにちは。配信を始めます。")
        self._prefetch_seq += 1
        prefetch_session_id = f"yt_intro_prefetch_{self._prefetch_seq}"
        return await self._collect_turn_sentences(
            template,
            context="Intro",
            session_id=prefetch_session_id,
            user_id="yt_intro_prefetch_user",
        )

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

    async def prepare_sentences_synth(
        self,
        sentences: List[Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        """各 sentence の TTS 合成を queue 外で先行実行し、 結果リストを返す。

        sentences: [(style, text), ...]
        戻り値: [{"file_path", "duration", "style", "text"}, ...]

        WAITING 60 秒中に intro / news1 を事前合成しておく等、 視聴者を待たせない
        ための先行合成。 後段で `body.queue_speak(prepared_wav_path=...)` に渡す。
        """
        prepared: List[Dict[str, Any]] = []
        for style, text in sentences:
            result = await self.body.prepare_speak(text=text, style=style)
            prepared.append({
                "file_path": result.get("file_path", ""),
                "duration": result.get("duration", 0.0),
                "style": style,
                "text": text,
            })
        return prepared

    async def play_prepared_sentences(
        self,
        sentences: List[Tuple[str, str]],
        wait_after: bool = True,
    ) -> List[str]:
        """生成済みセリフを Body の発話キューへ投入し、 投入した speak action_ids を返す。"""
        return await self._play_sentences(sentences, wait_after=wait_after)

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

    async def prepare_news_finished_text(self) -> List[Tuple[str, str]]:
        """ニュース全消化時の「読み終わり」セリフを Gemini 生成する（発話キュー投入は別）。

        最後のニュース再生中に handle_news 内で裏先行で呼び、 prepare_sentences_synth
        で wav 化したものを handle_qa 冒頭で再生する用。 prefetch 専用 session を使う。
        """
        template = self.templates.get("news_finished", "全てのニュースを読み上げました。")
        self._prefetch_seq += 1
        prefetch_session_id = f"yt_news_finished_prefetch_{self._prefetch_seq}"
        return await self._collect_turn_sentences(
            template,
            context="News Finished",
            session_id=prefetch_session_id,
            user_id="yt_news_finished_prefetch_user",
        )

    async def prepare_qa_intro_text(self) -> List[Tuple[str, str]]:
        """QA フェーズ開始の促進セリフを Gemini 生成する（発話キュー投入は別）。

        最後のニュース再生中に handle_news 内で裏先行で呼び、 prepare_sentences_synth
        で wav 化したものを handle_qa 冒頭で再生する用。 prefetch 専用 session を使う。
        """
        template = self.templates.get("qa", "みんな、コメントどうぞ〜！")
        self._prefetch_seq += 1
        prefetch_session_id = f"yt_qa_intro_prefetch_{self._prefetch_seq}"
        return await self._collect_turn_sentences(
            template,
            context="QA Intro",
            session_id=prefetch_session_id,
            user_id="yt_qa_intro_prefetch_user",
        )

    async def prepare_qa_chitchat_text(
        self, recent_titles: Optional[List[str]] = None
    ) -> List[Tuple[str, str]]:
        """QA 初手 chitchat 用のセリフだけを生成し、発話キューには投入しません。

        最後のニュース再生中に裏で先回り発火し、`handle_qa` 冒頭で preloaded ルートから
        再生する用途。prepare_news_reading_text と同じく専用 session_id を使い、
        メイン session への context 混入を防ぐ。
        """
        template = self.templates.get(
            "qa_chitchat", "今日もみんなと話せて、くらら嬉しいな"
        )
        if recent_titles:
            tail = recent_titles[-3:]
            context = "QA chitchat. 直前に読んだニュース: " + " / ".join(tail)
        else:
            context = "QA chitchat"
        self._prefetch_seq += 1
        prefetch_session_id = f"yt_qa_prefetch_{self._prefetch_seq}"
        return await self._collect_turn_sentences(
            template,
            context=context,
            session_id=prefetch_session_id,
            user_id="yt_qa_prefetch_user",
        )

    async def process_qa_chitchat(self, recent_titles: Optional[List[str]] = None):
        """QA 中の自発雑談：コメントが来ていない時間に独り言／呼びかけ／振り返りを喋る。

        recent_titles を渡すとニュース振り返りトピックの素材として Gemini に提示される。
        """
        template = self.templates.get(
            "qa_chitchat", "今日もみんなと話せて、くらら嬉しいな"
        )
        if recent_titles:
            tail = recent_titles[-3:]
            context = "QA chitchat. 直前に読んだニュース: " + " / ".join(tail)
        else:
            context = "QA chitchat"
        await self.process_turn(template, context=context)

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

    async def process_turn(
        self,
        user_input: str,
        context: Optional[str] = None,
        wait_after: bool = True,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
        caption_type: Optional[str] = None,
    ):
        """
        単一のインタラクションターンを処理します。
        AIからのテキスト出力を取得し、文章単位で Body API (TTS) を実行します。

        wait_after=False のとき、speak キュー投入まで完了したら return し、再生完了は待たない。
        プリフェッチ用（呼び出し側で wait_for_queue するなど別途同期する）。

        caption_title / caption_summary / caption_type を渡すと、最初の発話の再生開始と
        同期して caption overlay を表示する（コメント回答時の「どのコメントに答えているか」
        提示などに使う）。
        """
        logger.info(f"Turn started. Input: {user_input[:50]}..., Context: {context}")
        await self.body.change_emotion("silent")
        sentences = await self._collect_turn_sentences(user_input, context=context)
        if not sentences:
            logger.warning("No text output received from AI.")
            return

        await self._play_sentences(
            sentences,
            wait_after=wait_after,
            first_caption_title=caption_title,
            first_caption_summary=caption_summary,
            caption_type=caption_type,
        )

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
                
                if collected_sentences:
                    full_text = " / ".join(text for _, text in collected_sentences)
                    logger.info(f"[Gemini] {context or 'turn'}: {full_text}")
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
        caption_type: Optional[str] = None,
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
                caption_type=caption_type if sentences_spoken == 0 else None,
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
        caption_type: Optional[str] = None,
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
            if caption_type is not None:
                kwargs["caption_type"] = caption_type
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
        """感情タグを取り除きます。絵文字は voice_normalizer 側で認識外のみ除去する。"""
        sentence = re.sub(r'\[emotion:\s*(\w+)\]', '', sentence)
        return sentence.strip()

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
        """句点（。！？改行）で文に分割する。

        _collect_buffered_sentences と協調する。戻り値の末尾要素は常に
        「未確定セグメント」（句点で終わっていない余り。無ければ空文字）であり、
        呼び出し側がこれを buffer に戻して次のストリームチャンクを待つ。これにより
        句点で完結した文は次チャンクを待たず即確定でき、逐次再生が成立する。
        """
        parts = re.split(r"([。！？\n])", text)
        sentences: list[str] = []
        buf = ""
        for part in parts:
            buf += part
            if part in "。！？\n":
                if buf.strip():
                    sentences.append(buf)
                buf = ""
        # 末尾の未確定分を必ず最後の要素として置く（空でも置く＝契約）
        sentences.append(buf)
        return sentences
