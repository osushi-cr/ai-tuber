# くらら ビジュアル設計書

## キャラ概要

| 項目 | 値 |
|---|---|
| 名前 | くらら（Kurara） |
| 由来 | Claude（AnthropicのAI）から |
| 年齢感 | 14歳前後（中学2年生くらい） |
| 体格 | 標準・小柄寄り、健康的 |
| 雰囲気 | 明るい・元気・お兄ちゃん大好きな妹 |

## カラーパレット（Anthropic Claude ブランド準拠）

| 部位 | カラー | Hex | 用途 |
|---|---|---|---|
| 髪 | Crail (warm coral) | `#CC785C` | メイン髪色（コーラルオレンジ系ボブ） |
| 髪ハイライト | Light coral | `#E89A82` | 髪の明部・前髪 |
| 髪シャドウ | Deep amber | `#A85540` | 髪の陰・毛先 |
| 肌 | Cream | `#F5F4EE` ベース | 標準肌色（やや暖色） |
| 頬 | Soft pink | `#F2C0B0` | 頬の赤み |
| 目 | Amber brown | `#8B5A3C` | 瞳ベース |
| 目ハイライト | Cream | `#FFF8E7` | 瞳の光 |

## 髪型

- **スタイル**: ボブカット（顎ライン）
- **前髪**: シースルー前髪（軽め・額がうっすら見える）
- **横**: 耳を覆う〜耳下ライン
- **質感**: ストレート、サラサラ
- **長さ目安**: 顎下〜首ライン

## 衣装（K-POPアイドル風セーラー制服）

参考画像ベースに以下の構成：

| 部位 | 説明 |
|---|---|
| ベレー帽 | 白いセーラー風ベレー帽。前面に「KR」または「Kurara」紺刺繍ロゴ。後ろに紺リボン尻尾（短め2本） |
| トップス | 白い半袖パフスリーブブラウス |
| セーラーカラー | 紺色、白ライン3本入り |
| ボウタイ | 大きめ紺色サテンリボン（胸元中央） |
| サスペンダースカート | 紺色、前面にゴールドボタン2列（4個） |
| スカート裾 | 白ライン3本（細・太・細の順） |
| 靴 | 白ソックス＋黒ローファー |

**カラーリング**:
- ベース紺 (Navy): `#1A2847`
- 白 (Off-white): `#F5F4EE`
- ボタン金 (Gold): `#D4A857`

## 表情5種（OBSアセット用）

ren と同様、5種の表情png をassets/ に配置する。

| 表情 | ファイル名 | 方向性 |
|---|---|---|
| normal | `ai_normal.png` | にっこり微笑み（ベースライン）。口角少し上がる、目はやさしく開く |
| joyful | `ai_joyful.png` | 大きい笑顔。目を細めて閉じ気味、口を大きく開いて歯見える、両頬赤み |
| fun | `ai_fun.png` | はしゃぎ。ウインク（片目つむる）、舌チラ、頬うっすら赤み |
| sad | `ai_sad.png` | 困り眉、口角下がる、目に涙うっすら |
| angry | `ai_angry.png` | ぷんぷん。眉吊り上げ、口角きゅっと結ぶ、頬膨らませる |

**共通仕様**:
- 同じキャラ・同じ衣装・同じ髪型・同じポーズ（**ハーフボディ／太もも中ほどまで・正面**）
- 表情のみ変化、ポーズは固定で OBSで切替可能化
- 背景透過 PNG（**1024×1536 推奨、縦長 2:3**）
- 解像度: アニメ調・線画＋セルシェード
- スカート全体・サスペンダー金ボタン・スカート裾の白3本ラインまで見える構図

## ChatGPT image-2 プロンプト（5表情分）

各プロンプトを ChatGPT (image-2 / GPT image generation) に投げて生成。
**重要**: 1枚目を生成したら参照画像として残り4枚で reference に使うと一貫性が出る。

### 共通ベース（プロンプト先頭に毎回貼る）

```
Anime-style illustration, semi-realistic shading, soft cell-shading.
Character: 14-year-old AITuber girl named Kurara.
Hair: warm coral-orange (#CC785C) bob cut, chin-length, see-through bangs, sleek straight texture.
Eyes: large amber-brown (#8B5A3C) anime eyes with cream highlights.
Skin: warm cream tone with soft pink cheek blush.
Outfit: K-pop idol sailor uniform — white sailor beret with "KR" navy embroidered logo on the front and navy ribbon tails at the back, white short-sleeve puff-sleeve blouse, navy sailor collar with three white stripes, large navy satin bowtie, navy suspender skirt with two rows of gold buttons, white triple-stripe hem.
Pose: half-body shot, visible from head down to mid-thigh, facing forward. The full skirt with gold suspender buttons and white triple-stripe hem must be clearly visible.
Background: transparent (PNG with alpha channel).
Resolution: 1024x1536 vertical (2:3 aspect ratio).
```

### 表情別プロンプト追記

| 表情 | プロンプト追記 |
|---|---|
| normal | `Expression: gentle smile, slight upturned mouth corners, soft open eyes, calm and friendly.` |
| joyful | `Expression: big bright smile with visible teeth, eyes narrowed in delight, both cheeks flushed pink, joyful sparkle. KEEP THE SAME POSE AS THE REFERENCE — body posture and hands identical, only facial expression changes.` |
| fun | `Expression: playful wink (one eye closed), tongue slightly out, soft cheek blush, mischievous fun energy. KEEP THE SAME POSE AS THE REFERENCE — hands and body posture identical, only facial expression changes.` |
| sad | `Expression: troubled eyebrows tilted down, mouth corners drooped, slight tears welling in eyes. KEEP THE SAME POSE AS THE REFERENCE — body posture, shoulders, and hands identical, only facial expression changes.` |
| angry | `Expression: pouty annoyed face, eyebrows raised in frustration, lips pressed tight in small frown, cheeks puffed. KEEP THE SAME POSE AS THE REFERENCE — body posture and hands identical, only facial expression changes.` |

## 生成手順（お兄ちゃん向け）

1. ChatGPT (https://chatgpt.com) を開く
2. 「画像を生成」を選択
3. 上の「共通ベース」＋「normal用追記」を貼って `ai_normal.png` を生成
4. 結果を**参照画像**として残り4表情を生成（一貫性確保）
5. 各pngを `data/mind/kurara/assets/` に保存
6. 透過処理されてない場合は remove.bg などで背景透過化

## ループ動画素材（Seedance 2.0 i2v）

### 生成リスト（11本）

| # | 元画像 | ファイル名 | プロンプト追記 | 用途 |
|---|---|---|---|---|
| 1 | normal.png | `normal_idle.mp4` | subtle natural breathing, slight head tilt, soft eye blinks | 待機ループ（最重要・LLM思考中の常時表示） |
| 2 | normal.png | `normal_speaking_a.mp4` | gentle speaking motion, subtle mouth movement, occasional head nod | 通常発話A |
| 3 | normal.png | `normal_speaking_b.mp4` | calm explanation gesture, slight head turn, eye contact engagement | 通常発話B |
| 4 | joyful.png | `joyful_a.mp4` | excited bouncing energy, eyes sparkle, joyful head bob | 喜びA |
| 5 | joyful.png | `joyful_b.mp4` | warm laughter, slight body sway, hair gentle movement | 喜びB |
| 6 | fun.png | `fun_a.mp4` | playful teasing wink, mischievous head tilt, tongue gentle motion | 楽しいA |
| 7 | fun.png | `fun_b.mp4` | giggling energy, slight shoulder shake, sparkly eyes | 楽しいB |
| 8 | sad.png | `sad_a.mp4` | downcast eyes, soft sigh motion, slight head droop | 悲しいA |
| 9 | sad.png | `sad_b.mp4` | troubled gaze, gentle head shake, lip quiver | 悲しいB |
| 10 | angry.png | `angry_a.mp4` | pouty cheek puff, narrowed eyes, slight head turn-away | 怒りA |
| 11 | angry.png | `angry_b.mp4` | irritated huff, raised eyebrow, slight foot tap | 怒りB |

### 透過運用方針: OBS クロマキー（緑背景必須）

MP4 (H.264 yuv420p) は**アルファチャンネル非対応**のため、動画素材で背景透過を実現するには：

- ❌ 白背景 → OBSクロマキーで白を抜くと**衣装の白（ブラウス・ベレー帽・裾ライン）も一緒に抜ける**
- ❌ アルファ付きMP4 → そもそも H.264 では不可
- ✅ **緑背景 (#00FF00) → OBS クロマキー (Color Key: Green) で抜く**
- ✅ 別解: Dreamina/Seedance で WebM (VP9) 透過出力できるか実機検証（推奨度低）

**運用確定**: 全動画素材は **PURE GREEN #00FF00 背景**で生成、OBS側でクロマキー設定する。

### 共通 i2v プロンプト（全本に必須）

```
SEAMLESS LOOP — first frame must match last frame for perfect cyclic playback.
Duration: 10 seconds.
Anime-style 2D animation, smooth cell-shaded movement, 24fps.
Camera: completely static, no zoom, no pan, no perspective change.

Background: SOLID PURE GREEN (#00FF00) for chroma key compositing — flat green screen, no texture, no gradient, no patterns. The background must be completely uniform green.

Body posture: identical to source image — only natural micro-motions allowed (breathing, hair sway, blink, subtle expression nuance).
Do not change outfit, hair, or pose composition.
```

### Seedance 生成ワークフロー（お兄ちゃん向け）

**選択肢 A: Dreamina (CapCut) — 無料枠**
1. https://dreamina.capcut.com/ にアクセス（CapCutアカウント必要）
2. 「Image to Video」モード選択
3. 元画像（例: normal.png）アップロード
4. プロンプト = 「共通 i2v プロンプト」＋「該当行のプロンプト追記」を貼る
5. Duration: 10s、解像度: 1080p（縦長）
6. 生成 → DL → `data/mind/kurara/assets/videos/<ファイル名>.mp4` に配置
7. 1日225トークン制限 → 1本5〜30トークン消費なので、無理なく日割り

**選択肢 B: VolcEngine API — 有料**
1. https://www.volcengine.com/ でアカウント開設
2. Doubao Vision/動画生成 API 申請
3. APIキー取得 → `scripts/generate_kurara_loops.py` をPython で実装（次セッション課題）
4. 一括バッチ生成（11本×$1.4 ≈ $15.4）

### ループ品質チェック観点

- 動画末尾で**ガクッと跳ねないか**（first=last 確認）
- **背景が PURE GREEN (#00FF00) で完全一様か**（グラデ・ノイズ・透けが無い）
- **OBSクロマキー後にエッジが綺麗か**（緑のフリンジ残り＝Spill が無いか）
- **ポーズが大幅に変わっていないか**（同じ立ち位置・体の向き）
- **服装の細部（KRロゴ・金ボタン・ボウタイ）が崩れていないか**

### OBS Studio Mac native セットアップ

**1. インストール**
```bash
brew install --cask obs
```
- OBS 28.0以降は OBS WebSocket v5 がビルトイン（追加プラグイン不要）

**2. OBS WebSocket 有効化**
- OBS起動後 → メニュー「Tools → WebSocket Server Settings」
- 「Enable WebSocket server」ON
- Port: **4455**（obs_adapter.py のデフォルト）
- Password: 任意設定（環境変数 `OBS_PASSWORD` に同値設定）

**3. シーン構成（手動セットアップ）**

obs_adapter.py が想定する **7ソース** をシーンに追加：

| ソース名 | 種類 | ファイル | ループ | 用途 |
|---|---|---|---|---|
| `BGM` | メディアソース | （任意のmp3） | ✅ | 配信BGM（無くても可） |
| `voice` | メディアソース | （空または `~/.cache/ai-tuber/voice/dummy.wav`） | ❌ | Irodori-TTS出力を動的更新 |
| `silent` | メディアソース | `assets/videos/normal_idle.mp4` | ✅ | 待機ループ（最重要・常時表示候補） |
| `normal` | メディアソース | `assets/videos/normal_speaking_a.mp4` | ✅ | 通常発話 |
| `joyful` | メディアソース | `assets/videos/joyful_a.mp4` | ✅ | 喜び発話 |
| `fun` | メディアソース | `assets/videos/fun_a.mp4` | ✅ | 楽しい発話 |
| `sad` | メディアソース | `assets/videos/sad_a.mp4` | ✅ | 悲しみ発話 |
| `angry` | メディアソース | `assets/videos/angry_a.mp4` | ✅ | 怒り発話 |

**ソース名は厳密一致必須**（obs_adapter.py の `EMOTION_MAP` が参照）。

**4. Color Key フィルタ（表情6ソース全てに適用）**

各動画ソース（silent / normal / joyful / fun / sad / angry）を右クリック → **「フィルタ」** → 「**+** → エフェクトフィルタ → カラーキー」：

| 設定 | 値 |
|---|---|
| キーカラータイプ | カスタム |
| キーカラー | `#00FF00` |
| 類似性 | 400 |
| 滑らかさ | 80 |
| キー色のスピル削減 | 50 |

**5. 各ソース共通設定**
- ファイルパス: 絶対パス推奨（`/Users/yoshida/src/github.com/osushi-cr/ai-tuber/data/mind/kurara/assets/videos/...`）
- ループ: 表情6ソース全てON、voice はOFF
- 「無効時にファイルを閉じる」: OFF（高速切替のため）
- 「アクティブ時にリスタート」: voice のみ ON

**6. 初期表示状態**
- silent: ✅表示
- normal/joyful/fun/sad/angry: ❌非表示
- voice: ✅表示（音声ミュートで音だけ管理）
- 配信開始時: silent + voice が表示、表情ソースは音声再生時に切り替え

### scene config 自動生成（次セッション課題）

ren のような JSON scene file を kurara 用に作って配布できるようにすると、GUI セットアップを skip できる。
Phase 2 で `data/mind/kurara/scene-config/Kurara.json` を自動生成スクリプトと共に提供する。

## 配信背景（くららの部屋）

配信中の背景として、表情ソースの最背面に重ねる固定画像。

### 仕様

| 項目 | 値 |
|---|---|
| ファイル名 | `assets/backgrounds/room_main.png` |
| 解像度 | 1920×1080 (16:9 OBSキャンバス標準) |
| 透過 | 不要（部屋の絵そのものが背景） |
| カラー | Crail (#CC785C) 基調・cream wall・light wood furniture |
| 構図 | カメラ目線・中央前景はキャラ立ち位置のため空け |

### ChatGPT image-2 プロンプト

```
Anime-style 2D illustration, soft cell-shading, warm cozy atmosphere.

Scene: A 14-year-old anime girl's bedroom designed for AITuber streaming.
Camera: facing the room from where the streamer would be standing — a clean view of the wall and furniture, suitable as a green-screen-friendly background overlay (the character will be composited in the foreground later).

Color palette (Anthropic Claude brand):
- Warm coral / Crail (#CC785C) as primary accent
- Cream (#F5F4EE) walls
- Light wood furniture (#C9A87A)
- Soft pink and gold highlights

Room contents:
- A wooden study desk on the right with a sleek laptop, headphones, and a small plush mascot
- A bookshelf on the left filled with novels, manga, and a few framed photos
- A bed in the back with coral-colored bedding and white pillows
- A round window with sheer white curtains letting in soft afternoon light
- Some potted plants and string fairy lights for ambiance
- A few cute pastel posters/paintings on the wall (no recognizable copyrighted IP)
- Subtle "KR" themed décor accent (a pennant or pillow with KR initial)

Composition: Wide angle, slightly looking down. Empty space in the center foreground where the character will stand (do not place any furniture there).
Background: NOT transparent — keep the room scene as the actual image content.
Resolution: 1920x1080 horizontal (16:9 widescreen for OBS canvas).
No text, no people, no logos.
```

### OBS追加手順

1. **画像ソース追加**: 「ソース」パネル「+」→「画像」
2. **ソース名**: `background_room`
3. **画像ファイル**: `~/src/github.com/osushi-cr/ai-tuber/data/mind/kurara/assets/backgrounds/room_main.png`
4. **配置**: ソース一覧で**最背面**にドラッグ（一番下）
5. **キャンバスにフィット**: 右クリック →「変換」→「画面に合わせる」

→ プレビューで「くららの部屋＋立ってるくらら」が合成表示されればOK✨

## 次フェーズ（このスコープ外）

- **reference.wav 5感情**（肉声録音）
- **フィラーwav追加生成**（Irodori-TTSで・必要に応じて）
- **YouTube OAuth → コメント取得→OBS Browser Source 合成**
- **Saint Graph 本体起動**（GEMINI_API_KEY+ADK）→ 配信ループ実機テスト

## 参考リンク

- Anthropic ブランドガイド色 Crail: https://www.anthropic.com/
- handoff メモ: `~/src/work/ai-management/03_projects/kurara-aituber/handoff-2026-04-26.md`
