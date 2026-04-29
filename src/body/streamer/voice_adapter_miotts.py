"""MioTTS adapter for speech synthesis (HTTP client to local MioTTS server)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import wave
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

MIOTTS_API_BASE = os.getenv("MIOTTS_API_BASE", "http://localhost:8001")
MIOTTS_PRESET_ID = os.getenv("MIOTTS_PRESET_ID", "kurara")
MIOTTS_TIMEOUT = float(os.getenv("MIOTTS_TIMEOUT", "60.0"))

# MioTTS-0.1B は英字略語入力で max_tokens まで暴走する弱点があるため、
# 入力テキストの英字を事前にカタカナ読みへ正規化する。
_ALPHABET_KATAKANA = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー",
    "F": "エフ", "G": "ジー", "H": "エイチ", "I": "アイ", "J": "ジェイ",
    "K": "ケー", "L": "エル", "M": "エム", "N": "エヌ", "O": "オー",
    "P": "ピー", "Q": "キュー", "R": "アール", "S": "エス", "T": "ティー",
    "U": "ユー", "V": "ブイ", "W": "ダブリュー", "X": "エックス", "Y": "ワイ",
    "Z": "ゼット",
}
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
]
_COMMON_ABBREVS_SORTED = sorted(_COMMON_ABBREVS, key=lambda x: -len(x[0]))


# 絵文字（ピクトグラム / シンボル / 装飾）も学習データ外で MioTTS-0.1B が暴走するため除去する。
# Unicode カテゴリ "So"（Symbol, Other）と主要絵文字レンジを対象にする。
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # Misc Symbols and Pictographs / Emoticons / Transport / Supplemental
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002600-\U000027BF"  # Misc Symbols / Dingbats（✨ ★ ☆ 等）
    "\U0001F1E6-\U0001F1FF"  # Regional Indicator
    "]+",
    flags=re.UNICODE,
)


# 1桁数字をTTS読みに変換するテーブル。"〇" は MioTTS が「まる」と読み揺れするため
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
    - 符号: 数字直前の "+" "-" → "プラス" "マイナス"
    - パーセント / アンパサンド / 全角イコール / ドル → カナ
    - 小数: "6917.81" → "六千九百十七点八一"（一桁ずつ）
    - 整数: "54293" → "五万四千二百九十三"
    """
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    text = re.sub(r"(?<![\d.])\+(?=\d)", "プラス", text)
    text = re.sub(r"(?<![\d.])\-(?=\d)", "マイナス", text)
    text = text.replace("%", "パーセント").replace("％", "パーセント")
    text = text.replace("&", "アンド").replace("＆", "アンド")
    text = text.replace("＝", "イコール")
    text = text.replace("$", "ドル")
    text = re.sub(r"(\d+)\.(\d+)", lambda m: _kanji_decimal(m.group(1), m.group(2)), text)
    # 単桁整数（"1ドル" "2月" 等）は漢数字化しない。漢数字「一」は MioTTS が「ひと」読みに揺れるため、
    # アラビア数字のままにして「いち / に」と読ませる。2桁以上の整数だけ漢数字化する。
    text = re.sub(r"\d{2,}", lambda m: _kanji_int(int(m.group(0))), text)
    return text


def _normalize_text(text: str) -> str:
    """MioTTS が暴走しないよう & TTSが正しく読めるようテキストを正規化する。

    - 絵文字除去
    - 波ダッシュ削除（「みんな〜」→「みんな」、「ー」変換だと音声崩壊）
    - 半角・全角空白除去（日本語テキストとして不自然＋暴走トリガーになる）
    - 感嘆符・疑問符（！？!?）を句点「。」に統一（混在で暴走するため）
    - 連続「。」を1つに圧縮
    - 数値・記号正規化（カンマ削除・%・±符号・&・＝・$）
    - 既知略語・固有名詞をカタカナ化
    - 残った2文字以上の連続英字をアルファベット読み
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
    # COMMON_ABBREVS にヒットしなかった連続英字は削除する。アルファベット読みに展開すると
    # "Superlative" → "エスユーピーイーアールエル..." のように一般英単語までスペル読みされて
    # 視聴者には読み間違いに聞こえるため。原稿生成側（Gemini）で英語タイトル・固有名詞を
    # カタカナ表記する規約を持たせ、漏れた英字はサイレントに落とすセーフティネット運用。
    text = re.sub(r"[A-Za-z]+", "", text)
    # 英字削除で空になった鉤括弧ペアを除去（"より「」、" のような不自然な並びがTTSで暴走/崩壊するため）
    text = re.sub(r"「」|『』|\(\)|（）|【】|《》|〈〉", "", text)
    return text


# 後方互換用エイリアス
_normalize_english = _normalize_text

VOICE_DIR = Path(os.getenv("VOICE_DIR", str(Path.home() / ".cache/ai-tuber/voice")))
VOICE_DIR.mkdir(parents=True, exist_ok=True)

# MioTTS は長文を一発で投げると max_tokens まで暴走することがあるため、文単位分割で
# サイズを揃えて安定生成する。1.7B Q4_K_M（Apache 2.0）では 100字一発まで安定（2026-04-29 ベンチ）。
# 短文（min_chars 未満）はマージ、長文（max_chars 超）は読点で再分割。
_SENTENCE_MAX_CHARS = int(os.getenv("MIOTTS_SENTENCE_MAX_CHARS", "100"))
_SENTENCE_MIN_CHARS = int(os.getenv("MIOTTS_SENTENCE_MIN_CHARS", "20"))


def _split_sentences(
    text: str,
    max_chars: int = _SENTENCE_MAX_CHARS,
    min_chars: int = _SENTENCE_MIN_CHARS,
) -> list[str]:
    """句点で分割し、短文(min_chars未満)はマージ、長文(max_chars超)は読点で再分割する。"""
    parts = re.split(r"([。！？\n])", text)
    sentences: list[str] = []
    buf = ""
    for part in parts:
        buf += part
        if part in "。！？\n":
            stripped = buf.strip()
            if stripped:
                sentences.append(stripped)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    # 短文を前後にマージ（merged の最後が min_chars 未満なら次を結合）
    merged: list[str] = []
    for s in sentences:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + len(s) <= max_chars:
            merged[-1] += s
        else:
            merged.append(s)

    # 長文を読点で再分割
    result: list[str] = []
    for s in merged:
        if len(s) <= max_chars:
            result.append(s)
            continue
        sub_parts = re.split(r"([、])", s)
        sub_buf = ""
        for sp in sub_parts:
            if len(sub_buf) + len(sp) > max_chars and sub_buf:
                result.append(sub_buf.strip())
                sub_buf = sp
            else:
                sub_buf += sp
        if sub_buf.strip():
            result.append(sub_buf.strip())

    # 最終 fail-safe: 句点も読点もない長文を max_chars でぶつ切り（暴走の根絶）
    final: list[str] = []
    for s in result:
        while len(s) > max_chars:
            final.append(s[:max_chars])
            s = s[max_chars:]
        if s.strip():
            final.append(s.strip())
    return [s for s in final if s]


def get_wav_duration(file_path: str) -> float:
    """Return WAV file duration in seconds."""
    try:
        with wave.open(file_path, "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate)
    except Exception as e:
        logger.error("WAV duration error for %s: %s", file_path, e)
        return 3.0


def _concat_wavs(wav_paths: list[Path], out_path: Path) -> None:
    """複数の wav ファイルを連結して1ファイルに保存する（同一サンプリングレート前提）。"""
    if not wav_paths:
        raise ValueError("wav_paths is empty")
    with wave.open(str(wav_paths[0]), "rb") as first:
        params = first.getparams()
        all_frames = first.readframes(first.getnframes())
    for p in wav_paths[1:]:
        with wave.open(str(p), "rb") as w:
            all_frames += w.readframes(w.getnframes())
    with wave.open(str(out_path), "wb") as out:
        out.setparams(params)
        out.writeframes(all_frames)


def _post_tts(text: str) -> bytes:
    """MioTTS API に正規化済みテキストを POST して wav バイナリを返す。"""
    payload = {
        "text": text,
        "reference": {"type": "preset", "preset_id": MIOTTS_PRESET_ID},
        "output": {"format": "wav"},
    }
    with httpx.Client(timeout=MIOTTS_TIMEOUT) as client:
        resp = client.post(f"{MIOTTS_API_BASE}/v1/tts", json=payload)
        resp.raise_for_status()
        return resp.content


def _synthesize_sync(text: str) -> tuple[str, float]:
    # 正規化→分割→送信 の順序を厳密に守る（split時点で絵文字・空白等が残ってると暴走の元）
    normalized = _normalize_text(text)
    sentences = _split_sentences(normalized)
    logger.info(
        f"[synth] text_len={len(text)}->{len(normalized)} sentences={len(sentences)} preset={MIOTTS_PRESET_ID}"
    )

    if len(sentences) <= 1:
        wav_bytes = _post_tts(normalized)
        filename = f"speech_{abs(hash(text)) % 100000}.wav"
        out_path = VOICE_DIR / filename
        out_path.write_bytes(wav_bytes)
        return str(out_path), get_wav_duration(str(out_path))

    # 多文: 各文を順次生成→ wav 結合
    parts: list[Path] = []
    for i, sent in enumerate(sentences):
        wav_bytes = _post_tts(sent)
        part_path = VOICE_DIR / f"speech_{abs(hash(text)) % 100000}_part{i}.wav"
        part_path.write_bytes(wav_bytes)
        parts.append(part_path)
        logger.info(f"[synth:part {i + 1}/{len(sentences)}] len={len(sent)} -> {part_path.name}")

    out_path = VOICE_DIR / f"speech_{abs(hash(text)) % 100000}.wav"
    _concat_wavs(parts, out_path)
    return str(out_path), get_wav_duration(str(out_path))


async def generate_and_save(
    text: str,
    style: str = "neutral",
    speaker_id: Optional[int] = None,
) -> tuple[str, float]:
    """Generate speech via MioTTS HTTP API and save to VOICE_DIR.

    Mirrors voice_adapter_irodori I/F. style/speaker_id are accepted for
    compatibility but currently unused.
    """
    logger.info("Generating speech: '%s' (style=%s)", text, style)
    return await asyncio.to_thread(_synthesize_sync, text)
