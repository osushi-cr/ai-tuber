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

## 次フェーズ（このスコープ外）

- **ループmp4**（Seedance 2.0 i2v、表情png × 動き）
- **待機mp4**（silent loop）
- **reference.wav 5感情**（肉声録音）
- **フィラーwav 10〜20本**（Irodori-TTSで生成）

## 参考リンク

- Anthropic ブランドガイド色 Crail: https://www.anthropic.com/
- handoff メモ: `~/src/work/ai-management/03_projects/kurara-aituber/handoff-2026-04-26.md`
