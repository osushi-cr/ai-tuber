# くらら (Kurara) — Character Package

このディレクトリには、AITuber「くらら」のキャラクターパッケージが含まれています。
ベースは `data/mind/ren/` 構造を踏襲し、TTS バックエンドを Irodori-TTS に差し替えています。

## ディレクトリ構成

```
data/mind/kurara/
├── README.md       # このファイル
├── persona.md      # キャラクター設定（性格、口調、背景など）
├── mind.json       # TTS バックエンド設定（Irodori-TTS）
└── assets/         # OBSで使用するアセット（5表情png予定・素材生成タスクで埋める）
```

## TTS バックエンド: Irodori-TTS

`mind.json` の `voice_engine: "irodori"` により Irodori-TTS で音声合成します。

- **checkpoint**: `Aratako/Irodori-TTS-500M-v2` (HuggingFace)
- **ref_wav**: `~/src/personal/Irodori-TTS/voice_library/kurara/reference.wav`
- **device**: Mac MPS / fp32 / 16.7s/発話（モデル常駐前提）

## ren との差分

| 項目 | ren | kurara |
|---|---|---|
| TTS | VoiceVox API (HTTP) | Irodori-TTS (Python直呼び) |
| `mind.json` | `speaker_id: 58` | `voice_engine: "irodori"` + ref_wav |
| 性格 | のじゃ口調・ぐらしの婆様 | 妹キャラ・お兄ちゃん大好き |
| 表情png | 5枚（normal/joyful/fun/sad/angry） | 同（生成予定） |

## 動作確認（standalone E2E）

```bash
# Mac native で voice_adapter_irodori を直接叩く
cd ~/src/github.com/osushi-cr/ai-tuber
python -c "
import asyncio
from src.body.streamer.voice_adapter_irodori import generate_and_save
path, dur = asyncio.run(generate_and_save('お兄ちゃん、こんにちは！'))
print(path, dur)
"
afplay <path>
```

## 関連ファイル
- 元設計メモ: `03_projects/kurara-aituber/handoff-2026-04-26.md`
- TTS環境: `~/src/personal/Irodori-TTS/`
