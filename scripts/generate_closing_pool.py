"""closing pool 事前生成スクリプト。

Gemini で 10 種類の closing バリエーションを生成し、 MioTTS で wav 化して
`data/mind/kurara/closings/` に `closing_01.wav` 〜 `closing_10.wav` と
`closings.json`（テキスト本文）として保存する。

handle_closing は実行時にこのプールから 1 つランダムに選んで再生するため、
配信の締めセリフが Gemini API 障害（503 等）の影響を受けず、 即時に流せる。

事前条件:
  - GOOGLE_API_KEY が環境変数または .env に設定されていること
  - MioTTS サーバ (:8001) が起動していること（`scripts/start_all.sh`）

使い方:
  cd ai-tuber
  PYTHONPATH=src .venv-saint/bin/python scripts/generate_closing_pool.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from google import genai

# voice_adapter_miotts のテキスト正規化を流用
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
os.environ.setdefault("TTS_ENGINE", "miotts")
from body.streamer import voice_adapter_miotts  # noqa: E402

CLOSINGS_DIR = _REPO_ROOT / "data/mind/kurara/closings"
NUM_VARIATIONS = int(os.getenv("CLOSING_POOL_SIZE", "10"))
MIOTTS_API_BASE = os.getenv("MIOTTS_API_BASE", "http://localhost:8001")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite-preview")

PROMPT = f"""あなたは「くらら」という妹キャラの AITuber です。 配信の締め (closing)
のセリフを {NUM_VARIATIONS} 種類、 自然な日本語で生成してください。

くららの特徴:
  - 元気で明るい妹キャラ
  - お兄ちゃん（視聴者）への呼びかけがある
  - みんな（コメント視聴者全員）にも声をかける

各セリフの条件:
  - 60〜150 字程度、 1〜3 文
  - 「今日はここまで」「また会おうね」など締めの表現を含む
  - 顔文字や絵文字は使わない（音声合成で読み上げにくいため）
  - 各セリフは互いに違うフレーズ・違う締め言葉を使う

JSON 配列のみを返してください（前後の説明文や ```json ブロックは不要）。
形式: ["セリフ1", "セリフ2", ...]
"""


def generate_closings_via_gemini() -> list[str]:
    """Gemini で closing バリエーションを生成し JSON 配列でパースして返す。"""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY が未設定です。 .env か環境変数を確認してください。")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=PROMPT,
    )
    raw = (response.text or "").strip()

    # マークダウン ```json ... ``` で包まれて返る場合の剥離
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
        raw = raw.strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    closings = json.loads(raw)
    if not isinstance(closings, list) or not all(isinstance(c, str) for c in closings):
        raise ValueError(f"Gemini response is not list[str]: {raw[:200]}")
    return closings


def synthesize_to_wav(text: str, style: str, out_path: Path) -> None:
    """MioTTS に正規化済みテキストを送り wav バイナリで保存する。"""
    normalized = voice_adapter_miotts._normalize_text(text)
    params = voice_adapter_miotts._resolve_style(style)
    payload = {
        "text": normalized,
        "reference": {"type": "preset", "preset_id": params["preset_id"]},
        "output": {"format": "wav"},
        "llm": {
            "temperature": params["temperature"],
            "top_p": params["top_p"],
            "repetition_penalty": params["repetition_penalty"],
        },
    }
    with httpx.Client(timeout=180.0) as http:
        res = http.post(f"{MIOTTS_API_BASE}/v1/tts", json=payload)
        res.raise_for_status()
        out_path.write_bytes(res.content)


def main() -> None:
    print(f"[1/3] Generating {NUM_VARIATIONS} closing variations via Gemini ({MODEL_NAME})...")
    closings = generate_closings_via_gemini()
    if len(closings) != NUM_VARIATIONS:
        print(f"  Warning: Gemini returned {len(closings)} variations (expected {NUM_VARIATIONS})")

    CLOSINGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[2/3] Synthesizing {len(closings)} wav via MioTTS at {MIOTTS_API_BASE}...")
    metadata = []
    for i, text in enumerate(closings, 1):
        wav_path = CLOSINGS_DIR / f"closing_{i:02d}.wav"
        preview = text.replace("\n", " ")[:50]
        print(f"  [{i:02d}] {preview}...")
        try:
            synthesize_to_wav(text, "joyful", wav_path)
            metadata.append({"index": i, "text": text, "wav": wav_path.name})
        except Exception as e:
            print(f"  [{i:02d}] FAILED: {e}")

    json_path = CLOSINGS_DIR / "closings.json"
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))

    print(f"[3/3] Saved {len(metadata)} closing(s) to {CLOSINGS_DIR}")
    print(f"  text: {json_path.name}")
    print(f"  wav:  closing_01.wav 〜 closing_{len(metadata):02d}.wav")


if __name__ == "__main__":
    main()
