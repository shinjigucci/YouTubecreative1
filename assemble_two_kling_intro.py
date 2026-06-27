from __future__ import annotations

import argparse
import subprocess
import urllib.request
from pathlib import Path

import imageio_ffmpeg


ROOT = Path(__file__).resolve().parent
W, H = 1280, 720
JAPANESE_FONT_URL = "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"
FONT_CACHE = ROOT / "fonts" / "NotoSansCJKjp-Regular.otf"


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail[-4000:] or f"Command failed: {' '.join(cmd)}")


def media_duration(path: Path) -> float:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run([ffmpeg, "-i", str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text = proc.stderr or ""
    marker = "Duration:"
    if marker not in text:
        return 60.0
    raw = text.split(marker, 1)[1].split(",", 1)[0].strip()
    hh, mm, ss = raw.split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def split_captions(text: str, max_chars: int = 30) -> list[str]:
    cleaned = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    captions: list[str] = []
    current = ""
    for char in cleaned:
        current += char
        if len(current) >= max_chars and char in "。！？、,.!?":
            captions.append(current.strip())
            current = ""
    if current.strip():
        captions.append(current.strip())
    return captions or [cleaned]


def caption_lines(caption: str, max_line_chars: int = 22) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in caption:
        current += char
        if len(current) >= max_line_chars:
            lines.append(current.strip())
            current = ""
        if len(lines) >= 2:
            break
    if current.strip() and len(lines) < 2:
        lines.append(current.strip())
    return lines or [caption[:max_line_chars]]


def font_file() -> Path:
    candidates = [
        FONT_CACHE,
        ROOT / "fonts" / "NotoSansCJKjp-Regular.otf",
        ROOT / "fonts" / "NotoSansJP-VF.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        Path("C:/Windows/Fonts/NotoSansJP-VF.ttf"),
        Path("C:/Windows/Fonts/YuGothB.ttc"),
        Path("C:/Windows/Fonts/meiryob.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 100000:
            return candidate

    FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(JAPANESE_FONT_URL, FONT_CACHE)
    except Exception as exc:
        raise RuntimeError(
            "日本語フォントの取得に失敗しました。fonts/NotoSansJP-VF.ttf をGitHubへアップロードしてください。"
            f" 詳細: {exc}"
        ) from exc
    if FONT_CACHE.exists() and FONT_CACHE.stat().st_size > 100000:
        return FONT_CACHE
    raise RuntimeError("日本語フォントが見つからず、自動取得にも失敗しました。")


def ffmpeg_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def build_caption_filter(script: str, out_dir: Path, total_duration: float) -> str:
    captions = split_captions(script)
    font = ffmpeg_path(font_file())
    caption_dir = out_dir / "captions"
    caption_dir.mkdir(parents=True, exist_ok=True)
    segment = max(1.8, total_duration / max(1, len(captions)))
    current = "vcat"
    filters: list[str] = []
    step = 0
    for index, caption in enumerate(captions):
        start = index * segment
        end = min(total_duration + 1.0, start + segment + 0.35)
        lines = caption_lines(caption)
        for line_index, line in enumerate(lines):
            text_path = caption_dir / f"caption_{index:03d}_{line_index}.txt"
            text_path.write_text(line, encoding="utf-8")
            y_pos = "h-150" if len(lines) == 2 and line_index == 0 else ("h-96" if len(lines) == 2 else "h-118")
            out = f"vcap{step}"
            filters.append(
                f"[{current}]drawtext=fontfile='{font}':textfile='{ffmpeg_path(text_path)}':"
                f"x=(w-text_w)/2:y={y_pos}:fontsize=32:fontcolor=white:"
                f"box=1:boxcolor=black@0.70:boxborderw=14:"
                f"enable='between(t,{start:.2f},{end:.2f})'[{out}]"
            )
            current = out
            step += 1
    filters.append(f"[{current}]format=yuv420p[v]")
    return ";".join(filters)


def output_snapshot(out_dir: Path) -> str:
    if not out_dir.exists():
        return f"{out_dir} does not exist"
    files = []
    for path in out_dir.rglob("*"):
        if path.is_file():
            files.append(f"{path} ({path.stat().st_size} bytes)")
    return "\n".join(files[-80:]) or f"{out_dir} is empty"


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
        if not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError(f"必要な素材が見つからないか空です: {path}\n現在の出力ファイル:\n{output_snapshot(out_dir)}")

    script = Path(args.script).read_text(encoding="utf-8")
    audio_duration = media_duration(audio)
    caption_filter = build_caption_filter(script, out_dir, audio_duration)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    filter_complex = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
        f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
        f"[2:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v2];"
        f"[v0][v1][v2]concat=n=3:v=1:a=0[vcat];"
        f"{caption_filter}"
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
    if not final.exists() or final.stat().st_size <= 0:
        raise RuntimeError(f"最終MP4を書き出せませんでした: {final}\n現在の出力ファイル:\n{output_snapshot(out_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
