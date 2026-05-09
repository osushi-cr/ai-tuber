"""Shared text normalizer for TTS adapters (MioTTS / Irodori).

ニュース原稿や Gemini が生成したセリフを TTS が正しく読み上げられる形に正規化する。
voice_adapter_miotts / voice_adapter_irodori の両方からこのモジュールを呼ぶこと。
"""
from __future__ import annotations

import re

# よく出る固有表現は専用読み。短い略語が長い略語の接頭辞になるケース（"AI" vs "AIO" 等）の
# 衝突を避けるため、適用時は文字数の長い順に置換する（_COMMON_ABBREVS_SORTED を使用）。
_COMMON_ABBREVS = [
    ("YouTube", "ユーチューブ"),
    ("ChatGPT", "チャットジーピーティー"),
    ("Claude", "クロード"),
    ("Gemini", "ジェミニ"),
    ("OpenAI", "オープンエーアイ"),
    ("Anthropic", "アンソロピック"),
    ("Twitter", "ツイッター"),
    ("OBS", "オービーエス"),
    ("URL", "ユーアールエル"),
    ("API", "エーピーアイ"),
    ("TTS", "ティーティーエス"),
    ("LLM", "エルエルエム"),
    ("AI", "エーアイ"),
    ("CPU", "シーピーユー"),
    ("GPU", "ジーピーユー"),
    # ニュース読み上げで頻出する固有名詞（カタカナ読み確定が必要なもの）
    ("VTuber", "ブイチューバー"),
    ("YouTuber", "ユーチューバー"),
    ("NVIDIA", "エヌビディア"),
    ("DAM", "ダム"),
    ("ETF", "イーティーエフ"),
    ("SEO", "エスイーオー"),
    ("GEO", "ジーイーオー"),
    ("AIO", "エーアイオー"),
    ("S&P500", "エスアンドピーゴヒャク"),
    # IT/AI ニュースで頻出するブランド・製品名（漏れると strip されてセリフが意味不明になる）
    ("NotebookLM", "ノートブックエルエム"),
    ("Workspace", "ワークスペース"),
    ("Google", "グーグル"),
    ("Microsoft", "マイクロソフト"),
    ("Apple", "アップル"),
    ("Meta", "メタ"),
    ("Amazon", "アマゾン"),
    ("Cloud", "クラウド"),
    ("Photos", "フォトス"),
    ("Drive", "ドライブ"),
    ("Docs", "ドックス"),
    ("Sheets", "シーツ"),
    ("Slides", "スライズ"),
    ("Gmail", "ジーメール"),
    ("Slack", "スラック"),
    ("Discord", "ディスコード"),
    ("Linux", "リナックス"),
    ("Windows", "ウィンドウズ"),
    ("Android", "アンドロイド"),
    ("iPhone", "アイフォン"),
    ("iPad", "アイパッド"),
    ("Mac", "マック"),
    ("TV", "ティーブイ"),
    ("Word", "ワード"),
    ("Excel", "エクセル"),
    ("PowerPoint", "パワーポイント"),
    ("PDF", "ピーディーエフ"),
    ("HTML", "エイチティーエムエル"),
    ("CSS", "シーエスエス"),
    ("JS", "ジェイエス"),
    ("SDK", "エスディーケー"),
    ("Q1", "第一四半期"),
    ("Q2", "第二四半期"),
    ("Q3", "第三四半期"),
    ("Q4", "第四四半期"),
    ("UK", "イギリス"),
    ("US", "アメリカ"),
    ("EU", "イーユー"),
    ("CEO", "シーイーオー"),
    ("CTO", "シーティーオー"),
    ("CFO", "シーエフオー"),
    ("KPI", "ケーピーアイ"),
    ("OKR", "オーケーアール"),
    ("SaaS", "サース"),
    ("PaaS", "パース"),
    ("IaaS", "イアース"),
    # 2026-05 配信検証で観察したブランド・固有名詞（Gemini が誤カタカナ化していた語の正読み）
    ("Mythos", "ミュトス"),
    ("Yubico", "ユビコ"),
    ("Stripe", "ストライプ"),
    ("Grok", "グロック"),
    ("xAI", "エックスエーアイ"),
    # 2026-05-08 配信で読み間違い観察（GPT-5.5 が「マイナス5.5」化、AMD が削除消滅、Cyber が削除）
    ("GPT", "ジーピーティー"),
    ("AMD", "エーエムディー"),
    ("Cyber", "サイバー"),
    # 2026-05-08 ZAYA1-8B 紹介ニュースで文意崩壊した語の追加対応（モデル名・社名・GPU 製品名）
    ("ZAYA1-8B", "ザヤワンエイトビー"),
    ("ZAYA1", "ザヤワン"),
    ("Zyphra", "ザイフラ"),
    ("Instinct", "インスティンクト"),
    ("MI300X", "エムアイサンビャクエックス"),
    # Gemini が間違えて出してきたカタカナ表記の保険補正（左辺が英字でなくても string.replace でマッチする）
    ("マイソス", "ミュトス"),
    # 2026-05-10 配信 #2 で英字セーフティネット削除により単語抜けが発生した語（AI 業界頻出）
    ("ElonMusk", "イーロンマスク"),
    ("Sam Altman", "サムアルトマン"),
    ("Mira Murati", "ミラムラティ"),
    ("Dario Amodei", "ダリオアモデイ"),
    ("SpaceX", "スペースエックス"),
    ("Tesla", "テスラ"),
    ("Musk", "マスク"),
    ("Altman", "アルトマン"),
    ("Copilot", "コパイロット"),
    ("Mistral", "ミストラル"),
    ("Llama", "ラマ"),
    ("Cambridge", "ケンブリッジ"),
    ("Miko", "ミコ"),
    ("RLHF", "アールエルエイチエフ"),
    ("ARR", "エーアールアール"),
    ("MRR", "エムアールアール"),
    ("AGI", "エージーアイ"),
    ("MCP", "エムシーピー"),
    ("RAG", "ラグ"),
    ("ROI", "アールオーアイ"),
    ("QA", "キューエー"),
]
_COMMON_ABBREVS_SORTED = sorted(_COMMON_ABBREVS, key=lambda x: -len(x[0]))


# 絵文字（ピクトグラム / シンボル / 装飾）も学習データ外で TTS が暴走するため除去する。
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # Misc Symbols and Pictographs / Emoticons / Transport / Supplemental
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002600-\U000027BF"  # Misc Symbols / Dingbats（✨ ★ ☆ 等）
    "\U0001F1E6-\U0001F1FF"  # Regional Indicator
    "]+",
    flags=re.UNICODE,
)


# 1桁数字をTTS読みに変換するテーブル。"〇" は TTS が「まる」と読み揺れするため
# 「ゼロ」カタカナで固定読みにする。整数部の途中ゼロは _kanji_int 側で skip されるため、
# このテーブルは小数部や 0 単独のときだけ使われる。
_KANJI_DIGITS = ["ゼロ", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
_KANJI_SMALL_UNITS = ["", "十", "百", "千"]
_KANJI_BIG_UNITS = ["", "万", "億", "兆", "京"]


def _kanji_int(n: int) -> str:
    """正の整数を日本語漢数字表記へ変換する（4桁ごとに万・億…付与、"1"は十百千の前で省略）。"""
    if n == 0:
        return "ゼロ"
    s = str(n)
    groups: list[str] = []
    while s:
        groups.append(s[-4:])
        s = s[:-4]
    parts: list[str] = []
    for i, g in enumerate(groups):
        if int(g) == 0:
            continue
        chunk = ""
        for j, ch in enumerate(g):
            d = int(ch)
            pos = len(g) - 1 - j
            if d == 0:
                continue
            if d == 1 and pos > 0:
                chunk += _KANJI_SMALL_UNITS[pos]
            else:
                chunk += _KANJI_DIGITS[d] + _KANJI_SMALL_UNITS[pos]
        parts.append(chunk + _KANJI_BIG_UNITS[i])
    return "".join(reversed(parts))


def _kanji_decimal(int_part: str, dec_part: str) -> str:
    """小数を「整数部の漢数字 + 点 + 小数部の一桁ずつ漢数字」に変換する。"""
    int_kanji = _kanji_int(int(int_part))
    dec_kanji = "".join(_KANJI_DIGITS[int(c)] for c in dec_part)
    return f"{int_kanji}点{dec_kanji}"


def _normalize_numbers_and_symbols(text: str) -> str:
    """ニュース原稿で頻出する数値・記号をTTSが読みやすい表記に正規化する。

    - 桁区切りカンマ削除: "11,797,933" → "11797933"（後段で漢数字化）
    - 符号: 文頭または区切り文字直後の "-数字" → "マイナス"（"GPT-5.5" の "-" は連結記号扱いで削除）
    - 残るハイフン（モデル名・複合語の連結記号）は削除
    - パーセント / アンパサンド / 全角イコール / ドル → カナ
    - 小数: "6917.81" → "六千九百十七点八一"（一桁ずつ）
    - 整数: "54293" → "五万四千二百九十三"
    """
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    text = re.sub(r"(?<![\d.])\+(?=\d)", "プラス", text)
    text = re.sub(r"^-(?=\d)", "マイナス", text)
    text = re.sub(r"(?<=[、。「（『\(])-(?=\d)", "マイナス", text)
    text = text.replace("-", "")
    text = text.replace("%", "パーセント").replace("％", "パーセント")
    text = text.replace("&", "アンド").replace("＆", "アンド")
    text = text.replace("＝", "イコール")
    text = text.replace("$", "ドル")
    text = re.sub(r"(\d+)\.(\d+)", lambda m: _kanji_decimal(m.group(1), m.group(2)), text)
    # 単桁整数（"1ドル" "2月" 等）は漢数字化しない。漢数字「一」は TTS が「ひと」読みに揺れるため、
    # アラビア数字のままにして「いち / に」と読ませる。2桁以上の整数だけ漢数字化する。
    text = re.sub(r"\d{2,}", lambda m: _kanji_int(int(m.group(0))), text)
    return text


def normalize_text(text: str) -> str:
    """TTS が暴走しないよう & 正しく読めるよう日本語テキストを正規化する。

    - 絵文字除去
    - 波ダッシュ削除（「みんな〜」→「みんな」、「ー」変換だと音声崩壊）
    - 半角・全角空白除去（日本語テキストとして不自然＋暴走トリガーになる）
    - 感嘆符・疑問符（！？!?）を句点「。」に統一（混在で暴走するため）
    - 連続「。」を1つに圧縮
    - 既知略語・固有名詞をカタカナ化
    - 数値・記号正規化（カンマ削除・%・±符号・&・＝・$・ハイフン）
    - 残った2文字以上の連続英字を削除（暴走防止セーフティネット）
    """
    text = _EMOJI_PATTERN.sub("", text)
    text = text.replace("〜", "").replace("～", "")
    text = text.replace(" ", "").replace("　", "")
    text = re.sub(r"[！？!?]", "。", text)
    text = re.sub(r"。+", "。", text)
    # COMMON_ABBREVS は数値・記号正規化より先に適用する（"S&P500" のような記号入り略語が
    # "&" → "アンド" 置換で拾えなくなるのを防ぐ）。長い順で置換することで "AI" / "AIO" の
    # ような接頭辞衝突（"AIO" が "AI"+"O" と先取りされるケース）も回避する。
    for abbrev, kana in _COMMON_ABBREVS_SORTED:
        text = text.replace(abbrev, kana)
    text = _normalize_numbers_and_symbols(text)
    # COMMON_ABBREVS にヒットしなかった連続英字はそのまま flow matching に渡す。
    # 削除すると単語抜けで文意が失われるが、そのまま渡せば（崩壊しても）位置と存在は伝わり、
    # 辞書漏れの検知（耳判定での違和感）も容易になる。
    return text
