"""
MCPツール実装モジュール。
発話、感情変更、コメント取得の各ツールを提供します。
"""
from typing import Optional, Dict, Any
from uuid import uuid4
from .io_adapter import io_adapter
from ..service import BodyServiceBase


class CLIBodyService(BodyServiceBase):
    """Body CLI サービスの実装。"""

    async def speak(
        self,
        text: str,
        style: str = "neutral",
        speaker_id: Optional[int] = None,
        caption_title: Optional[str] = None,
        caption_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """指定されたテキストを標準出力に表示（発話）します。"""
        style_str = f" ({style})" if style else ""
        io_adapter.write_output(f"\n[AI{style_str}]: {text}")
        return {"message": "Speaking completed", "action_id": str(uuid4())}

    async def change_emotion(self, emotion: str) -> str:
        """アバターの感情を変更します。"""
        return f"Emotion changed to {emotion}"

    async def peek_comments(self) -> str:
        """CLI モードでは入力読み取りが破壊操作なので、peek は空配列を返す（OBS overlay 不使用）。"""
        import json
        return json.dumps([])

    async def consume_comments(self) -> str:
        """キューに蓄積されたユーザーコメントを取得します（破壊）。"""
        import json
        inputs = io_adapter.get_inputs()
        if not inputs:
            return json.dumps([])

        # body-streamerの形式に合わせて、辞書のリストとして返す
        comments = [{"author": "User", "message": line} for line in inputs]
        return json.dumps(comments, ensure_ascii=False)

    async def start_broadcast(self, config: Optional[Dict[str, Any]] = None) -> str:
        """配信/録画を開始します。 (CLI版)"""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("[CLI] start_broadcast called (no-op in CLI mode)")
        return "CLI mode: broadcast start skipped"

    async def stop_broadcast(self) -> str:
        """配信/録画を停止します。 (CLI版)"""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("[CLI] stop_broadcast called (no-op in CLI mode)")
        return "CLI mode: broadcast stop skipped"

    async def wait_for_queue(self) -> str:
        """何もしません (CLI版)"""
        return "CLI mode: no queue to wait"

    async def wait_for_queue_strict(
        self,
        action_ids: Optional[list[str]] = None,
        recent_count: Optional[int] = None,
    ) -> bool:
        """CLI モードでは未処理 queue がないため常に成功扱いにする。"""
        return True


# Singleton インスタンス
body_service = CLIBodyService()
