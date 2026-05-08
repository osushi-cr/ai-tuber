"""MioTTS adapter for speech synthesis (HTTP client to local MioTTS server)."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import wave
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

MIOTTS_API_BASE = os.getenv("MIOTTS_API_BASE", "http://localhost:8001")
MIOTTS_PRESET_ID = os.getenv("MIOTTS_PRESET_ID", "kurara")
MIOTTS_TIMEOUT = float(os.getenv("MIOTTS_TIMEOUT", "60.0"))

# テキスト正規化（略語カタカナ化・記号変換・暴走対策）は voice_normalizer に集約し、
# voice_adapter_irodori からも同じ前処理を呼ぶ。
from .voice_normalizer import normalize_text

# 後方互換: scripts/generate_closing_pool.py が voice_adapter_miotts._normalize_text を参照する
_normalize_text = normalize_text


VOICE_DIR = Path(os.getenv("VOICE_DIR", str(Path.home() / ".cache/ai-tuber/voice")))
VOICE_DIR.mkdir(parents=True, exist_ok=True)

# MioTTS は長文を一発で投げると max_tokens まで暴走することがあるため、文単位分割で
# サイズを揃えて安定生成する。1.7B Q4_K_M（Apache 2.0）では 100字一発まで安定（2026-04-29 ベンチ）。
# 短文（min_chars 未満）はマージ、長文（max_chars 超）は読点で再分割。
_SENTENCE_MAX_CHARS = int(os.getenv("MIOTTS_SENTENCE_MAX_CHARS", "100"))
_SENTENCE_MIN_CHARS = int(os.getenv("MIOTTS_SENTENCE_MIN_CHARS", "20"))


def _split_sentences(
    text: str,
    max_chars: int = _SENTENCE_MAX_CHARS,
    min_chars: int = _SENTENCE_MIN_CHARS,
) -> list[str]:
    """句点で分割し、短文(min_chars未満)はマージ、長文(max_chars超)は読点で再分割する。"""
    parts = re.split(r"([。！？\n])", text)
    sentences: list[str] = []
    buf = ""
    for part in parts:
        buf += part
        if part in "。！？\n":
            stripped = buf.strip()
            if stripped:
                sentences.append(stripped)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    # 短文を前後にマージ（merged の最後が min_chars 未満なら次を結合）
    merged: list[str] = []
    for s in sentences:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + len(s) <= max_chars:
            merged[-1] += s
        else:
            merged.append(s)

    # 長文を読点で再分割
    result: list[str] = []
    for s in merged:
        if len(s) <= max_chars:
            result.append(s)
            continue
        sub_parts = re.split(r"([、])", s)
        sub_buf = ""
        for sp in sub_parts:
            if len(sub_buf) + len(sp) > max_chars and sub_buf:
                result.append(sub_buf.strip())
                sub_buf = sp
            else:
                sub_buf += sp
        if sub_buf.strip():
            result.append(sub_buf.strip())

    # 最終 fail-safe: 句点も読点もない長文を max_chars でぶつ切り（暴走の根絶）
    final: list[str] = []
    for s in result:
        while len(s) > max_chars:
            final.append(s[:max_chars])
            s = s[max_chars:]
        if s.strip():
            final.append(s.strip())
    return [s for s in final if s]


def get_wav_duration(file_path: str) -> float:
    """Return WAV file duration in seconds."""
    try:
        with wave.open(file_path, "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate)
    except Exception as e:
        logger.error("WAV duration error for %s: %s", file_path, e)
        return 3.0


def _concat_wavs(wav_paths: list[Path], out_path: Path) -> None:
    """複数の wav ファイルを連結して1ファイルに保存する（同一サンプリングレート前提）。"""
    if not wav_paths:
        raise ValueError("wav_paths is empty")
    with wave.open(str(wav_paths[0]), "rb") as first:
        params = first.getparams()
        all_frames = first.readframes(first.getnframes())
    for p in wav_paths[1:]:
        with wave.open(str(p), "rb") as w:
            all_frames += w.readframes(w.getnframes())
    with wave.open(str(out_path), "wb") as out:
        out.setparams(params)
        out.writeframes(all_frames)


# style → MioTTS API パラメータのマップ。preset_id（MioTTS-Inference の presets/*.pt 名）と
# llm サンプリングパラメータ（temperature 等）を組み合わせて感情差を出す。
# 未登録 style は neutral にフォールバックする（sad/angry/fun は今後素材を追加して拡張予定）。
_DEFAULT_TOP_P = 0.95
_DEFAULT_REPETITION_PENALTY = 1.05

_STYLE_PARAMS: dict[str, dict] = {
    "neutral": {
        "preset_id": MIOTTS_PRESET_ID,
        "temperature": 0.5,
        "top_p": _DEFAULT_TOP_P,
        "repetition_penalty": _DEFAULT_REPETITION_PENALTY,
    },
    "sad": {
        "preset_id": MIOTTS_PRESET_ID,
        "temperature": 0.3,
        "top_p": 0.9,
        "repetition_penalty": _DEFAULT_REPETITION_PENALTY,
    },
    "fun": {
        "preset_id": "kurara_joyful",
        "temperature": 0.8,
        "top_p": _DEFAULT_TOP_P,
        "repetition_penalty": _DEFAULT_REPETITION_PENALTY,
    },
    "joyful": {
        "preset_id": MIOTTS_PRESET_ID,
        "temperature": 0.8,
        "top_p": _DEFAULT_TOP_P,
        "repetition_penalty": 1.2,
    },
    "angry": {
        "preset_id": "kurara_joyful",
        "temperature": 0.8,
        "top_p": _DEFAULT_TOP_P,
        "repetition_penalty": _DEFAULT_REPETITION_PENALTY,
    },
}


def _resolve_style(style: str) -> dict:
    return _STYLE_PARAMS.get(style, _STYLE_PARAMS["neutral"])


def _post_tts(text: str, params: dict) -> bytes:
    """MioTTS API に正規化済みテキストと style パラメータを POST して wav バイナリを返す。"""
    payload = {
        "text": text,
        "reference": {"type": "preset", "preset_id": params["preset_id"]},
        "output": {"format": "wav"},
        "llm": {
            "temperature": params["temperature"],
            "top_p": params["top_p"],
            "repetition_penalty": params["repetition_penalty"],
        },
    }
    with httpx.Client(timeout=MIOTTS_TIMEOUT) as client:
        resp = client.post(f"{MIOTTS_API_BASE}/v1/tts", json=payload)
        resp.raise_for_status()
        return resp.content


# MioTTS-1.7B は temperature が高いと max_tokens まで暴走することがあり、
# 短文（38字）でも 28秒以上の wav が生成される事例を 2026-05-02 検証で観測。
# 通常発話は 0.18〜0.24 秒/字、 暴走例は 0.41 秒/字以上になるため、
# その間の 0.35 秒/字を境界にして 1 回だけ再生成する。
_DURATION_PER_CHAR_LIMIT = float(os.getenv("MIOTTS_DURATION_PER_CHAR_LIMIT", "0.35"))


def _wav_duration_from_bytes(wav_bytes: bytes) -> float:
    """wav バイナリのまま duration を読む（一時ファイル不要）。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _post_tts_with_retry(text: str, params: dict) -> bytes:
    """生成後 duration が text_len * _DURATION_PER_CHAR_LIMIT を超えたら 1 回だけ再生成する。

    再生成も異常な場合は警告ログのみ残してその wav を返す（無限リトライ防止）。
    """
    wav_bytes = _post_tts(text, params)
    text_len = max(1, len(text))
    dur = _wav_duration_from_bytes(wav_bytes)
    ratio = dur / text_len
    if ratio <= _DURATION_PER_CHAR_LIMIT:
        return wav_bytes

    logger.warning(
        f"[synth] runaway detected: dur={dur:.1f}s / len={text_len} = {ratio:.2f}s/char "
        f"> {_DURATION_PER_CHAR_LIMIT}. Retrying once."
    )
    retry_bytes = _post_tts(text, params)
    retry_dur = _wav_duration_from_bytes(retry_bytes)
    retry_ratio = retry_dur / text_len
    if retry_ratio > _DURATION_PER_CHAR_LIMIT:
        logger.warning(
            f"[synth] retry still abnormal: dur={retry_dur:.1f}s ratio={retry_ratio:.2f}s/char. "
            f"Using retry result anyway."
        )
    return retry_bytes


def _synthesize_sync(text: str, style: str) -> tuple[str, float]:
    # 正規化→分割→送信 の順序を厳密に守る（split時点で絵文字・空白等が残ってると暴走の元）
    normalized = normalize_text(text)
    sentences = _split_sentences(normalized)
    params = _resolve_style(style)
    logger.info(
        f"[synth] text_len={len(text)}->{len(normalized)} sentences={len(sentences)} "
        f"style={style} preset={params['preset_id']} temp={params['temperature']}"
    )

    if len(sentences) <= 1:
        wav_bytes = _post_tts_with_retry(normalized, params)
        filename = f"speech_{abs(hash(text)) % 100000}.wav"
        out_path = VOICE_DIR / filename
        out_path.write_bytes(wav_bytes)
        return str(out_path), get_wav_duration(str(out_path))

    # 多文: 各文を順次生成→ wav 結合
    parts: list[Path] = []
    for i, sent in enumerate(sentences):
        wav_bytes = _post_tts_with_retry(sent, params)
        part_path = VOICE_DIR / f"speech_{abs(hash(text)) % 100000}_part{i}.wav"
        part_path.write_bytes(wav_bytes)
        parts.append(part_path)
        logger.info(f"[synth:part {i + 1}/{len(sentences)}] len={len(sent)} -> {part_path.name}")

    out_path = VOICE_DIR / f"speech_{abs(hash(text)) % 100000}.wav"
    _concat_wavs(parts, out_path)
    return str(out_path), get_wav_duration(str(out_path))


async def generate_and_save(
    text: str,
    style: str = "neutral",
    speaker_id: Optional[int] = None,
) -> tuple[str, float]:
    """Generate speech via MioTTS HTTP API and save to VOICE_DIR.

    `style` は _STYLE_PRESET_MAP で対応する MioTTS プリセットを選択する。未登録 style は
    ベース preset（neutral）にフォールバックする。`speaker_id` は voice_adapter_irodori との
    I/F 互換のため受け取るが MioTTS では未使用。
    """
    logger.info("Generating speech: '%s' (style=%s)", text, style)
    return await asyncio.to_thread(_synthesize_sync, text, style)
