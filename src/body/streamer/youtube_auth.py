import os
import json
import logging
from typing import Optional, Tuple, Any
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# Fallback paths for local development
YOUTUBE_CLIENT_SECRET_PATH = os.path.join("data", "youtube_client_secret.json")
YOUTUBE_TOKEN_PATH = os.path.join("data", "youtube_token.json")

class YouTubeAuth:
    """Centralized authentication management for YouTube Data API v3."""

    SCOPES = ["https://www.googleapis.com/auth/youtube"]

    @classmethod
    def get_credentials(cls) -> Optional[Credentials]:
        """
        Load and return YouTube credentials from environment variables or file.
        Automatically handles BOM and basic JSON validation.
        """
        creds = None
        
        # 1. Try to load from environment variable (JSON string) first
        token_json_str = os.getenv("YOUTUBE_TOKEN_JSON")
        if token_json_str:
            try:
                # Remove UTF-8 BOM if present
                if token_json_str.startswith('\ufeff'):
                    token_json_str = token_json_str[1:]
                token_info = json.loads(token_json_str)
                # Use scopes from token or default
                scopes = token_info.get('scopes', cls.SCOPES)
                creds = Credentials.from_authorized_user_info(token_info, scopes)
                logger.info("Loaded YouTube credentials from YOUTUBE_TOKEN_JSON environment variable")
            except Exception as e:
                logger.error(f"Failed to load credentials from YOUTUBE_TOKEN_JSON: {e}")

        # 2. Fallback to file if not loaded from env
        if not creds and os.path.exists(YOUTUBE_TOKEN_PATH):
            try:
                creds = Credentials.from_authorized_user_file(YOUTUBE_TOKEN_PATH, cls.SCOPES)
                logger.info(f"Loaded YouTube credentials from {YOUTUBE_TOKEN_PATH}")
            except Exception as e:
                logger.error(f"Failed to load credentials from {YOUTUBE_TOKEN_PATH}: {e}")
        
        # 3. Handle token refresh if possible
        if creds and creds.expired and creds.refresh_token:
            logger.info("YouTube token expired, attempting to refresh...")
            try:
                creds.refresh(Request())
                logger.info("YouTube token refreshed successfully")
            except Exception as e:
                logger.warning(f"Failed to refresh YouTube token: {e}")
                # Return expired creds, higher level might choose to start OAuth flow
        
        return creds

    @classmethod
    def get_service(cls, creds: Optional[Credentials] = None) -> Any:
        """
        Build and return the YouTube service client.
        If creds is not provided, it will attempt to load them.
        """
        if not creds:
            creds = cls.get_credentials()
            
        if not creds or not creds.valid:
            raise ValueError("No valid YouTube credentials found. Authorization required.")
            
        return build('youtube', 'v3', credentials=creds)

    @classmethod
    def start_oauth_flow(cls) -> Tuple[Any, Credentials]:
        """
        Start an interactive OAuth 2.0 flow to obtain new credentials.
        Desktop OAuth client + InstalledAppFlow.run_local_server() \u3092\u4f7f\u3046\u305f\u3081\u3001
        \u30d6\u30e9\u30a6\u30b6\u304c\u81ea\u52d5\u3067\u958b\u304d\u3001\u8a8d\u53ef\u5f8c localhost \u306b\u30ea\u30c0\u30a4\u30ec\u30af\u30c8\u3055\u308c\u3066\u30b3\u30fc\u30c9\u8cbc\u308a\u623b\u3057\u4e0d\u8981\u3002
        Google \u304c deprecated \u3057\u305f oob (out-of-band) \u30d5\u30ed\u30fc\u306f\u4f7f\u308f\u306a\u3044\u3002
        """
        flow: Optional[InstalledAppFlow] = None
        client_secret_json_str = os.getenv("YOUTUBE_CLIENT_SECRET_JSON")

        if client_secret_json_str:
            if client_secret_json_str.startswith('\ufeff'):
                client_secret_json_str = client_secret_json_str[1:]
            client_config = json.loads(client_secret_json_str)
            flow = InstalledAppFlow.from_client_config(client_config, scopes=cls.SCOPES)
            logger.info("Initialized YouTube OAuth flow from YOUTUBE_CLIENT_SECRET_JSON")
        elif os.path.exists(YOUTUBE_CLIENT_SECRET_PATH):
            flow = InstalledAppFlow.from_client_secrets_file(
                YOUTUBE_CLIENT_SECRET_PATH, scopes=cls.SCOPES
            )
            logger.info(f"Initialized YouTube OAuth flow from {YOUTUBE_CLIENT_SECRET_PATH}")

        if not flow:
            raise FileNotFoundError("YouTube client secret not found in environment or file.")

        # \u30d6\u30e9\u30a6\u30b6\u3092\u8d77\u52d5\u3057\u3001localhost \u306e\u30e9\u30f3\u30c0\u30e0\u30dd\u30fc\u30c8\u3067\u30b3\u30fc\u30eb\u30d0\u30c3\u30af\u3092\u53d7\u3051\u308b
        creds = flow.run_local_server(port=0, prompt='consent')

        # \u30c8\u30fc\u30af\u30f3\u3092\u30d5\u30a1\u30a4\u30eb\u306b\u4fdd\u5b58
        try:
            os.makedirs(os.path.dirname(YOUTUBE_TOKEN_PATH), exist_ok=True)
            with open(YOUTUBE_TOKEN_PATH, 'w') as token_file:
                token_file.write(creds.to_json())
            logger.info(f"Saved new credentials to {YOUTUBE_TOKEN_PATH}")
        except Exception as e:
            logger.warning(f"Could not save tokens to {YOUTUBE_TOKEN_PATH}: {e}")

        return cls.get_service(creds), creds


if __name__ == "__main__":
    # 初回 OAuth 認可フロー: `python -m body.streamer.youtube_auth` で起動。
    # data/youtube_client_secret.json を読み込み、表示された URL をブラウザで開いて
    # 認可コードを貼り戻すと data/youtube_token.json が生成される。
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    YouTubeAuth.start_oauth_flow()
    print(f"\nDone. Token saved to {YOUTUBE_TOKEN_PATH}")
