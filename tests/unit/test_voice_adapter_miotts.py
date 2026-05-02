"""voice_adapter_miotts.py の TTS パラメータ・暴走リトライのユニットテスト。"""
import io
import os
import wave

import pytest

os.environ.setdefault("TTS_ENGINE", "miotts")

from body.streamer import voice_adapter_miotts as adapter


def _make_wav(seconds: float, rate: int = 44100) -> bytes:
    """指定秒数の無音 wav バイナリを返す（duration 検査用ダミー）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def test_style_params_high_arousal_temperatures_are_capped_at_0_8():
    """高揚系 style (fun / joyful / angry) は temperature 0.8 上限。
    1.0 だと MioTTS が暴走しがちなため (2026-05-02 検証で実害観測)。"""
    assert adapter._STYLE_PARAMS["fun"]["temperature"] == 0.8
    assert adapter._STYLE_PARAMS["joyful"]["temperature"] == 0.8
    assert adapter._STYLE_PARAMS["angry"]["temperature"] == 0.8


def test_style_params_low_arousal_temperatures_unchanged():
    """落ち着き系 (neutral / sad) は据え置き。"""
    assert adapter._STYLE_PARAMS["neutral"]["temperature"] == 0.5
    assert adapter._STYLE_PARAMS["sad"]["temperature"] == 0.3


def test_post_tts_with_retry_passes_through_when_duration_normal(monkeypatch):
    """通常 duration（0.20 秒/字 程度）なら _post_tts は 1 回しか呼ばれない。"""
    calls = []

    def fake_post_tts(text, params):
        calls.append(text)
        # 28 字なら 6 秒（0.21 秒/字）= 正常範囲
        return _make_wav(seconds=6.0)

    monkeypatch.setattr(adapter, "_post_tts", fake_post_tts)

    result = adapter._post_tts_with_retry("わあ、コメントありがとう！かわいいって言われて嬉しいなあ", {})

    assert len(calls) == 1
    assert len(result) > 0


def test_post_tts_with_retry_retries_once_when_runaway_then_recovers(monkeypatch):
    """暴走 (28 秒 / 28 字 = 1.0 秒/字) → 1 回リトライで正常に戻る。"""
    durations = [28.0, 6.0]  # 1回目暴走、 2回目正常

    def fake_post_tts(text, params):
        return _make_wav(seconds=durations.pop(0))

    monkeypatch.setattr(adapter, "_post_tts", fake_post_tts)

    text = "わあ、コメントありがとう！かわいいって言われて嬉しいなあ"  # 28 字程度
    result = adapter._post_tts_with_retry(text, {})

    # 2 回目（6 秒）の wav が返る
    returned_dur = adapter._wav_duration_from_bytes(result)
    assert returned_dur == pytest.approx(6.0, abs=0.01)
    # durations が両方消費されている = 2 回呼ばれた
    assert durations == []


def test_post_tts_with_retry_catches_borderline_runaway_at_0_4_per_char(monkeypatch):
    """68 字 / 28 秒 = 0.41 秒/字 のような境界暴走（YouTube Live #2 で実害観測）も
    閾値 0.35 秒/字でちゃんと拾うこと。"""
    durations = [28.0, 14.0]  # 1回目: 0.41 秒/字、 2回目: 0.20 秒/字

    def fake_post_tts(text, params):
        return _make_wav(seconds=durations.pop(0))

    monkeypatch.setattr(adapter, "_post_tts", fake_post_tts)

    text = "あ" * 68  # 68 字
    result = adapter._post_tts_with_retry(text, {})

    returned_dur = adapter._wav_duration_from_bytes(result)
    assert returned_dur == pytest.approx(14.0, abs=0.01)
    assert durations == []


def test_post_tts_with_retry_gives_up_after_one_retry_even_if_still_abnormal(monkeypatch):
    """リトライしても暴走したまま → 警告ログ出して諦めて返す（無限リトライ防止）。"""
    durations = [28.0, 30.0]  # 両方暴走

    def fake_post_tts(text, params):
        return _make_wav(seconds=durations.pop(0))

    monkeypatch.setattr(adapter, "_post_tts", fake_post_tts)

    text = "短文でも暴走することがある"  # 13 字
    result = adapter._post_tts_with_retry(text, {})

    # 2 回目の wav (30 秒) を諦めて返す
    returned_dur = adapter._wav_duration_from_bytes(result)
    assert returned_dur == pytest.approx(30.0, abs=0.01)
    assert durations == []
