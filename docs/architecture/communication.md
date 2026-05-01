# 通信プロトコル仕様

AI Tuber システムは **REST API** と **MCP** の2つの通信プロトコルを使い分けます。

---

## REST API インターフェース (Body)

Saint Graph から Body への指令送信に使用します。

### 接続先

- **CLI モード**: `http://body-cli:8000`
- **Streamer モード**: `http://body-streamer:8000`

`RUN_MODE` 環境変数により自動的に切り替わります。

---

## Body REST API エンドポイント

### 1. POST /api/speak

アバターに発話させます（presentation queue に追加して即時復帰）。

**Request Body**:
```json
{
  "text": "発話させるテキスト内容",
  "style": "neutral",  // neutral, joyful, fun, angry, sad
  "speaker_id": 46,    // オプション。指定された場合は style より優先
  "caption_title": "ニュース見出し",      // オプション
  "caption_summary": "ニュース本文要約"   // オプション
}
```

**Response**:
```json
{
  "status": "ok",
  "result": "Speech queued",
  "action_id": "uuid4"
}
```

**注意**:
- `speaker_id` が指定された場合、`style` は無視されます
- VoiceVox の話者ID を直接指定できます
- **Streamer モード**: 指定された発話内容は内部キューに追加され、順次再生されます。
- `caption_title` / `caption_summary` を指定した場合、Body worker は音声生成完了後、音声再生開始の直前にニュースキャプションを更新します。

### 2. POST /api/change_emotion

アバターの表情を変更します（presentation queue に追加して即時復帰）。

**Request Body**:
```json
{
  "emotion": "joyful"  // neutral, joyful, fun, angry, sad
}
```

**Response**:
```json
{
  "status": "ok",
  "result": "Emotion change queued",
  "action_id": "uuid4"
}
```

**注意**:
- API レベルでは `neutral` を使用（内部的に OBS のソース名 `normal` にマッピングされます）
- **Streamer モード**: 表情変更は内部キューに追加され、発話と同期して処理されます。


### 3. GET /api/comments

OBS overlay 表示用に直近のユーザーコメントを取得します。buffer は破壊しません。

**Response (共通)**:
```json
{
  "status": "ok",
  "comments": [
    {
      "author": "User",
      "message": "コメント内容",
      "timestamp": "2026-02-02T17:00:00+00:00"  // CLIモードでは省略される場合があります
    }
  ]
}
```

Saint Graph がコメント反応用に buffer を drain する場合は `POST /api/comments/consume` を使います。

### 4. presentation queue 操作用 endpoint

以下はすべて presentation queue に action を投入して即時復帰します。レスポンスには `action_id` が含まれます。

| Endpoint | 用途 | Request Body |
|---|---|---|
| `POST /api/caption/news` | ニュースキャプション更新 | `{"title": "...", "summary": "..."}` |
| `POST /api/caption/clear` | ニュースキャプション消去 | `{}` |
| `POST /api/scene/switch` | OBS シーン切替 | `{"scene": "kurara_main"}` |
| `POST /api/bgm/switch` | ループ系 BGM 切替 | `{"bgm_id": "news"}` |
| `POST /api/bgm/play` | BGM / SE 再生 | `{"bgm_id": "se", "restart": true}` |
| `POST /api/bgm/stop` | BGM / SE 停止 | `{"bgm_id": "se"}` |
| `POST /api/filler/play` | filler wav 再生 | `{"category": "aizuchi", "style": "neutral"}` |

### 5. POST /api/queue/wait

presentation queue の `join()` のみを待ちます。action の成功/失敗は判定しません。

### 6. POST /api/queue/wait_strict

presentation queue の消化を待った後、指定 action がすべて `completed` か検査します。1 件でも `failed` / `cancelled` / 未知の `action_id` があれば `false` を返します。

**Request Body**:
```json
{
  "action_ids": ["uuid4"],  // オプション。省略時は直近 N 件を検査
  "recent_count": 20        // オプション
}
```

**Response**:
```json
{
  "status": "ok",
  "result": true
}
```

### 7. POST /api/broadcast/start

配信または録画を開始します。Body サービスが `STREAMING_MODE` 環境変数に基づいて、YouTube Live 配信か OBS 録画かを自動判定します。

**Request Body** (オプション):
```json
{
  "title": "配信タイトル",
  "description": "配信の説明",
  "scheduled_start_time": "2024-12-31T00:00:00.000Z",
  "privacy_status": "private"
}
```

> CLIモードでは Body は空実装（No-op）として成功を返します。

**Response**:
```json
{
  "status": "ok",
  "result": "YouTube Live配信を開始しました。ブロードキャストID: xxxxx"
}
```

### 8. POST /api/broadcast/stop

配信または録画を停止します。

**Response**:
```json
{
  "status": "ok",
  "result": "配信を停止しました"
}
```

---

## MCP インターフェース (Autonomous Tools)

AI（LLM）が状況に応じて自律的に呼び出す外部ツールに使用します。システム管理や基盤操作のためのツールはここには含めず、AIの「能力」を拡張するもののみを定義します。
詳細は [src/tools/README.md](../../src/tools/README.md) を参照してください。

### 接続先

- **Weather Tool**: `http://tools-weather:8001/sse`

環境変数 `WEATHER_MCP_URL` で設定可能です。

---

## Weather MCP Server

### get_weather

指定された場所の天気情報を取得します。

**Description**: Retrieve weather information for a specified location and date.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "location": {
      "type": "string",
      "description": "都市名や地域名（例: Tokyo, Fukuoka）"
    },
    "date": {
      "type": "string",
      "description": "日付（YYYY-MM-DD）または相対日時（today, tomorrow）。省略時は現在・直近の天気。"
    }
  },
  "required": ["location"]
}
```

**呼び出し例**:
```python
# AI が自律的にツールとして呼び出し
result = await agent.run("東京の明日の天気を教えて")
# → AI が get_weather(location="Tokyo", date="tomorrow") を使用
```

---

## 呼び出しパターンの棲み分け

| 機能 | 通信方式 | 起動トリガー | 理由 |
|---|---|---|---|
| **発話・感情変更** | REST | **プログラム（Parser）** | エラーを許容せず、常に確実に実行するため |
| **caption / scene / BGM / filler** | REST → presentation queue | **プログラム（Loop）** | 視聴者向けの時系列を worker で一元管理するため |
| **コメント取得** | REST | **プログラム（Loop）** | 定期的なポーリングが必要なため |
| **録画・配信制御** | REST | **プログラム（Setup/Teardown）** | システムの開始・終了と同期させるため |
| **天気予報取得** | MCP | **AI（Autonomous）** | 必要かどうかを AI が判断し、動的に拡張したいため |

---

## 環境変数

### Saint Graph 設定

| 変数名 | デフォルト値 | 説明 |
|--------|-------------|------|
| `RUN_MODE` | `cli` | 動作モード（`cli` または `streamer`） |
| `BODY_URL` | (自動設定) | Body サービスの URL（RUN_MODE により決定） |
| `WEATHER_MCP_URL` | `http://tools-weather:8001/sse` | MCP サーバーの URL |
| `GOOGLE_API_KEY` | (必須) | Google Gemini API キー |
| `MODEL_NAME` | `gemini-2.5-flash-lite` | 使用する Gemini モデル |
| `ADK_TELEMETRY` | `false` | Google ADK テレメトリの有効化 |
| `NEWS_DIR` | `/app/data/news` | ニュース原稿ディレクトリ |
| `MAX_WAIT_CYCLES` | `30` | ニュース終了後の沈黙タイムアウト（秒） |

### Body 設定

| 変数名 | デフォルト値 | 説明 |
|--------|-------------|------|
| `PORT` | `8000` (CLI) / `8002` (Streamer) | REST サーバーのポート |
| `VOICEVOX_HOST` | `voicevox` | VoiceVox Engine のホスト名 |
| `OBS_HOST` | `obs-studio` | OBS WebSocket のホスト名 |
| `YOUTUBE_CLIENT_SECRET_JSON` | - | OAuth 認証情報の JSON 文字列 |
| `YOUTUBE_TOKEN_JSON` | - | OAuth トークンの JSON 文字列 |

---

## タイムアウトと制約

### Saint Graph

- **Polling Interval**: `1.0s` (コメント取得の間隔)
- **Connect Timeout**: `30s`
- **Tool Execution Timeout**: `30s`

### Body

- **OBS WebSocket 接続リトライ**: 最大5回、指数バックオフ（2秒 → 4秒 → 8秒...）
- **YouTube Live Chat ID取得リトライ**: 最大10回、10秒間隔

---

## 関連ドキュメント

- [システム概要](./overview.md) - 全体アーキテクチャ
- [Saint Graph - Body Client](../components/saint-graph/body-client.md) - REST クライアント実装
- [Body](../components/body/README.md) - REST サーバー実装
- [Body Tools - Weather](../components/body/tools/weather.md) - MCP サーバー実装
