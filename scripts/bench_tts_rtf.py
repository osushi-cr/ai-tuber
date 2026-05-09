"""irodori_tts_server (port 8003) に対して 5 文連続合成を投げ、RTF を測る。

prewarm 済みサーバを前提とし、初回リクエストの Metal グラフコンパイルが既に
吸収されている状態の本番 RTF を計測する。
"""
from __future__ import annotations

import time
from statistics import mean

import httpx

URL = "http://127.0.0.1:8003/tts"

SENTENCES = [
    "次のニュースだよっ。",
    "AnthropicがClaudeの思考を自然言語で読み解く技術を公開したんだって。",
    "NLA、ナチュラル・ランゲージ・アクティベーションって呼ばれてて、内部の活性化を文字に翻訳するアプローチみたい。",
    "解釈可能性の研究としてはかなり大きな前進になりそう。",
    "お兄ちゃん、これ論文化に絡む話だから後で要チェックだよっ！",
]


def main() -> None:
    rows = []
    with httpx.Client(timeout=60.0) as client:
        # warmup（prewarm 済みなのでサーバ側はキャッシュ効くが念のため 1 発）
        client.post(URL, json={"text": "ウォームアップ。"})

        for i, sent in enumerate(SENTENCES, 1):
            chars = len(sent)
            t0 = time.perf_counter()
            r = client.post(URL, json={"text": sent})
            elapsed = time.perf_counter() - t0
            r.raise_for_status()
            data = r.json()
            audio_sec = data["duration"]
            rtf = elapsed / audio_sec
            rows.append((i, chars, elapsed, audio_sec, rtf))
            print(f"  s{i} ({chars:3d} chars): gen={elapsed:.2f}s audio={audio_sec:.2f}s RTF={rtf:.3f}")

    print("\n=== Summary ===")
    total_gen = sum(r[2] for r in rows)
    total_audio = sum(r[3] for r in rows)
    avg_rtf = total_gen / total_audio
    rtf_values = [r[4] for r in rows]
    print(f"total_gen   = {total_gen:.2f}s")
    print(f"total_audio = {total_audio:.2f}s")
    print(f"avg_RTF     = {avg_rtf:.3f}")
    print(f"per-sent RTF: min={min(rtf_values):.3f} mean={mean(rtf_values):.3f} max={max(rtf_values):.3f}")

    # 30 字想定での生成時間目安
    short_rows = [r for r in rows if 25 <= r[1] <= 35]
    if short_rows:
        avg_short = mean(r[2] for r in short_rows)
        print(f"\n30字級の平均生成時間: {avg_short:.2f}s （{len(short_rows)} 文）")
        print(f"目標 30字 < 2.0s : {'✅ 達成' if avg_short < 2.0 else '❌ 未達'}")
    print(f"目標 avg_RTF < 0.3 : {'✅ 達成' if avg_rtf < 0.3 else '❌ 未達'}")


if __name__ == "__main__":
    main()
