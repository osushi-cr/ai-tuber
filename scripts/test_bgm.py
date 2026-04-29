#!/usr/bin/env python3
"""Live OBS BGM control demo.

Cycles through play_bgm / stop_bgm / switch_bgm / play_se against a running
OBS Studio (Mac native) via WebSocket v5.

Run from Irodori-TTS uv env:
    cd ~/src/personal/Irodori-TTS
    uv run python ~/src/github.com/osushi-cr/ai-tuber/scripts/test_bgm.py

Env vars (defaults work for the standard local setup):
    OBS_HOST=127.0.0.1
    OBS_PORT=4455
    OBS_PASSWORD=
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

env_file = REPO_ROOT / ".env"
if env_file.is_file():
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

os.environ.setdefault("OBS_HOST", "127.0.0.1")
os.environ.setdefault("OBS_PORT", "4455")

from body.streamer import obs_adapter  # noqa: E402


async def main() -> None:
    print(f"Connecting to OBS at {os.environ['OBS_HOST']}:{os.environ['OBS_PORT']} ...")
    if not await obs_adapter.connect():
        print("ERROR: cannot connect to OBS WebSocket. Is OBS running with WebSocket enabled?")
        sys.exit(1)
    print("Connected.\n")

    try:
        print("[1] play_bgm('chitchat') — 雑談ループ開始（5秒）")
        await obs_adapter.play_bgm("chitchat")
        await asyncio.sleep(5.0)

        print("[2] play_se() — シーン切替SE（7秒待つ）")
        await obs_adapter.play_se()
        await asyncio.sleep(7.0)

        print("[3] switch_bgm('news') — ニュース読み上げBGMへ切替（5秒）")
        await obs_adapter.switch_bgm("news")
        await asyncio.sleep(5.0)

        print("[4] switch_bgm('chitchat') — 雑談へ戻す（5秒）")
        await obs_adapter.switch_bgm("chitchat")
        await asyncio.sleep(5.0)

        print("[5] play_bgm('op') — オープニング再生（10秒）")
        await obs_adapter.play_bgm("op")
        await asyncio.sleep(10.0)

        print("[6] switch_bgm('chitchat') — 雑談へ戻す（5秒）")
        await obs_adapter.switch_bgm("chitchat")
        await asyncio.sleep(5.0)

        print("[7] play_bgm('ed') — エンディング再生（10秒）")
        await obs_adapter.play_bgm("ed")
        await asyncio.sleep(10.0)

        print("[8] stop_bgm('ed') / stop_bgm('chitchat') — 全停止")
        await obs_adapter.stop_bgm("ed")
        await obs_adapter.stop_bgm("chitchat")

        print("\nDone.")
    finally:
        await obs_adapter.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
