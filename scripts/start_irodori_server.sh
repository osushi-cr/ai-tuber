#!/usr/bin/env bash
# Irodori-TTS HTTP server を Irodori-TTS の .venv で常駐起動する。
# body venv を ML スタックで汚染しないため、Python は Irodori-TTS 側を使う。

set -uo pipefail

IRODORI_DIR="${HOME}/src/personal/Irodori-TTS"
AI_TUBER_DIR="${HOME}/src/github.com/osushi-cr/ai-tuber"
LOG_DIR="${HOME}/.cache/ai-tuber/logs"
PORT="${IRODORI_PORT:-8003}"

mkdir -p "${LOG_DIR}"

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  ✓ irodori-tts-server already running on :${PORT}"
    exit 0
fi

if [[ ! -x "${IRODORI_DIR}/.venv/bin/python" ]]; then
    echo "  ✗ Irodori-TTS .venv not found: ${IRODORI_DIR}/.venv"
    exit 1
fi

cd "${IRODORI_DIR}" || exit 1
nohup env PYTHONUNBUFFERED=1 IRODORI_PORT="${PORT}" \
    PYTHONPATH="${IRODORI_DIR}" \
    "${IRODORI_DIR}/.venv/bin/python" \
    "${AI_TUBER_DIR}/scripts/irodori_tts_server.py" \
    > "${LOG_DIR}/irodori-tts-server.log" 2>&1 &

echo "  → started PID=$! port=${PORT}"
echo "  log: ${LOG_DIR}/irodori-tts-server.log"
