#!/usr/bin/env python3
"""End-to-end demo: Irodori-TTS filler wavs + OBS expression switch.

Cycles through 5 filler wavs with matching emotions, using
obs_adapter.play_media_with_emotion to drive both audio playback (via OBS
'voice' media source) and visible expression source in lockstep.

Run from Irodori-TTS uv env:
    cd ~/src/personal/Irodori-TTS
    uv run python ~/src/github.com/osushi-cr/ai-tuber/scripts/test_obs_voice_emotion.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Load .env from repo root
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

PRESETS = Path.home() / "src/personal/Irodori-TTS/voice_library/kurara/presets"

# (filler wav, emotion) — emotion drives expression source via obs_adapter.EMOTION_MAP
DEMO_SCRIPT = [
    (PRESETS / "filler_intro_yaa_01.wav",         "joyful"),
    (PRESETS / "filler_aizuchi_un_02.wav",        "neutral"),
    (PRESETS / "filler_thinking_unn_02.wav",      "sad"),
    (PRESETS / "filler_reaction_hee_01.wav",      "fun"),
    (PRESETS / "filler_aizuchi_naruhodo_01.wav",  "neutral"),
    (PRESETS / "filler_reaction_eh_01.wav",       "fun"),
    (PRESETS / "filler_outro_byebye_01.wav",      "joyful"),
]

GAP_BETWEEN = 0.4  # seconds of silent between utterances


async def main() -> None:
    print(f"Connecting to OBS at {os.environ['OBS_HOST']}:{os.environ['OBS_PORT']} ...")
    if not await obs_adapter.connect():
        print("ERROR: cannot connect to OBS WebSocket.")
        sys.exit(1)
    print("Connected. Starting voice + emotion demo.")

    # Reset to silent before starting
    await obs_adapter.set_visible_source("silent")
    await asyncio.sleep(0.5)

    try:
        for i, (wav, emotion) in enumerate(DEMO_SCRIPT, 1):
            if not wav.is_file():
                print(f"  [{i}] SKIP missing: {wav.name}")
                continue
            print(f"  [{i}] {emotion:10}  {wav.name}")
            ok = await obs_adapter.play_media_with_emotion(
                audio_source="voice",
                file_path=str(wav),
                emotion=emotion,
            )
            if not ok:
                print(f"      FAILED")
                continue

            # Estimate playback duration from WAV header
            import wave
            with wave.open(str(wav), "rb") as w:
                dur = w.getnframes() / float(w.getframerate())
            await asyncio.sleep(dur + GAP_BETWEEN)
            await obs_adapter.set_visible_source("silent")
            await asyncio.sleep(GAP_BETWEEN)
    finally:
        await obs_adapter.set_visible_source("silent")
        await obs_adapter.disconnect()
        print("Done. Final state: silent.")


if __name__ == "__main__":
    asyncio.run(main())
