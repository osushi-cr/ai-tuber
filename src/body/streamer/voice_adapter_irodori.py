"""Irodori-TTS adapter for speech synthesis (HTTP client to local Irodori-TTS server).

Server は Irodori-TTS の .venv で常駐 (scripts/irodori_tts_server.py)。
body venv を ML スタックで汚染しないための分離設計。voice_adapter_miotts と同じ流儀。

長文は文単位に分割して順次合成し wav 連結する。Irodori-TTS は seconds=30 で
固定 latent steps の上限に達して後半が崩壊するため、文単位に分けるのが必須。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import wave
from pathlib import Path
from typing import Optional

import httpx

from .voice_normalizer import normalize_text

logger = logging.getLogger(__name__)

IRODORI_API_BASE = os.getenv("IRODORI_API_BASE", "http://localhost:8003")
IRODORI_TIMEOUT = float(os.getenv("IRODORI_TIMEOUT", "120.0"))

VOICE_DIR = Path(os.getenv("VOICE_DIR", str(Path.home() / ".cache/ai-tuber/voice")))
VOICE_DIR.mkdir(parents=True, exist_ok=True)

# Irodori-TTS は seconds=30 で latent steps 上限に達するため、1 リクエストの上限を
# 余裕を持たせて 80 字に揃える。短文は前後マージ、長文は読点で再分割。
_SENTENCE_MAX_CHARS = int(os.getenv("IRODORI_SENTENCE_MAX_CHARS", "80"))
_SENTENCE_MIN_CHARS = int(os.getenv("IRODORI_SENTENCE_MIN_CHARS", "20"))


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

    merged: list[str] = []
    for s in sentences:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + len(s) <= max_chars:
            merged[-1] += s
        else:
            merged.append(s)

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

    final: list[str] = []
    for s in result:
        while len(s) > max_chars:
            final.append(s[:max_chars])
            s = s[max_chars:]
        if s.strip():
            final.append(s.strip())
    return final


def _concat_wavs(parts: list[Path], out_path: Path) -> None:
    """同一フォーマット前提で wav の frames を連結する。"""
    if not parts:
        raise ValueError("no parts to concat")
    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]
    for p in parts[1:]:
        with wave.open(str(p), "rb") as w:
            if w.getparams()[:3] != params[:3]:
                logger.warning("wav params mismatch on %s", p)
            frames.append(w.readframes(w.getnframes()))
    with wave.open(str(out_path), "wb") as out:
        out.setparams(params)
        for f in frames:
            out.writeframes(f)


def _wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except Exception as e:
        logger.error("WAV duration error for %s: %s", path, e)
        return 3.0


def get_wav_duration(file_path: str) -> float:
    return _wav_duration(file_path)


def _post_tts_sync(text: str) -> dict:
    with httpx.Client(timeout=IRODORI_TIMEOUT) as client:
        resp = client.post(f"{IRODORI_API_BASE}/tts", json={"text": text})
        resp.raise_for_status()
        return resp.json()


def _synthesize_sync(text: str) -> tuple[str, float]:
    normalized = normalize_text(text)
    sentences = _split_sentences(normalized)
    logger.info(
        "[synth] text_len=%d->%d sentences=%d",
        len(text), len(normalized), len(sentences),
    )

    base_hash = abs(hash(text)) % 100000

    if len(sentences) <= 1:
        data = _post_tts_sync(normalized)
        return data["audio_path"], float(data.get("duration") or _wav_duration(data["audio_path"]))

    parts: list[Path] = []
    for i, sent in enumerate(sentences):
        data = _post_tts_sync(sent)
        # server が保存した path をそのまま使うと並走時に上書きされうるので
        # part 用にコピーリネームして安定化する
        src = Path(data["audio_path"])
        part_path = VOICE_DIR / f"speech_{base_hash}_part{i}.wav"
        if src != part_path:
            part_path.write_bytes(src.read_bytes())
        parts.append(part_path)
        logger.info("[synth:part %d/%d] len=%d -> %s", i + 1, len(sentences), len(sent), part_path.name)

    out_path = VOICE_DIR / f"speech_{base_hash}.wav"
    _concat_wavs(parts, out_path)
    return str(out_path), _wav_duration(str(out_path))


async def generate_and_save(
    text: str,
    style: str = "neutral",
    speaker_id: Optional[int] = None,
) -> tuple[str, float]:
    """Generate speech via Irodori-TTS HTTP server and return (path, duration).

    style/speaker_id は VoiceVox adapter と I/F 互換のため受け取るが現状未使用。
    多感情合成は per-emotion ref-wav を別途録音してから対応する。
    """
    logger.info("Generating speech: '%s' (style=%s)", text, style)
    return await asyncio.to_thread(_synthesize_sync, text)
