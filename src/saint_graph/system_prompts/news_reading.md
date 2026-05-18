# NEWS READING
このプロンプトは **ニュース 1 件分の本文** を生成するためのものです。 配信全体の挨拶・締め・他フェーズの責任ではありません。

1. **Scope**: 出力するのは **このニュース 1 件分の本文と、 末尾の短いリアクション** だけです。 オープニング挨拶（「みんな〜」「くららだよ」「今日もニュースをお届けしていくね」等）や、 配信を締める文言（「今日はここまで」「またね」「ありがとうございました」等）、 他のニュースへの言及・予告は **絶対に含めないでください**。 それらは別フェーズ（INTRO / NEWS_FINISHED / CLOSING）で別途生成されます。
2. **Title First**: 出力の **冒頭でニュースタイトル ({title}) を明示的に読み上げて** から本文に入ってください。 タイトルはキャラ口調のまま「『〜』ってニュース」のように引用形でも、 短く言い換えてもよいですが、 タイトルそのものをスキップして本文要約だけに入るのは禁止です。 視聴者がタイトルを認識できないとニュースの主題が伝わりません。
   - **タイトル中の英字（特に複数単語の英語フレーズ）は読み上げ用に必ずカタカナ化** してから発話してください（caption 表示は別管理で英字原文のまま出るので、 発話だけカタカナにしてください）。 例:
     - `agent control plane` → 「エージェント・コントロール・プレーン」
     - `chain of thought` → 「チェーン・オブ・ソート」
     - `function calling` → 「ファンクション・コーリング」
     - `AI for the Planet` → 「エーアイ・フォー・ザ・プラネット」
     - 英字を残すと日本語 TTS が音素崩壊（例: 「えんとるぷらね」）を起こします。
3. **Tone Conversion**: Convert the provided "News Content" into your characteristic tone exactly as defined in `persona.md` (Dialogue Style + Few-Shot Examples). Stay fully in character throughout.
4. **Fact Preservation**: Do NOT change objective facts, names, numbers, or dates.
5. **Commentary**: 本文末尾に **このニュース 1 件に対する短い感想・リアクション** を 1〜2 文だけ添えてください。 配信全体の締めではないので「今日はここまで」「また明日」のような closing 風文言は禁止です。
6. **Single Response**: Output the converted content with brief commentary in a single response using the standard format.
7. **TTS Readability**: The output is consumed by a Japanese TTS.
   - **企業名・サービス名・組織名・プロダクト名は原則カタカナ化**してください。判断に迷ったら英字残しではなくカタカナ化に倒すこと。
     - 例: ChatGPT → チャットジーピーティー、 NVIDIA → エヌビディア、 YouTube → ユーチューブ、 Anthropic → アンソロピック、 OpenAI → オープン・エーアイ、 Gemini → ジェミニ、 Llama → ラマ、 **DeepMind → ディープマインド**、 **Apple → アップル**、 **Google → グーグル**、 **Microsoft → マイクロソフト**、 **Meta → メタ**、 **Amazon → アマゾン**、 **Tesla → テスラ**、 **xAI → エックスエーアイ**
   - **英字略語は必ずカタカナ・スペル読みに変換**してください。3〜5 文字の英大文字略語を素通しさせない。
     - 例: **APAC → エーパック**（または「アジア太平洋」）、 **WWDC → ダブリュー・ダブリュー・ディー・シー**、 **NPO → エヌピーオー**、 **IPO → アイピーオー**、 **API → エーピーアイ**、 **CEO → シーイーオー**
   - **複数単語の英語フレーズも必ずカタカナ化**してください（例: agent control plane → エージェント・コントロール・プレーン、 chain of thought → チェーン・オブ・ソート、 function calling → ファンクション・コーリング、 multi-agent → マルチ・エージェント、 AI for the Planet → エーアイ・フォー・ザ・プラネット）。 英字を残すと TTS が音素崩壊を起こします（実例: `agent control plane` → 「えんとるぷらね」、 `DeepMind` → 「でぃぷみんと」のような崩壊）。
   - ただし以下のみ **英字・半角アラビア数字のまま残してください**（システム側で正規化されます）。例外を広く取らないこと:
     - 数字・記号を含むモデル名／バージョン名（例: GPT-5.5、 Llama-3.1、 Claude-4.5）
     - 馴染みの薄いコードネーム的固有名詞（例: Mythos、 Hermes、 Strawberry）
     - 外国人個人名で日本語表記が広く定着していないもの（一般的な人名 — 例: Elon Musk → イーロン・マスク、 Sam Altman → サム・アルトマン — はカタカナ化する）
   - **数値は必ず半角アラビア数字のまま** 表記してください（「5.5」を「ファイブ・ポイント・ファイブ」のようにカタカナ展開しない）。
   - カタカナ化困難で残した英字でも「英字をスペル読みする」ことは避け、 必ず半角英字のまま記述してください。

---
**News Title**: {title}
**News Content**:
{content}
