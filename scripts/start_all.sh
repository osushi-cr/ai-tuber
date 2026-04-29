#!/usr/bin/env bash
# kurara AITuber バックエンド3プロセスを順次起動するワンコマンドスクリプト。
# OBS Studio は GUI アプリなので手動起動してもらう前提。

set -uo pipefail

LOG_DIR="${HOME}/.cache/ai-tuber/logs"
MODEL_PATH="${MIOTTS_MODEL_PATH:-${HOME}/.cache/miotts-models/MioTTS-1.7B-Q4_K_M.gguf}"
MIOTTS_INFERENCE_DIR="${HOME}/src/personal/MioTTS-Inference"
AI_TUBER_DIR="${HOME}/src/github.com/osushi-cr/ai-tuber"

mkdir -p "${LOG_DIR}"

start_if_idle() {
    local port="$1" name="$2"
    if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  ✓ ${name} already running on :${port}"
        return 1
    fi
    return 0
}

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
    nohup uv run python run_server.py \
        --llm-base-url http://localhost:8002/v1 --device cpu \
        > "${LOG_DIR}/run-server.log" 2>&1 &
    echo "  → started PID=$!"
fi

echo "[3/3] body-streamer (uvicorn) :8000"
if start_if_idle 8000 "body-streamer"; then
    cd "${AI_TUBER_DIR}"
    nohup env PYTHONPATH=src .venv/bin/python \
        -m uvicorn body.streamer.main:app --port 8000 \
        > "${LOG_DIR}/body-streamer.log" 2>&1 &
    echo "  → started PID=$!"
fi

echo ""
echo "Waiting 8s for services to come up..."
sleep 8

echo ""
echo "=== Status ==="
for port in 8002 8001 8000; do
    if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  ✅ :${port}"
    else
        echo "  ❌ :${port} (failed to start, check ${LOG_DIR}/)"
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
