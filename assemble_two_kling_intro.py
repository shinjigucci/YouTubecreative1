from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import imageio_ffmpeg


ROOT = Path(__file__).resolve().parent
W, H = 1280, 720


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail[-3000:] or f"Command failed: {' '.join(cmd)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--kling-hook", required=True)
    parser.add_argument("--kling-next", required=True)
    args = parser.parse_args()

    out_dir = ROOT / "output" / args.project
    hook = Path(args.kling_hook)
    next5 = Path(args.kling_next)
    body = out_dir / "body.mp4"
    audio = out_dir / "narration.mp3"
    final = out_dir / "youtube_ad_broll_kling10.mp4"
    for path in [hook, next5, body, audio]:
        if not path.exists():
            raise RuntimeError(f"必要な素材がありません: {path}")

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    filter_complex = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
        f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
        f"[2:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v2];"
        "[v0][v1][v2]concat=n=3:v=1:a=0[v]"
    )
    run([
        ffmpeg, "-y",
        "-i", str(hook),
        "-i", str(next5),
        "-i", str(body),
        "-i", str(audio),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "3:a:0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-c:a", "aac",
        "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        str(final),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
