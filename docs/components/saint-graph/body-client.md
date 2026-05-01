# Body クライアント

Saint Graph から Body へ指令を送信する REST クライアントについて説明します。

---

## 役割

`body_client.py` は Body サービスへの HTTP リクエストをカプセル化し、シンプルな API を提供します。
内部的に共通の `_request` メソッドを使用することで、通信処理とエラーハンドリングを一元管理しています。

---

## BodyClient クラス

### 初期化

```python
from saint_graph.body_client import BodyClient

# RUN_MODE に応じて自動的に URL が設定される
body_client = BodyClient(base_url=config.BODY_URL)
# CLI モード: http://body-cli:8000
# Streamer モード: http://body-streamer:8002
```

---

## メソッド

### speak(text, style, speaker_id=None, caption_title=None, caption_summary=None)

テキストを発話させます。

```python
await body_client.speak(
    text="こんにちは、今日は良い天気ですね！",
    style="joyful",
    caption_title="今日のニュース",
    caption_summary="見出しの要約"
)
```

**パラメータ**:
- `text` (str): 発話させるテキスト
- `style` (str): 発話スタイル（neutral, joyful, fun, angry, sad）
- `speaker_id` (int, Optional): 声の ID（style より優先）
- `caption_title` / `caption_summary` (str, Optional): 最初の音声再生直前に同期更新するニュースキャプション

**内部処理** (Streamer モード):
1. リクエストを送信し、Body 側で内部キューに追加
2. キューにより、前後の発話や表情変更と順次実行される
3. caption 指定がある場合は、Body worker が音声生成完了後・再生開始直前に OBS caption を更新
4. **非ブロッキング**: 本メソッドはキューへの追加が完了した時点で即時復帰します（Mind 側で待機する必要がありません）。

`queue_speak()` を使うと、`action_id` を含む REST レスポンスを取得できます。

### change_emotion(emotion)

アバターの表情を変更します。

```python
await body_client.change_emotion("joyful")
```

**パラメータ**:
- `emotion` (str): 感情（neutral, joyful, fun, angry, sad）

**内部処理** (Streamer モード):
- 表情変更リクエストを内部キューに追加し、発話と同期して順次処理されます。

### get_comments()

視聴者コメントを取得します。

```python
comments = await body_client.get_comments()
# [{"author": "...", "message": "...", "timestamp": "..."}]
```

**戻り値**:
- `List[Dict[str, Any]]`: コメントのリスト。CLI モードでも Streamer モードと互換性のある形式で返されます。

### start_broadcast(config) / stop_broadcast()

配信または録画を開始・停止します。

```python
# 配信開始
await body_client.start_broadcast({"title": "Live Stream"})

# ... 配信処理 ...

# 配信停止
await body_client.stop_broadcast()
```

- **概要**: 録画と配信を統合したエンドポイントです。環境変数 `STREAMING_MODE` に基づき、Body 側で自動的に OBS 録画か YouTube Live 配信かを判定します。
- **責任範囲**: `start_broadcast` は OBS 録画 / YouTube Live 配信開始と stream active 待機のみを行います。caption clear・scene 切替・auto-filler 起動は broadcast loop が presentation queue に投入します。
- **補足**: `stop_broadcast` は、キュー内のすべての発話が完了するのを待機してから停止処理を行いますが、呼び出し側でも必要に応じて `wait_for_queue` を使用できます。

### wait_for_queue(timeout=300.0)

キュー内のすべての処理（発話、表情変更）が完了するまで待機します。

```python
await body_client.wait_for_queue()
```

- **用途**: 配信のリズムを整えるために、1つのフェーズやターンが終わる際に AI が最後まで話し終えるのを待つために使用します。通信による「間」を詰めつつ、対話のリズムを維持するための「いいとこ取り」構成の要となります。

### wait_for_queue_strict(action_ids=None, timeout=300.0, recent_count=None)

キューの消化を待ったうえで、指定した `action_id` がすべて `completed` になったか検査します。

```python
res = await body_client.queue_scene_switch("kurara_main")
ok = await body_client.wait_for_queue_strict([res["action_id"]])
```

- `True`: 指定 action がすべて成功
- `False`: 1 件以上が失敗、キャンセル、または未知の action_id

### presentation queue 操作

以下のメソッドは caption / scene / BGM / filler を presentation queue に投入します。

| メソッド | 用途 |
|---|---|
| `queue_caption_news(title, summary)` | ニュースキャプション更新 |
| `queue_caption_clear()` | ニュースキャプション消去 |
| `queue_scene_switch(scene_name)` | OBS シーン切替 |
| `queue_bgm_switch(bgm_id)` | ループ系 BGM 切替 |
| `queue_bgm_play(bgm_id, restart=True)` | BGM / SE 再生 |
| `queue_bgm_stop(bgm_id)` | BGM / SE 停止 |
| `queue_auto_filler_start()` | auto-filler ループ開始 |
| `queue_auto_filler_stop()` | auto-filler ループ停止 |

既存の `update_news_caption()` / `clear_news_caption()` / `switch_scene()` / `switch_bgm()` / `play_bgm()` / `stop_bgm()` / `start_auto_filler()` / `stop_auto_filler()` も同じ endpoint を呼びますが、戻り値は従来通り表示用文字列です。action_id が必要な場合は `queue_*` メソッドを使います。

---

## 設計の詳細

### 共通リクエスト処理 (`_request`)

`BodyClient` 内部では、重複を避けるために共通のプライベートメソッドを使用しています。

```python
async def _request(self, method, path, payload=None, timeout=DEFAULT_TIMEOUT):
    # httpx.AsyncClient による送信と共通のエラーハンドリング
    ...
```

### エラーハンドリング

`BodyClient` の各メソッドは内部で `httpx` の例外をキャッチし、エラー発生時は詳細なログを出力します。また、呼び出し側の `Mind` ロジックが通信エラーによってクラッシュするのを防ぐため、成功/失敗のステータス文字列や空のリストを返します。

- **詳細なロギング**: `ConnectError`, `TimeoutException`, `HTTPStatusError` などを個別にキャッチし、原因を特定しやすくしています。
- **フォールバック**: 通信失敗時は "Error: ..." という文字列を返すか、空のリストを返すことで、エージェントのループが継続できるように設計されています。

---

## 関連ドキュメント

- [README](./README.md) - Saint Graph 概要
- [通信プロトコル](../../architecture/communication.md) - REST API 仕様
- [Body](../../components/body/README.md) - Body 実装
