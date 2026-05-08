"""Irodori-TTS adapter for speech synthesis (Mac native)."""
from __future__ import annotations

import asyncio
import logging
import os
import wave
from pathlib import Path
from typing import Optional

from .voice_normalizer import normalize_text

logger = logging.getLogger(__name__)

IRODORI_CHECKPOINT_REPO = os.getenv("IRODORI_CHECKPOINT_REPO", "Aratako/Irodori-TTS-500M-v2")
IRODORI_REF_WAV = os.getenv(
    "IRODORI_REF_WAV",
    str(Path.home() / "src/personal/Irodori-TTS/voice_library/kurara/reference.wav"),
)
IRODORI_DEVICE = os.getenv("IRODORI_DEVICE", "mps")
IRODORI_NUM_STEPS = int(os.getenv("IRODORI_NUM_STEPS", "40"))
IRODORI_PRECISION = os.getenv("IRODORI_PRECISION", "fp32")
IRODORI_CODEC_REPO = os.getenv("IRODORI_CODEC_REPO", "Aratako/Semantic-DACVAE-Japanese-32dim")

VOICE_DIR = Path(os.getenv("VOICE_DIR", str(Path.home() / ".cache/ai-tuber/voice")))
VOICE_DIR.mkdir(parents=True, exist_ok=True)

_runtime = None


def _get_runtime():
    """Lazy-init Irodori-TTS InferenceRuntime singleton (16.7s/utterance after first call)."""
    global _runtime
    if _runtime is None:
        from huggingface_hub import hf_hub_download
        from irodori_tts.inference_runtime import InferenceRuntime, RuntimeKey

        logger.info("Loading Irodori-TTS checkpoint: %s", IRODORI_CHECKPOINT_REPO)
        checkpoint_path = hf_hub_download(
            repo_id=IRODORI_CHECKPOINT_REPO,
            filename="model.safetensors",
        )
        _runtime = InferenceRuntime.from_key(
            RuntimeKey(
                checkpoint=checkpoint_path,
                model_device=IRODORI_DEVICE,
                codec_repo=IRODORI_CODEC_REPO,
                model_precision=IRODORI_PRECISION,
                codec_device=IRODORI_DEVICE,
                codec_precision=IRODORI_PRECISION,
                codec_deterministic_encode=True,
                codec_deterministic_decode=True,
                enable_watermark=False,
                compile_model=False,
                compile_dynamic=False,
            )
        )
        logger.info(
            "Irodori-TTS runtime ready (device=%s precision=%s steps=%d)",
            IRODORI_DEVICE,
            IRODORI_PRECISION,
            IRODORI_NUM_STEPS,
        )
    return _runtime


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


def _estimate_seconds(text: str) -> float:
    """音声生成に必要な秒数を文字数から推定する。

    日本語の話速はおおむね 1秒 ≒ 4.5 文字。seconds が短いと末尾が不明瞭になる。
    上限は 30s — Irodori-TTS は訓練時の固定 latent steps を超えると品質が崩れるため
    （inference_runtime.py の `fixed_target_latent_steps` 警告参照）。30s 以上の
    発話は呼び出し側でテキストを文単位に分割して順次 speak すること（次セッション課題）。
    """
    est = len(text) / 4.5 + 2.0
    return max(8.0, min(30.0, est))


def _synthesize_sync(text: str) -> tuple[str, float]:
    from irodori_tts.inference_runtime import SamplingRequest, save_wav

    # 略語カナ化・記号正規化（"GPT-5.5" → "ジーピーティー五点五" 等）。voice_adapter_miotts と共通レイヤー。
    text = normalize_text(text)
    runtime = _get_runtime()
    seconds = _estimate_seconds(text)
    logger.info(f"[synth] text_len={len(text)} -> seconds={seconds:.1f}")
    result = runtime.synthesize(
        SamplingRequest(
            text=text,
            caption=None,
            ref_wav=IRODORI_REF_WAV,
            ref_latent=None,
            no_ref=False,
            ref_normalize_db=-16.0,
            ref_ensure_max=True,
            num_candidates=1,
            decode_mode="sequential",
            seconds=seconds,
            max_ref_seconds=30.0,
            num_steps=IRODORI_NUM_STEPS,
            cfg_scale_text=3.0,
            cfg_scale_caption=3.0,
            cfg_scale_speaker=5.0,
            cfg_guidance_mode="independent",
            cfg_min_t=0.5,
            cfg_max_t=1.0,
            context_kv_cache=True,
            trim_tail=True,
            tail_window_size=20,
            tail_std_threshold=0.05,
            tail_mean_threshold=0.1,
        ),
        log_fn=None,
    )
    filename = f"speech_{abs(hash(text)) % 100000}.wav"
    out_path = VOICE_DIR / filename
    saved_path = save_wav(str(out_path), result.audio, result.sample_rate)
    return str(saved_path), get_wav_duration(str(saved_path))


async def generate_and_save(
    text: str,
    style: str = "neutral",
    speaker_id: Optional[int] = None,
) -> tuple[str, float]:
    """Generate speech via Irodori-TTS and save to VOICE_DIR.

    Mirrors the VoiceVox voice_adapter I/F. style/speaker_id are accepted for
    compatibility but currently unused — multi-style synthesis requires per-emotion
    reference wavs which are not yet recorded.
    """
    logger.info("Generating speech: '%s' (style=%s)", text, style)
    return await asyncio.to_thread(_synthesize_sync, text)
