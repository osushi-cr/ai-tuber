#!/usr/bin/env python3
"""Live OBS emotion-switch demo.

Connects to a running OBS Studio (Mac native) via WebSocket v5 and cycles
through silent / normal / joyful / fun / sad / angry, holding each for 2s.

Run from Irodori-TTS uv env (where obs-websocket-py is installed):
    cd ~/src/personal/Irodori-TTS
    uv run python ~/src/github.com/osushi-cr/ai-tuber/scripts/test_obs_emotion_switch.py

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

# Load .env from repo root if present (for OBS_PASSWORD, etc.)
env_file = REPO_ROOT / ".env"
if env_file.is_file():
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Default to localhost; obs_adapter defaults to "obs-studio" which is the
# docker hostname.
os.environ.setdefault("OBS_HOST", "127.0.0.1")
os.environ.setdefault("OBS_PORT", "4455")

from body.streamer import obs_adapter  # noqa: E402

EMOTIONS = ["silent", "neutral", "joyful", "fun", "sad", "angry"]
HOLD_SECONDS = 2.0


async def main() -> None:
    print(f"Connecting to OBS at {os.environ['OBS_HOST']}:{os.environ['OBS_PORT']} ...")
    if not await obs_adapter.connect():
        print("ERROR: cannot connect to OBS WebSocket. Is OBS running with WebSocket enabled?")
        sys.exit(1)
    print("Connected. Starting emotion cycle (Ctrl-C to stop).")

    try:
        for round_idx in range(2):
            for emo in EMOTIONS:
                print(f"  [round {round_idx + 1}] -> {emo}")
                msg = await obs_adapter.set_visible_source(emo)
                print(f"      {msg}")
                await asyncio.sleep(HOLD_SECONDS)
    finally:
        # Always end on silent (idle loop)
        await obs_adapter.set_visible_source("silent")
        await obs_adapter.disconnect()
        print("Disconnected. Final state: silent")


if __name__ == "__main__":
    asyncio.run(main())
