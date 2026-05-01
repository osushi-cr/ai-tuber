"""Body サービスの共通インターフェース (ABC)

CLI / Streamer 両モードが準拠すべき抽象基底クラスを定義します。
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BodyServiceBase(ABC):
    """Body サービスが実装すべき共通インターフェース。"""

    @abstractmethod
    async def speak(
        self,
        text: str,
        style: str = "neutral",
        speaker_id: Optional[int] = None,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
    ) -> Any:
        """テキストを発話します。"""
        ...

    @abstractmethod
    async def change_emotion(self, emotion: str) -> str:
        """アバターの表情（感情）を変更します。"""
        ...

    @abstractmethod
    async def peek_comments(self) -> str:
        """OBS overlay 表示用にコメントを peek します（破壊しない）。JSON 文字列（List[Dict]）を返します。"""
        ...

    @abstractmethod
    async def consume_comments(self) -> str:
        """saint_graph リアクション用にコメントを consume します（buffer drain）。JSON 文字列（List[Dict]）を返します。"""
        ...

    @abstractmethod
    async def start_broadcast(self, config: Optional[Dict[str, Any]] = None) -> str:
        """録画または配信を開始します。"""
        ...

    @abstractmethod
    async def wait_for_queue(self) -> str:
        """すべての処理が完了するまで待機します。"""
        ...

    @abstractmethod
    async def wait_for_queue_strict(
        self,
        action_ids: Optional[list[str]] = None,
        recent_count: Optional[int] = None,
    ) -> bool:
        """指定 action がすべて成功したか検査します。"""
        ...
