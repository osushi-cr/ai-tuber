#!/usr/bin/env python3
"""Quality check for kurara loop videos.

Verifies:
- Codec / resolution / duration / FPS
- Background greenness (corner sampling, distance from #00FF00)
- Loop quality (first/last frame similarity via RMSE)

Usage:
    uv run python scripts/check_video_quality.py path/to/video.mp4
    uv run python scripts/check_video_quality.py data/mind/kurara/assets/videos/*.mp4
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("ERROR: requires Pillow + numpy. Run inside Irodori-TTS uv env.", file=sys.stderr)
    sys.exit(1)


PURE_GREEN = np.array([0, 255, 0])


def ffprobe(path: Path) -> dict:
    """Return codec/resolution/fps/duration metadata."""
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,r_frame_rate,duration,pix_fmt",
            "-show_entries", "format=duration,size",
            "-of", "json",
            str(path),
        ],
        stderr=subprocess.DEVNULL,
    )
    return json.loads(out)


def extract_frame(video: Path, target_offset: float, out_path: Path) -> None:
    """Extract a single frame at target_offset seconds (or near end if negative)."""
    if target_offset < 0:
        cmd = ["ffmpeg", "-y", "-sseof", str(target_offset), "-i", str(video),
               "-vframes", "1", str(out_path)]
    else:
        cmd = ["ffmpeg", "-y", "-ss", str(target_offset), "-i", str(video),
               "-vframes", "1", str(out_path)]
    subprocess.run(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=True)


def corner_avg(arr: np.ndarray, n: int = 20) -> np.ndarray:
    """Average RGB of four 20x20 corner regions."""
    h, w = arr.shape[:2]
    corners = [
        arr[0:n, 0:n],
        arr[0:n, w-n:w],
        arr[h-n:h, 0:n],
        arr[h-n:h, w-n:w],
    ]
    return np.stack([c.mean(axis=(0, 1)) for c in corners])


def green_score(corners_rgb: np.ndarray) -> tuple[float, str]:
    """Distance from PURE_GREEN. Returns (avg_distance, verdict)."""
    dist = np.linalg.norm(corners_rgb - PURE_GREEN, axis=1).mean()
    if dist < 60:
        verdict = "EXCELLENT (close to #00FF00)"
    elif dist < 130:
        verdict = "OK (workable green, OBS picker required)"
    elif dist < 200:
        verdict = "WARNING (dim green, chroma may struggle)"
    else:
        verdict = "FAIL (background is not green)"
    return dist, verdict


def loop_quality(first_path: Path, last_path: Path) -> tuple[float, str]:
    """RMSE between first and last frame as loop continuity proxy."""
    a = np.array(Image.open(first_path).convert("RGB"), dtype=np.float32)
    b = np.array(Image.open(last_path).convert("RGB"), dtype=np.float32)
    if a.shape != b.shape:
        return float("inf"), "FAIL (size mismatch)"
    rmse = float(np.sqrt(((a - b) ** 2).mean()))
    if rmse < 8:
        verdict = "EXCELLENT (seamless loop)"
    elif rmse < 18:
        verdict = "OK (minor jitter at loop boundary)"
    elif rmse < 35:
        verdict = "WARNING (visible jump at loop)"
    else:
        verdict = "FAIL (large discontinuity, will pop on loop)"
    return rmse, verdict


def check_one(video: Path) -> dict:
    info = ffprobe(video)
    stream = info["streams"][0]
    fmt = info["format"]

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        first_png = tdp / "first.png"
        last_png = tdp / "last.png"
        extract_frame(video, 0.0, first_png)
        extract_frame(video, -0.1, last_png)

        first_arr = np.array(Image.open(first_png).convert("RGB"))
        corners = corner_avg(first_arr)
        green_dist, green_verdict = green_score(corners)
        rmse, loop_verdict = loop_quality(first_png, last_png)

    return {
        "file": str(video),
        "codec": stream.get("codec_name"),
        "size_px": f"{stream.get('width')}x{stream.get('height')}",
        "fps": stream.get("r_frame_rate"),
        "pix_fmt": stream.get("pix_fmt"),
        "duration_s": float(fmt.get("duration", 0)),
        "size_mb": int(fmt.get("size", 0)) / (1024 * 1024),
        "corner_rgb": [c.astype(int).tolist() for c in corners],
        "green_dist": green_dist,
        "green_verdict": green_verdict,
        "loop_rmse": rmse,
        "loop_verdict": loop_verdict,
    }


def render(report: dict) -> str:
    name = Path(report["file"]).name
    lines = [
        f"\n=== {name} ===",
        f"  codec={report['codec']}  size={report['size_px']}  fps={report['fps']}"
        f"  pix={report['pix_fmt']}  dur={report['duration_s']:.2f}s  ({report['size_mb']:.1f}MB)",
        f"  corners RGB: {report['corner_rgb']}",
        f"  green:       dist={report['green_dist']:>6.1f}   {report['green_verdict']}",
        f"  loop:        rmse={report['loop_rmse']:>6.2f}   {report['loop_verdict']}",
    ]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: check_video_quality.py <video.mp4> [video.mp4 ...]", file=sys.stderr)
        sys.exit(2)
    videos = [Path(p).resolve() for p in sys.argv[1:]]
    for v in videos:
        if not v.is_file():
            print(f"SKIP (not a file): {v}", file=sys.stderr)
            continue
        try:
            print(render(check_one(v)))
        except subprocess.CalledProcessError as e:
            print(f"\n=== {v.name} ===  FFMPEG ERROR: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
