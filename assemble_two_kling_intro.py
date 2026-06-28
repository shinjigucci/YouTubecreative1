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
    try:
        value = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        value = str(path).replace("\\", "/").replace(":", "\\:")
    return value.replace("'", "\\'")


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "").replace("}", "")


def write_ass_subtitles(script: str, out_dir: Path, total_duration: float) -> Path:
    captions = split_captions(script)
    caption_dir = out_dir / "captions"
    caption_dir.mkdir(parents=True, exist_ok=True)
    ass_path = caption_dir / "captions.ass"
    segment = max(1.8, total_duration / max(1, len(captions)))
    dialogues: list[str] = []
    for index, caption in enumerate(captions):
        start = index * segment
        end = min(total_duration + 1.0, start + segment + 0.35)
        text = r"\N".join(ass_escape(line) for line in caption_lines(caption))
        dialogues.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")

    ass = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1280",
        "PlayResY: 720",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Noto Sans CJK JP,32,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,3,1,0,2,80,80,46,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *dialogues,
        "",
    ])
    ass_path.write_text(ass, encoding="utf-8")
    return ass_path


def build_caption_filter(script: str, out_dir: Path, total_duration: float) -> str:
    font_file()
    ass_path = write_ass_subtitles(script, out_dir, total_duration)
    fonts_dir = ROOT / "fonts"
    return (
        f"[vcat]subtitles=filename='{ffmpeg_path(ass_path)}':"
        f"fontsdir='{ffmpeg_path(fonts_dir)}',format=yuv420p[v]"
    )


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

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    filter_complex = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
        f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
        f"[2:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v2];"
        f"[v0][v1][v2]concat=n=3:v=1:a=0,format=yuv420p[v]"
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
