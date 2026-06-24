from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
W, H = 1280, 720
FPS = 30


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail[-3000:] or f"Command failed: {' '.join(cmd)}")


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ROOT / "fonts" / "NotoSansJP-VF.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/NotoSansJP-VF.ttf"),
        Path("C:/Windows/Fonts/YuGothB.ttc" if bold else "C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc"),
    ]
    for item in candidates:
        if item.exists():
            return ImageFont.truetype(str(item), size)
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if text_width(draw, trial, fnt) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def chunks(text: str, max_chars: int) -> list[str]:
    cleaned = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    parts: list[str] = []
    current = ""
    for char in cleaned:
        current += char
        if len(current) >= max_chars and char in "。、！？!?":
            parts.append(current.strip())
            current = ""
    if current.strip():
        parts.append(current.strip())
    return parts or [cleaned[:max_chars] or "Claude Code広告自動化"]


def fish_tts(text: str, out_path: Path, reference_id: str, model: str, speed: str) -> bool:
    key = os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY")
    if not key:
        raise RuntimeError("Fish Audio APIキーがありません。")
    body = {
        "text": text,
        "reference_id": reference_id,
        "format": "mp3",
        "sample_rate": 44100,
        "mp3_bitrate": 128,
        "latency": "normal",
        "prosody": {"speed": float(speed or "1.03"), "volume": 0, "normalize_loudness": True},
    }
    req = urllib.request.Request(
        "https://api.fish.audio/v1/tts",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "model": model or "s2-pro"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as res:
            out_path.write_bytes(res.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Fish Audioエラー HTTP {exc.code}: {detail}") from exc
    return True


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


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    x, y = xy
    draw.text((x, y), text, font=fnt, fill=fill)


def draw_scene(text: str, scene_index: int, total_scenes: int) -> Image.Image:
    palettes = [
        ((12, 24, 38), (28, 148, 126), (255, 170, 54)),
        ((18, 30, 50), (42, 98, 180), (255, 170, 54)),
        ((20, 32, 38), (65, 150, 105), (240, 178, 65)),
        ((22, 24, 32), (155, 83, 40), (255, 170, 54)),
    ]
    bg, accent, orange = palettes[scene_index % len(palettes)]
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    for i in range(0, W, 80):
        shade = tuple(min(255, int(bg[j] + (i / W) * 35)) for j in range(3))
        draw.rectangle([i, 0, i + 80, H], fill=shade)

    draw.rectangle([0, 0, W, 76], fill=(8, 17, 29))
    draw.text((44, 16), "Claude Code", font=font(42), fill=(255, 255, 255))
    draw.rounded_rectangle([1010, 14, 1228, 62], radius=16, fill=orange)
    badge = "自動化"
    badge_font = font(28)
    draw.text((1010 + (218 - text_width(draw, badge, badge_font)) / 2, 21), badge, font=badge_font, fill=(25, 20, 12))

    draw.rounded_rectangle([70, 120, 1210, 590], radius=18, fill=(255, 255, 255), outline=(220, 230, 238), width=2)
    draw.rectangle([95, 155, 390, 188], fill=accent)
    draw.rectangle([95, 212, 560, 234], fill=(222, 231, 238))
    draw.rectangle([95, 252, 760, 274], fill=(222, 231, 238))
    draw.rectangle([95, 292, 680, 314], fill=(222, 231, 238))
    draw.line([95, 390, 350, 390, 350, 455, 610, 455, 610, 390, 900, 390], fill=orange, width=6)

    label_font = font(26)
    for x, label in [(100, "投稿"), (342, "LP"), (602, "PDF"), (890, "登録")]:
        draw.rounded_rectangle([x, 350, x + 118, 432], radius=10, fill=(250, 252, 255), outline=(180, 195, 210), width=2)
        draw.text((x + (118 - text_width(draw, label, label_font)) / 2, 374), label, font=label_font, fill=(20, 30, 42))

    caption_font = font(35)
    lines = wrap_text(draw, text, caption_font, 1060)[:3]
    y = 498
    for line in lines:
        draw.text((95, y), line, font=caption_font, fill=(15, 25, 38))
        y += 46
    draw.text((1040, 650), f"{scene_index + 1}/{total_scenes}", font=font(24), fill=(220, 230, 238))
    return img


def render_body(script: str, out_path: Path, duration: float, max_chars: int) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    parts = chunks(script, max_chars)
    total_frames = max(1, int(duration * FPS))
    scene_frames = max(1, int(5 * FPS))
    cmd = [
        ffmpeg, "-y", "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for frame_index in range(total_frames):
            scene_index = min(len(parts) - 1, (frame_index // scene_frames) % len(parts))
            proc.stdin.write(draw_scene(parts[scene_index], scene_index, len(parts)).tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("Bロール動画の生成に失敗しました。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--broll-script", default="")
    parser.add_argument("--project", required=True)
    parser.add_argument("--fish-reference-id", default="")
    parser.add_argument("--fish-model", default="s2-pro")
    parser.add_argument("--fish-speed", default="1.03")
    parser.add_argument("--max-chars", type=int, default=34)
    parser.add_argument("--tone-sample", default="professional_clean")
    args = parser.parse_args()

    script = Path(args.script).read_text(encoding="utf-8")
    broll_script = script
    if args.broll_script:
        broll_script = Path(args.broll_script).read_text(encoding="utf-8")
    out_dir = ROOT / "output" / args.project
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "narration.mp3"
    body_path = out_dir / "body.mp4"
    fish_tts(script, audio_path, args.fish_reference_id, args.fish_model, args.fish_speed)
    render_body(broll_script, body_path, max(20.0, media_duration(audio_path) - 10.0), args.max_chars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
