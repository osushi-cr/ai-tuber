# ai-tuber — Claude Code 向けプロジェクトガイド

## タイトル・概要

- **プロジェクト**: AITuber 配信システム（fork of Ren Studio）
- **キャラクター**: くらら（妹キャラ・MioTTS-1.7B でローカル合成・LLM は Gemini 系）
- **目的**: 個人開発の AITuber が AI ニュース等を配信する。OBS + YouTube Live + ローカル TTS の三位一体構造（魂 Saint Graph / 肉体 Body / 精神 Mind）
- **ペンネーム**: お寿司（@osushi_cr）
- **branch 既定**: `feature/voice-adapter-miotts`（現主力）

詳細アーキテクチャは `README.md`、エンジニアリング規約・Agent ルールは `AGENTS.md` を参照。Claude Code は本ファイルを主に読み、AGENTS.md は他 Agent（Codex 等）と共通の規約として併用する。

---

## 本配信手順（汎用版）

> 5/2 の本配信デビュー手順を汎用化したもの。日付・トピックだけ差し替えて使う。原版: `~/src/work/ai-management/03_projects/kurara-aituber/live-stream-prep-2026-05-02.md`

### 配信メタデータ（毎回ここを差し替える）

#### タイトルテンプレ

```
【AITuber】くららの{TOPIC} #{YYYY-MM-DD} · {SUBTITLE}
```

- `{TOPIC}`: 「AIニュース解説」「{特集テーマ}」など
- `{YYYY-MM-DD}`: 配信日（例 `2026-05-03`）
- `{SUBTITLE}`: 短いリード文（例「AI の最新ニュースを妹とゆるチャット」）

例:
```
【AITuber】くららのAIニュース解説 #2026-05-03 · AI の最新ニュースを妹とゆるチャット
```

#### 概要欄テンプレ

```
くらら、配信中だよ〜✨
お兄ちゃん（@osushi_cr）が作ってくれた AITuber「くらら」が、
今日の {TOPIC} を {N} 件、ゆるーくお話ししながら視聴者のコメントと交流するよ！

▼ こんな配信
・くららが {TOPIC} を {N} 件、順番に紹介
・読み終わったあとは Q&A コーナーで視聴者のコメント拾います
・気になることや感想、何でも書き込んでね！

▼ 中の人について
・お寿司（@osushi_cr）が個人開発で動かしてる AITuber
・声は MioTTS-1.7B（Apple Silicon ローカル合成）
・LLM は Gemini 3.1 Flash Lite Preview
・配信制御・ニュース選定はスクリプトで自動化

▼ ハッシュタグ
#AITuber #くらら #{TOPIC_HASHTAG} #VTuber

▼ お問い合わせ
@osushi_cr （X）までお気軽に！
```

差し替えポイント:
- `{TOPIC}` / `{N}` / `{TOPIC_HASHTAG}` を配信内容に合わせる
- LLM／TTS のバージョンが変わったら「中の人について」の行を更新

#### サムネイル

- パス: `data/mind/kurara/assets/contents/thumbnail.png`
- 仕様: 1280×720 / 2MB 以内
- 構成: kurara_main の intro overlay 装飾 +「{TOPIC}」+ くらら立ち絵 + LIVE バッジ
- アップロード: YouTube Studio で broadcast 作成後に**手動アップロード**（saint_graph 自動アップロードなし）

#### ニュース／台本

- 通常運用: `data/news/news_script.md` を **ai-news-digest が朝 7:00 に自動更新**する。配信時刻に最新版が入っているか確認するだけで OK
- 特集回: 手動で `data/news/news_script.md` を書き換えてからコミット → push してから配信開始

### 起動手順

#### 0. quota 残量確認

YouTube Cloud Console で当日の使用量を確認。**2,000 unit 以上**残量があるか確認（対策後 1 配信 ≈ 350 unit / broadcast 作成系 200 + chat polling 130-150）。

#### 1. .env を YouTube Live モードに

```bash
cd ~/src/github.com/osushi-cr/ai-tuber
sed -i '' 's/^STREAMING_MODE=false/STREAMING_MODE=true/' .env
```

配信後は必ず `false` に戻す（後始末セクション参照）。

#### 2. バックエンド起動

```bash
~/src/github.com/osushi-cr/ai-tuber/scripts/start_all.sh
# llama-server (8002) / MioCodec (8001) / body-streamer (8000) / OBS (4455) を確認
```

#### 3. saint_graph 起動

`/api/broadcast/start` を**人間も AI も独断で curl しない**。`saint_graph.main` を起動すれば内部から1回だけ呼ばれる。二重実行で 1 配信 300 unit 余分に消費する事故が過去にあった。

```bash
LOG=/tmp/saint_graph_pub_$(date +%Y%m%d_%H%M%S).log
echo "LOG=$LOG"
cd ~/src/github.com/osushi-cr/ai-tuber && \
  nohup env PYTHONUNBUFFERED=1 STREAMING_MODE=true \
    STREAM_TITLE="【AITuber】くららの{TOPIC} #{YYYY-MM-DD} · {SUBTITLE}" \
    STREAM_DESCRIPTION="$(cat <<'EOF'
{概要欄テンプレを差し替えてここに貼る}
EOF
)" \
    STREAM_PRIVACY="public" \
    CHARACTER_NAME=kurara WEATHER_MCP_URL= \
    BODY_URL=http://127.0.0.1:8000 NEWS_DIR=news \
    PYTHONPATH=$HOME/src/github.com/osushi-cr/ai-tuber/src \
    ./.venv-saint/bin/python -m saint_graph.main > "$LOG" 2>&1 &
echo "saint_graph PID=$!"
```

`STREAM_PRIVACY` は `public` / `unlisted` / `private` から選ぶ。検証配信は `unlisted`、本配信は `public`。

#### 4. broadcast 作成完了を待ってサムネアップロード

```bash
sleep 15
grep "Broadcast ID:" "$LOG"
# → 表示された broadcast_id を YouTube Studio で開いて
#   data/mind/kurara/assets/contents/thumbnail.png を手動アップロード
```

#### 5. 視聴 URL を提示

```bash
BROADCAST_ID=$(grep "Broadcast ID:" "$LOG" | awk -F: '{print $NF}' | tr -d ' ')
echo "視聴 URL: https://www.youtube.com/watch?v=${BROADCAST_ID}"
```

X 告知に貼る。

#### 6. 配信進行を監視

```bash
tail -f "$LOG" | grep -E "Phase transition|runaway|retry|QUOTA"
```

注目ログ:
- `Phase transition` — intro→news→qa→closing の遷移
- `runaway` / `retry` — TTS 暴走検知（temp 0.8 + 閾値 0.35 で事前救済される想定）
- `QUOTA` — quotaExceeded アラート（残量不足のサイン）

### 後始末

配信完了を確認したら必ず実行する:

```bash
~/src/github.com/osushi-cr/ai-tuber/scripts/stop_all.sh
sed -i '' 's/^STREAMING_MODE=true/STREAMING_MODE=false/' ~/src/github.com/osushi-cr/ai-tuber/.env
```

`.env` を `false` に戻し忘れると次回ローカル E2E が誤って配信モードで走る。

配信後の振り返り（推奨）:
- broadcast ID / privacy / 配信時刻 / 全体長 / log path
- 完走タイムライン（intro 開始〜closing 完了）
- 観察課題（視聴者からの指摘・絵柄違和感・音ズレ等）
- 次回への持ち越し fix
- 記録先: `~/src/work/ai-management/03_projects/kurara-aituber/youtube-live-result-{YYYY-MM-DD}-{n}.md`

---

## 配信前チェックリスト（毎回確認）

| # | 項目 | コマンド／場所 |
|---|---|---|
| 1 | quota 残量 ≧ 2,000 unit | YouTube Cloud Console |
| 2 | branch が想定通り | `git status --short --branch` |
| 3 | 未コミット変更が配信に必要か仕分け済 | `git status` で物理確認 |
| 4 | 必要なコミットを push 済 | `git log origin/{branch}..HEAD` 空であること |
| 5 | `data/news/news_script.md` が当日更新済 | 先頭の日付確認 |
| 6 | サムネ画像が最新（特集回は差し替え） | `data/mind/kurara/assets/contents/thumbnail.png` |
| 7 | OBS の current scene を `waiting` にしておく（保険）| OBS 手動操作 |
| 8 | `.env` の `STREAMING_MODE=true` 切替済 | `grep STREAMING_MODE .env` |

---

## quota 保護ルール（必読）

- **AI も人間も `/api/broadcast/start` を独断で curl しない**。`saint_graph.main` 起動だけで自動的に呼ばれる
- 二重実行で 1 配信 300 unit 余分に消費する事故が過去発生（2026-04-30）
- 検証配信は `STREAM_PRIVACY=unlisted` で行い、本配信のみ `public`
- `quotaExceeded` macOS 通知が出たら即座に saint_graph を kill して当日の配信を打ち切る

---

## 関連ドキュメント

- `README.md` — 三位一体構造／全体アーキテクチャ／環境変数一覧／キャラクター追加方法
- `AGENTS.md` — Agent 共通規約（人間 7：AI 3 比率／和解プロトコル／Tech Standards）
- `docs/README.md` — 体系化ドキュメント索引
- `docs/knowledge/youtube-setup.md` — YouTube OAuth セットアップ
- `docs/knowledge/troubleshooting.md` — 過去のトラブル事例

ai-management 側の作業ログ:
- `~/src/work/ai-management/03_projects/kurara-aituber/` — handoff／本配信準備パッケージ／配信結果記録
