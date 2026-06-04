# QA返答の体感高速化：saint_graph 文分割の復活

> Linear: YOS-51 / 対象: kurara-aituber の TTS 発話パイプライン
> ステータス: レビュー3周目（実装未着手）

## 1. 目的

配信中の**コメント返答（QAフェーズ）の体感応答（time-to-first-audio = 喋り出すまでの時間）を短縮**する。
旧実装では返答の全文を合成し終えるまで1音も鳴らなかった。これを「1文目が出来たら喋り始める」逐次再生に変え、**TtFA（喋り出しまで）を短縮**する。

> ⚠️ 注意（実測で確定）: 短縮されるのは **TtFA（喋り出しまで）** であり、**返答を全部喋り終わるまでの時間ではない**。後者は「返答文を実際に音声として喋る再生時間」で決まり、逐次化では縮まない（5文返答なら20秒前後）。当初「33秒→2秒」と見積もったが、実測ではTTS合成自体は3秒、33秒の大半は5文を喋る再生時間だった。本変更の効果は「全文合成を待たずに1文目から喋り出す」点に限られる。

## 2. 根本原因（コード＋実ログで確定）

### 2.1 実ログ（2026-06-04 朝の配信 `/tmp/saint_graph_pub_20260604_084835.log`）

```
08:55:38.354  コメント取得
08:56:11.179  Turn completed. 1 sentences spoken   → 約33秒（喋り終わるまで）
```
- 全 turn が `1 sentences spoken` ＝ 返答全体が1発話として扱われ、**全文合成完了まで1音も鳴らない**（旧実装の問題）
- Gemini 生成は ≈1秒（ボトルネックでない）
- ⚠️ この33秒は「喋り終わるまで」。実測では TTS 合成自体は約3秒で、残りは5文を実際に喋る再生時間。本変更が縮めるのは「喋り**出す**まで（TtFA）」であって、この33秒全体ではない

### 2.2 真因：`_split_sentences` が分割していない

`saint_graph.py:573`：

```python
def _split_sentences(self, text: str, force_flush: bool = False) -> list[str]:
    """テキストを区切りません。
    一括でVoiceVoxに渡すことで、OBSでの2.5秒のリップシンクラグによる
    「文ごとの不自然な間」を解消します。"""
    return [text]   # ← 丸ごと1個で返す
```

これは **fork 前から残る実装で、経緯は不明**。docstring が示す通り「合成が即時な前提」での最適化と推測される — 合成が速ければ、文ごとに分割すると OBS のリップシンクラグ（2.5s）が文数ぶん積み上がって不自然になるため、「分割をやめて一括合成」にしていたと読める。なお当時のエンジンが VoiceVox だったかは未確認（推測）。

**今は Irodori-TTS（合成が重い・1文≈2s）に移行済み**で、状況が逆転している：

| | 旧エンジン（合成即時） | Irodori 時代（現在） |
|---|---|---|
| 合成速度 | 即時 | 遅い（1文≈2s・全文合成≈3s） |
| 一括の損 | なし | **全文合成し終わるまで喋り出せない（TtFA悪化）** |
| 正解 | 一括（分割しない） | **分割して逐次再生（1文目から喋り出す）** |

→ `return [text]` は現行構成では逆効果。**これを実際の文分割に戻すことが根治**。

### 2.3 分割を戻すと既存の配管が初めて動く

`_collect_buffered_sentences`（saint_graph.py:484-521）は **`_split_sentences` が複数文を返す前提**で書かれている：

```
Gemini ストリーム
  └─ _collect_buffered_sentences: 文が完成した端から確定（最後の1文は未完成扱いで buffer 保持）
       ※ 現状 _split_sentences が [text] を返すため len<=1 で常に break ＝ 1文も確定しない
  └─ _play_sentences: 文ごとに _speak_sentence → body.queue_speak(文)   ← 文単位投入
       └─ body service._enqueue_action: enqueue 時に即 background 合成（service.py:202、既存prefetch）
            └─ 文1 再生中に 文2/文3 が裏で合成済みになる
```

→ **body 側は一切変更不要**。`_split_sentences` を直すだけで、既存の文単位 prefetch（`service.py:202`）が本来の働きをし、逐次再生が成立する。

## 3. 変更点（1関数のみ）

### `saint_graph.py:573` `_split_sentences` を句点分割に置換

#### 協調契約（`_collect_buffered_sentences` saint_graph.py:501-518）

呼び出し側は戻り値を**「最後の要素＝未確定（buffer に戻す）／それ以外＝確定文」**として扱う:
- `len(sentences) <= 1` かつ `not flush` → break（確定文ゼロ、buffer 継続）
- `sentences[0..-2]` を確定（line 505）
- `sentences[-1]` を `buffered_text` に戻す（line 511）。`flush=True` 時のみ末尾も確定（line 512-516）

→ **句点完結した完成文を即確定させるには、戻り値の末尾に「未確定セグメント（空でも可）」を必ず置く**必要がある。置かないと、句点完結文が `len==1` で break され、次の句点まで再生されない（TtFA が1文ぶん遅延）。

#### 実装

```python
def _split_sentences(self, text: str, force_flush: bool = False) -> list[str]:
    """句点（。！？改行）で分割する。

    _collect_buffered_sentences と協調する。戻り値の末尾要素は常に
    「未確定セグメント」（句点で終わっていない余り。無ければ空文字）であり、
    呼び出し側がこれを buffer に戻して次のストリームチャンクを待つ。
    """
    parts = re.split(r"([。！？\n])", text)
    sentences: list[str] = []
    buf = ""
    for part in parts:
        buf += part
        if part in "。！？\n":
            if buf.strip():
                sentences.append(buf)
            buf = ""
    # 末尾の未確定分を必ず最後の要素として置く（空でも置く＝契約）
    sentences.append(buf)
    return sentences
```

ケース別の動作（協調契約に照らした検証）:

| 入力（ストリーム途中, flush=False） | 戻り値 | 呼び出し側の挙動 |
|---|---|---|
| `"こんにちは。元気？"` | `["こんにちは。", "元気？"]` | `こんにちは。`確定・`元気？`をbuffer継続 ✅ |
| `"こんにちは。"` | `["こんにちは。", ""]` | `こんにちは。`**即確定**・`""`をbuffer継続 ✅（旧実装はここで遅延した） |
| `"あああ"`（句点なし） | `["あああ"]` | `len==1`→break・buffer継続 ✅ |
| `"文。"`（flush=True） | `["文。", ""]` | `文。`確定・末尾`""`は `if sentence:` で弾かれ無害 ✅ |

> 注: body 側 `voice_adapter_irodori._split_sentences`（80字上限・読点再分割）は **Irodori の latent step 上限対策**として別途必要なので残す。saint_graph 側は「句点で文に割る」だけで良い（長すぎる文の安全網は body 側が担う）。

## 4. 確定した論点

| # | 論点 | 確定 |
|---|---|---|
| 1 | 適用範囲 | QA返答含む全 process_turn 経路。news/intro も文分割されるが、これらは waiting 先行合成（`prepared_wav_path`）で再生されるため体感影響なし。むしろ自然な文区切りになる |
| 2 | 継ぎ目の無音 | **2-b：無音許容**。フィラーは挟まない。enqueue prefetch（service.py:202）が文N+1を先行合成するため、再生時間>合成時間なら無音は出ない |
| 3 | caption 粒度 | **対応不要**。`_play_sentences` が `sentences_spoken==0` のときだけ caption を付ける実装済み（saint_graph.py:446-453）。分割復活で自動的に「頭出し1枚」が成立 |
| 4 | body 側の変更 | **不要**。既存 prefetch 機構に乗る |
| 5 | OBS リップシンクラグ | **実機検証が必要**（§6）。当時の懸念だが、現在は `_playback_event` で実再生開始を待つ同期（obs_adapter.py:614）になっており、当時より改善している可能性が高い |

## 5. スコープ / 非スコープ

- スコープ: `saint_graph.py:_split_sentences`（句点分割に置換）**のみ**
- 非スコープ: body 側 voice_adapter / service の変更、MioTTS、MisoTTS評価、OBS adapter

## 6. 検証計画

- **ユニットテスト（実装と同時・TDD）**: `_split_sentences` は純粋関数。§3 のケース表をそのままテスト化する:
  - `"こんにちは。元気？"` → `["こんにちは。", "元気？"]`（末尾未確定を分離）
  - `"こんにちは。"` → `["こんにちは。", ""]`（句点完結でも末尾に空を置く）
  - `"あああ"` → `["あああ"]`（句点なしは未確定1要素）
  - `flush=True` 経路で末尾が確定し空文字が混入しないこと
  - さらに `_collect_buffered_sentences` との結合: ストリームを分割投入して、句点完結文が次チャンクを待たず確定することを確認（Critical 1 の回帰防止）
- **ベンチ**: QA返答で「コメント取得 → 最初の文の再生開始（TtFA）」をログ計測。`Turn completed. N sentences spoken` の N が 1 → 複数になることを確認
  - ✅ **実機検証済み（2026-06-04 完全実機 STREAMING_MODE=false）**: コメント注入→1文目再生 ≈3秒（旧: 全文合成完了まで無音）。`5 sentences spoken`・各文個別synthを確認。喋り出しが劇的に改善。なお返答を喋り終わるまでは約23秒（5文の再生時間）で、これは逐次化では縮まない
- **OBS リップシンクラグ実機確認（最重要）**: 文間の口パク同期・不自然な間が出ないか実機（OBS）で目視。`return [text]` のコメントが警告していた「2.5秒ラグ」が現行構成で再燃しないかを確認。**再燃する場合のみ** LIP_SYNC_ADJUST_MS 調整やバッファ深さ拡張を検討
- **既存退行**: news/intro/closing が壊れていないこと（これらも文分割されるが prepared 経路なので再生挙動は変わらないはず）

## 7. リスクと撤退

- 唯一のリスクは §6 の OBS ラグ再燃。1関数の変更なので、問題が出たら即 revert 可能（`return [text]` に戻すだけ）。撤退コストはほぼゼロ
