#!/usr/bin/env bash
# kurara AITuber バックエンド3プロセスを順次起動するワンコマンドスクリプト。
# OBS Studio は GUI アプリなので手動起動してもらう前提。

set -uo pipefail

LOG_DIR="${HOME}/.cache/ai-tuber/logs"
MODEL_PATH="${MIOTTS_MODEL_PATH:-${HOME}/.cache/miotts-models/MioTTS-1.7B-Q4_K_M.gguf}"
MIOTTS_INFERENCE_DIR="${HOME}/src/personal/MioTTS-Inference"
AI_TUBER_DIR="${HOME}/src/github.com/osushi-cr/ai-tuber"

mkdir -p "${LOG_DIR}"

# .env の TTS_ENGINE を尊重して TTS バックエンドを切り替える（既定: miotts）
TTS_ENGINE="$(grep -E '^TTS_ENGINE=' "${AI_TUBER_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" )"
TTS_ENGINE="${TTS_ENGINE:-miotts}"
echo "TTS_ENGINE=${TTS_ENGINE}"

start_if_idle() {
    local port="$1" name="$2"
    if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  ✓ ${name} already running on :${port}"
        return 1
    fi
    return 0
}

if [[ "${TTS_ENGINE}" == "irodori" ]]; then
    echo "[1/2] irodori-tts-server :8003"
    "${AI_TUBER_DIR}/scripts/start_irodori_server.sh"
else
    echo "[1/3] llama-server (MioTTS-1.7B) :8002"
    if start_if_idle 8002 "llama-server"; then
        if [[ ! -f "${MODEL_PATH}" ]]; then
            echo "  ✗ Model not found: ${MODEL_PATH}"
            exit 1
        fi
        nohup llama-server -m "${MODEL_PATH}" \
            -c 8192 --cont-batching --batch_size 8 --port 8002 \
            > "${LOG_DIR}/llama-server.log" 2>&1 &
        echo "  → started PID=$!"
    fi

    echo "[2/3] run_server.py (MioCodec) :8001"
    if start_if_idle 8001 "run_server.py"; then
        cd "${MIOTTS_INFERENCE_DIR}"
        nohup env PYTHONUNBUFFERED=1 uv run python run_server.py \
            --llm-base-url http://localhost:8002/v1 --device cpu \
            > "${LOG_DIR}/run-server.log" 2>&1 &
        echo "  → started PID=$!"
    fi
fi

if [[ "${TTS_ENGINE}" == "irodori" ]]; then
    echo "[2/2] body-streamer (uvicorn) :8000"
else
    echo "[3/3] body-streamer (uvicorn) :8000"
fi
if start_if_idle 8000 "body-streamer"; then
    cd "${AI_TUBER_DIR}"
    nohup env PYTHONUNBUFFERED=1 PYTHONPATH=src .venv/bin/python \
        -m uvicorn body.streamer.main:app --port 8000 \
        > "${LOG_DIR}/body-streamer.log" 2>&1 &
    echo "  → started PID=$!"
fi

echo ""
if [[ "${TTS_ENGINE}" == "irodori" ]]; then
    PORTS=(8003 8000)
else
    PORTS=(8002 8001 8000)
fi

MAX_WAIT=30
ELAPSED=0
echo "Waiting for services (up to ${MAX_WAIT}s)..."
while (( ELAPSED < MAX_WAIT )); do
    ALL_UP=true
    for port in "${PORTS[@]}"; do
        if ! lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
            ALL_UP=false
            break
        fi
    done
    if $ALL_UP; then break; fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

echo ""
echo "=== Status ==="
for port in "${PORTS[@]}"; do
    if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  ✅ :${port}"
    else
        echo "  ❌ :${port} (failed to start after ${MAX_WAIT}s, check ${LOG_DIR}/)"
    fi
done

# OBS WebSocket は GUI 必須なので「未起動なら open -a」ヒント
if lsof -nP -iTCP:4455 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  ✅ :4455 OBS WebSocket"
else
    echo "  ⚠️  :4455 OBS WebSocket not found — start OBS Studio manually:"
    echo "      open -a OBS"
fi

echo ""
echo "Logs: ${LOG_DIR}/"
echo "Stop: ${AI_TUBER_DIR}/scripts/stop_all.sh"
