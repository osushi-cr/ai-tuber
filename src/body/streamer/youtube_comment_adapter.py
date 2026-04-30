"""YouTube Live comment adapter using subprocess"""
import subprocess
import sys
import threading
import queue
import os
import json
import logging
from collections import deque
from typing import List, Dict

logger = logging.getLogger(__name__)

# OBS overlay と saint_graph が同じバッファを peek/consume するので、
# バッファに残るコメント数の上限。古いものから FIFO 削除。
BUFFER_MAX = 100


class YouTubeCommentAdapter:
    """Adapter for fetching YouTube Live comments using subprocess.

    OBS overlay 表示用の peek（非破壊）と saint_graph リアクション用の
    consume（破壊）を別メソッドに分離する。subprocess の stdout から
    取り込んだコメントは内部 buffer に溜め、peek は buffer のコピーを、
    consume は buffer 全件返してクリアする。
    """

    def __init__(self, video_id: str):
        """
        Initialize the comment adapter.

        Args:
            video_id: YouTube video/broadcast ID
        """
        # 親 body-streamer と同じ Python（venv の googleapiclient/google-auth が見える）で
        # サブプロセスを起動する。`python` リテラルだとシステム Python に解決されて
        # ai-tuber の .venv が見えず ModuleNotFoundError で即死する。
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'body.streamer.youtube_comment_fetcher', video_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy()  # 環境変数を子プロセスに渡す（YOUTUBE_TOKEN_JSON等）
        )
        self.q: queue.Queue = queue.Queue()
        self.error_q: queue.Queue = queue.Queue()

        # stdout監視スレッド（コメント取得用）
        self.thread = threading.Thread(target=self.enqueue_output, args=(self.process.stdout, self.q))
        self.thread.daemon = True
        self.thread.start()

        # stderr監視スレッド（エラー検出用）
        self.error_thread = threading.Thread(target=self.enqueue_output, args=(self.process.stderr, self.error_q))
        self.error_thread.daemon = True
        self.error_thread.start()

        # peek/consume の双方が参照する内部 buffer（直近 BUFFER_MAX 件まで FIFO）
        self._buffer: deque = deque(maxlen=BUFFER_MAX)
        self._buffer_lock = threading.Lock()

        logger.info(f"Started YouTube comment adapter for video: {video_id}")

    def enqueue_output(self, out, queue: queue.Queue):
        """Read output lines and enqueue them."""
        for line in iter(out.readline, ''):
            queue.put(line)
        out.close()

    def _drain_subprocess(self) -> None:
        """subprocess の stdout/stderr queue を取り込み、buffer に追加する。"""
        # エラー出力をチェック
        while not self.error_q.empty():
            line = self.error_q.get_nowait()
            if not line:
                continue

            line = line.strip()
            if line.startswith("DEBUG: "):
                logger.debug(f"YouTube comment subprocess: {line[7:]}")
            elif line.startswith("INFO: "):
                logger.info(f"YouTube comment subprocess: {line[6:]}")
            elif line.startswith("WARNING: "):
                logger.warning(f"YouTube comment subprocess: {line[9:]}")
            elif line.startswith("ERROR: "):
                logger.error(f"YouTube comment subprocess: {line[7:]}")
            else:
                # プレフィックスがない場合でも、予期せぬエラーの可能性を考慮して警告する
                logger.warning(f"YouTube comment subprocess (unprefixed): {line}")

        # コメントを取得して buffer に追加
        new_comments: list[dict] = []
        while not self.q.empty():
            line = self.q.get_nowait()
            if line:
                try:
                    comment_data = json.loads(line.strip())
                    if "error" in comment_data:
                        logger.error(f"YouTube API error: {comment_data['error']}")
                    else:
                        new_comments.append(comment_data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse comment JSON: {e}, line: {line.strip()}")
        if new_comments:
            with self._buffer_lock:
                self._buffer.extend(new_comments)

    def peek(self) -> List[Dict]:
        """OBS overlay 表示用: buffer のスナップショットを返す（破壊しない）。"""
        self._drain_subprocess()
        with self._buffer_lock:
            return list(self._buffer)

    def consume(self) -> List[Dict]:
        """saint_graph リアクション用: buffer 全件返して buffer をクリア。"""
        self._drain_subprocess()
        with self._buffer_lock:
            comments = list(self._buffer)
            self._buffer.clear()
            return comments

    def close(self):
        """サブプロセスを終了させる"""
        self.process.terminate()
        self.process.wait()
        logger.info("Closed YouTube comment adapter")
