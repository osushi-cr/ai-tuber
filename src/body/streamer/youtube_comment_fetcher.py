"""Subprocess script for fetching YouTube Live chat comments"""
import sys
import json
import time
import os
import subprocess
from googleapiclient.errors import HttpError
from body.streamer.youtube_auth import YouTubeAuth


# polling 間隔のミニマム（秒）。 YouTube が pollingIntervalMillis でもっと早く叩いて
# いいと言ってきても、 こちらの最低間隔を守って quota 浪費を抑える。
# 4s で叩くと 13 分配信で 200 回 × 5 unit = 1,000 unit 消費。 30s で 1/7.5 に。
MIN_POLL_INTERVAL = float(os.getenv("YOUTUBE_CHAT_POLL_INTERVAL", "30"))


def _trigger_quota_alert(broadcast_id: str = "") -> None:
    """quotaExceeded 検知時の強アラート。 macOS 通知 + 日本語音声アナウンスで
    ユーザーに「YouTube Studio で配信を手動停止」を促す。"""
    msg_short = "YouTube quota 枯渇。 Studio で配信終了をクリック"
    print(f"!!! QUOTA EXCEEDED !!! {msg_short} (broadcast={broadcast_id})", file=sys.stderr, flush=True)
    try:
        subprocess.Popen(
            [
                "osascript", "-e",
                'display notification "YouTube Studio で「配信を終了」を手動でクリックしてください" '
                'with title "YouTube quota exceeded" sound name "Sosumi"',
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["say", "-v", "Kyoko", "YouTube のクォータが切れました。 ユーチューブスタジオで配信を手動で停止してください"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def fetch_comments(video_id: str):
    """
    Fetch comments from YouTube Live chat and output as JSON lines.
    
    Args:
        video_id: YouTube broadcast/video ID
    """
    print(f"DEBUG: Starting comment fetch for video {video_id} using YouTubeAuth", file=sys.stderr, flush=True)
    
    try:
        # Build YouTube API client using centralized auth
        youtube = YouTubeAuth.get_service()
        print(f"DEBUG: Successfully authenticated with YouTubeAuth", file=sys.stderr, flush=True)
        
    except Exception as e:
        error_msg = f"YouTube authentication failed: {e}"
        print(json.dumps({"error": error_msg}), flush=True)
        print(f"ERROR: {error_msg}", file=sys.stderr, flush=True)
        return
    
    # Get the live chat ID from the video (with retry logic)
    live_chat_id = None
    max_retries = 10
    retry_interval = 10
    
    for attempt in range(max_retries):
        try:
            video_response = youtube.videos().list(
                part='liveStreamingDetails',
                id=video_id
            ).execute()
            
            if not video_response.get('items'):
                print(json.dumps({"error": f"Video {video_id} not found"}), flush=True)
                time.sleep(retry_interval)
                continue
            
            live_streaming_details = video_response['items'][0].get('liveStreamingDetails', {})
            live_chat_id = live_streaming_details.get('activeLiveChatId')
            
            if live_chat_id:
                print(f"DEBUG: Found live chat ID: {live_chat_id}", file=sys.stderr, flush=True)
                break
            else:
                print(f"DEBUG: Live chat not active yet (attempt {attempt + 1}/{max_retries})", file=sys.stderr, flush=True)
                if attempt < max_retries - 1:
                    time.sleep(retry_interval)
                    
        except HttpError as e:
            error_msg = f"YouTube API error getting live chat ID: {e}"
            print(json.dumps({"error": error_msg}), flush=True)
            print(f"ERROR: {error_msg}", file=sys.stderr, flush=True)
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
            else:
                return
    
    if not live_chat_id:
        error_msg = f"No active live chat found after {max_retries} attempts"
        print(json.dumps({"error": error_msg}), flush=True)
        print(f"ERROR: {error_msg}", file=sys.stderr, flush=True)
        return

    
    next_page_token = None
    polling_interval = MIN_POLL_INTERVAL

    print(f"DEBUG: Starting comment polling loop (min interval {MIN_POLL_INTERVAL}s)", file=sys.stderr, flush=True)

    # Polling loop
    while True:
        try:
            request = youtube.liveChatMessages().list(
                liveChatId=live_chat_id,
                part='snippet,authorDetails',
                pageToken=next_page_token
            )
            response = request.execute()

            # Output new comments as JSON lines
            for item in response.get('items', []):
                comment = {
                    'author': item['authorDetails']['displayName'],
                    'message': item['snippet']['displayMessage'],
                    'timestamp': item['snippet']['publishedAt']
                }
                print(json.dumps(comment), flush=True)

            # Update next page token. polling 間隔は YouTube 提案値と env min の max。
            next_page_token = response.get('nextPageToken')
            suggested = response.get('pollingIntervalMillis', 5000) / 1000.0
            polling_interval = max(MIN_POLL_INTERVAL, suggested)

            time.sleep(polling_interval)

        except HttpError as e:
            error_msg = f"API error while fetching comments: {e}"
            print(json.dumps({"error": error_msg}), flush=True)
            print(f"ERROR: {error_msg}", file=sys.stderr, flush=True)
            if "quotaExceeded" in str(e):
                _trigger_quota_alert(video_id)
                # quota 復活待ち。 リセットは PT 0:00（日本 17:00 頃）なので 60s 単位で polling 緩和
                time.sleep(60)
            else:
                time.sleep(5)
        except KeyboardInterrupt:
            print("DEBUG: Keyboard interrupt received, exiting", file=sys.stderr, flush=True)
            break
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            print(json.dumps({"error": error_msg}), flush=True)
            print(f"ERROR: {error_msg}", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: youtube_comment_fetcher.py <video_id>"}), flush=True)
        sys.exit(1)
    
    video_id = sys.argv[1]
    fetch_comments(video_id)
