#!/usr/bin/env python3
"""配信前に YouTube OAuth token を事前 refresh / 再認証する。

本配信中の interactive OAuth flow を絶対に走らせないためのスクリプト。
saint_graph 起動前に必ず実行する。

- token が valid: 何もしない
- expired + refresh_token あり: refresh
- それ以外（revoked など）: interactive flow（ブラウザ起動）

Usage:
    cd ~/src/github.com/osushi-cr/ai-tuber
    ./.venv/bin/python scripts/refresh_youtube_token.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

REPO = Path(__file__).resolve().parent.parent
CLIENT_SECRET = REPO / "data" / "youtube_client_secret.json"
TOKEN_PATH = REPO / "data" / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def main() -> int:
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        print("OK: token already valid")
        return 0

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
            print("OK: token refreshed")
            return 0
        except Exception as e:
            print(f"refresh failed: {e}", file=sys.stderr)

    if not CLIENT_SECRET.exists():
        print(f"NG: client_secret not found at {CLIENT_SECRET}", file=sys.stderr)
        return 2

    print("interactive OAuth flow (browser will open)...")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_PATH.write_text(creds.to_json())
    print("OK: re-authenticated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
