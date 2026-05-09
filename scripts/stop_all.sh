#!/usr/bin/env bash
# kurara AITuber バックエンド3プロセスを停止するスクリプト。
# 配信中なら /api/broadcast/stop で止めてから kill する。

set -uo pipefail

# 配信中なら止める（YouTube broadcast complete + OBS Stop）
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Stopping any active broadcast..."
    curl -s -X POST http://localhost:8000/api/broadcast/stop >/dev/null 2>&1 || true
    sleep 3
fi

stop_by_pattern() {
    local pattern="$1" label="$2"
    local pids
    pids=$(pgrep -f "${pattern}" || true)
    if [[ -n "${pids}" ]]; then
        echo "  ${label}: killing ${pids}"
        echo "${pids}" | xargs kill 2>/dev/null || true
    else
        echo "  ${label}: not running"
    fi
}

echo "Stopping backend processes..."
stop_by_pattern 'uvicorn body.streamer.main' 'body-streamer'
stop_by_pattern 'run_server.py.*--llm-base-url' 'run_server.py'
stop_by_pattern 'llama-server -m.*MioTTS' 'llama-server'
stop_by_pattern 'irodori_tts_server\.py' 'irodori-tts-server'

# saint_graph も併せて止める（残ってれば）
# `-m saint_graph.main` だと pgrep が -m をオプション解釈するため、頭の `-` を避ける
stop_by_pattern 'saint_graph\.main' 'saint_graph'

sleep 2

echo ""
echo "=== Status ==="
for port in 8002 8001 8003 8000; do
    if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  ⚠️  :${port} still listening"
    else
        echo "  ✅ :${port} stopped"
    fi
done
