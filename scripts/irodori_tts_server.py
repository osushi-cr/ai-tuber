"""Irodori-TTS HTTP server.

Irodori-TTS の InferenceRuntime を singleton で常駐させ、HTTP 越しに合成依頼を受ける。
body venv を ML スタックで汚染せずに済むよう、本ファイルは Irodori-TTS の `.venv` で実行する。

起動:
    ~/src/personal/Irodori-TTS/.venv/bin/python scripts/irodori_tts_server.py
    （または scripts/start_irodori_server.sh）

設定（環境変数、voice_adapter_irodori.py と同じ既定値）:
    IRODORI_HOST          default 127.0.0.1
    IRODORI_PORT          default 8003
    IRODORI_CHECKPOINT_REPO   default Aratako/Irodori-TTS-500M-v3
    IRODORI_CODEC_REPO    default Aratako/Semantic-DACVAE-Japanese-32dim
    IRODORI_REF_WAV       default ~/src/personal/Irodori-TTS/voice_library/kurara/reference.wav
    IRODORI_DEVICE        default mps
    IRODORI_PRECISION     default fp32
    IRODORI_NUM_STEPS     default 8
    IRODORI_T_SCHEDULE_MODE default sway
    IRODORI_SWAY_COEFF    default -1.0
    VOICE_DIR             default ~/.cache/ai-tuber/voice
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from huggingface_hub import hf_hub_download
from pydantic import BaseModel

from irodori_tts.inference_runtime import (
    InferenceRuntime,
    RuntimeKey,
    SamplingRequest,
    save_wav,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("irodori_tts_server")

HOST = os.getenv("IRODORI_HOST", "127.0.0.1")
PORT = int(os.getenv("IRODORI_PORT", "8003"))

CHECKPOINT_REPO = os.getenv("IRODORI_CHECKPOINT_REPO", "Aratako/Irodori-TTS-500M-v3")
CODEC_REPO = os.getenv("IRODORI_CODEC_REPO", "Aratako/Semantic-DACVAE-Japanese-32dim")
REF_WAV = os.getenv(
    "IRODORI_REF_WAV",
    str(Path.home() / "src/personal/Irodori-TTS/voice_library/kurara/reference.wav"),
)
DEVICE = os.getenv("IRODORI_DEVICE", "mps")
PRECISION = os.getenv("IRODORI_PRECISION", "fp32")
NUM_STEPS = int(os.getenv("IRODORI_NUM_STEPS", "8"))
T_SCHEDULE_MODE = os.getenv("IRODORI_T_SCHEDULE_MODE", "sway")
SWAY_COEFF = float(os.getenv("IRODORI_SWAY_COEFF", "-1.0"))

VOICE_DIR = Path(os.getenv("VOICE_DIR", str(Path.home() / ".cache/ai-tuber/voice")))
VOICE_DIR.mkdir(parents=True, exist_ok=True)

_runtime: InferenceRuntime | None = None
# InferenceRuntime は MPS Metal command buffer がスレッドセーフでないため、
# 並列リクエストは server 側で必ず直列化する（"commit an already committed
# command buffer" assertion で server 即死を防ぐ）。
_synth_lock = asyncio.Lock()


def _load_runtime() -> InferenceRuntime:
    logger.info("Loading Irodori-TTS checkpoint: %s", CHECKPOINT_REPO)
    checkpoint_path = hf_hub_download(repo_id=CHECKPOINT_REPO, filename="model.safetensors")
    runtime = InferenceRuntime.from_key(
        RuntimeKey(
            checkpoint=checkpoint_path,
            model_device=DEVICE,
            codec_repo=CODEC_REPO,
            model_precision=PRECISION,
            codec_device=DEVICE,
            codec_precision=PRECISION,
            codec_deterministic_encode=True,
            codec_deterministic_decode=True,
            compile_model=False,
            compile_dynamic=False,
        )
    )
    logger.info(
        "Runtime ready (device=%s precision=%s steps=%d schedule=%s sway_coeff=%.2f)",
        DEVICE, PRECISION, NUM_STEPS, T_SCHEDULE_MODE, SWAY_COEFF,
    )
    return runtime


def _prewarm(runtime: InferenceRuntime) -> None:
    """初回 synth は MPS Metal グラフコンパイルで数秒重い。起動時に 1 発打って吸収する。"""
    t0 = time.perf_counter()
    runtime.synthesize(
        SamplingRequest(
            text="ウォームアップ。",
            caption=None,
            ref_wav=REF_WAV,
            ref_latent=None,
            no_ref=False,
            ref_normalize_db=-16.0,
            ref_ensure_max=True,
            num_candidates=1,
            decode_mode="sequential",
            seconds=None,
            max_ref_seconds=30.0,
            num_steps=NUM_STEPS,
            t_schedule_mode=T_SCHEDULE_MODE,
            sway_coeff=SWAY_COEFF,
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
    logger.info("Prewarm done in %.2fs", time.perf_counter() - t0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runtime
    _runtime = _load_runtime()
    _prewarm(_runtime)
    yield
    _runtime = None


app = FastAPI(lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str


class TTSResponse(BaseModel):
    audio_path: str
    duration: float
    sample_rate: int


def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


@app.get("/healthz")
def healthz():
    return {"status": "ok" if _runtime is not None else "loading"}


@app.post("/tts", response_model=TTSResponse)
async def synthesize(req: TTSRequest):
    if _runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    # MPS は並列実行で Metal command buffer が衝突するため、ロック内で同期合成する。
    async with _synth_lock:
        return await asyncio.to_thread(_synthesize_locked, req.text)


def _synthesize_locked(text: str) -> TTSResponse:
    logger.info("[synth] text_len=%d", len(text))
    assert _runtime is not None
    result = _runtime.synthesize(
        SamplingRequest(
            text=text,
            caption=None,
            ref_wav=REF_WAV,
            ref_latent=None,
            no_ref=False,
            ref_normalize_db=-16.0,
            ref_ensure_max=True,
            num_candidates=1,
            decode_mode="sequential",
            seconds=None,
            max_ref_seconds=30.0,
            num_steps=NUM_STEPS,
            t_schedule_mode=T_SCHEDULE_MODE,
            sway_coeff=SWAY_COEFF,
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
    return TTSResponse(
        audio_path=str(saved_path),
        duration=_wav_duration(str(saved_path)),
        sample_rate=result.sample_rate,
    )


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
