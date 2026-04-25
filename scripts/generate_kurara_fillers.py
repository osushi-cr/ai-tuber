#!/usr/bin/env python3
"""Generate kurara filler wavs for AITuber idle/thinking moments.

Run from Irodori-TTS uv env:
    cd ~/src/personal/Irodori-TTS
    uv run python ~/src/github.com/osushi-cr/ai-tuber/scripts/generate_kurara_fillers.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IRODORI_ROOT = Path.home() / "src/personal/Irodori-TTS"
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(IRODORI_ROOT))

from body.streamer.voice_adapter_irodori import generate_and_save  # noqa: E402

PRESETS_DIR = Path.home() / "src/personal/Irodori-TTS/voice_library/kurara/presets"

FILLERS: dict[str, str] = {
    # 相槌 (8)
    "aizuchi_un_01": "うん",
    "aizuchi_un_02": "うん、うん",
    "aizuchi_etto_01": "えっと",
    "aizuchi_etto_02": "えーと、そうだね",
    "aizuchi_soudane_01": "そうだね",
    "aizuchi_soudane_02": "そう、そうなの",
    "aizuchi_naruhodo_01": "なるほどね〜",
    "aizuchi_sokka_01": "そっか、そういうことか",
    # 思考中 (4)
    "thinking_unn_01": "うーん",
    "thinking_unn_02": "うーん、どうだろう",
    "thinking_nnn_01": "んー",
    "thinking_etto_03": "えーとね",
    # 反応 (4)
    "reaction_hee_01": "へぇ〜",
    "reaction_hoo_01": "ほぉ〜、それは",
    "reaction_a_01": "あ、そうなんだ",
    "reaction_eh_01": "えっ、そうなの？",
    # つなぎ (2)
    "transition_jaa_01": "じゃあ、次のニュース行こうか",
    "transition_dewa_01": "では次の話題、いってみよう",
    # 配信開始/終了 補助 (2)
    "intro_yaa_01": "やっほー、お兄ちゃん",
    "outro_byebye_01": "また明日ね、バイバイ",
}


async def main() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    total = len(FILLERS)
    print(f"[fillers] generating {total} wavs into {PRESETS_DIR}")

    t_start = time.time()
    for i, (name, text) in enumerate(FILLERS.items(), 1):
        target = PRESETS_DIR / f"filler_{name}.wav"
        if target.exists():
            print(f"[{i:02d}/{total}] skip (exists): {target.name}")
            continue
        t0 = time.time()
        path, dur = await generate_and_save(text)
        elapsed = time.time() - t0
        shutil.move(path, target)
        print(
            f"[{i:02d}/{total}] {target.name}  "
            f"elapsed={elapsed:.1f}s duration={dur:.2f}s  text='{text}'"
        )

    total_elapsed = time.time() - t_start
    print(f"[fillers] done in {total_elapsed:.1f}s ({total} wavs)")


if __name__ == "__main__":
    asyncio.run(main())
