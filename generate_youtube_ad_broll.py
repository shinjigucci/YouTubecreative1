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
SUBTITLE_SAFE_Y = 560
JAPANESE_FONT_URL = "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"
FONT_CACHE = ROOT / "fonts" / "NotoSansCJKjp-Regular.otf"
FALLBACK_LINES = [
    "投稿から登録までの流れを整える。",
    "Claude Codeで受け皿づくりを自動化する。",
    "LP、PDF、無料教材まで一貫して準備する。",
    "投稿を増やす前に、成果につながる導線を作る。",
]
MOJIBAKE_MARKERS = set("�縺繧譁蜍謚逋譛蛹荳荳螳蟆驥豌")
_FONT_OBJECTS: dict[tuple[int, bool], ImageFont.ImageFont] = {}
_FONT_READY: Path | None = None


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail[-3000:] or f"Command failed: {' '.join(cmd)}")


def japanese_font_path(bold: bool = True) -> Path | None:
    global _FONT_READY
    if _FONT_READY and _FONT_READY.exists():
        return _FONT_READY
    candidates = [
        ROOT / "fonts" / "NotoSansJP-VF.ttf",
        ROOT / "fonts" / "NotoSansCJKjp-Regular.otf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/NotoSansJP-VF.ttf"),
        Path("C:/Windows/Fonts/YuGothB.ttc" if bold else "C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc"),
    ]
    for item in candidates:
        if item.exists():
            _FONT_READY = item
            return item
    try:
        FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        if not FONT_CACHE.exists() or FONT_CACHE.stat().st_size < 1_000_000:
            urllib.request.urlretrieve(JAPANESE_FONT_URL, FONT_CACHE)
        if FONT_CACHE.exists() and FONT_CACHE.stat().st_size > 1_000_000:
            _FONT_READY = FONT_CACHE
            return FONT_CACHE
    except Exception:
        return None
    return None


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    key = (size, bold)
    if key in _FONT_OBJECTS:
        return _FONT_OBJECTS[key]
    path = japanese_font_path(bold)
    if path:
        _FONT_OBJECTS[key] = ImageFont.truetype(str(path), size)
        return _FONT_OBJECTS[key]
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
    cleaned = " ".join(text.lstrip("\ufeff").replace("\r", " ").replace("\n", " ").split())
    parts: list[str] = []
    current = ""
    for char in cleaned:
        current += char
        if len(current) >= max_chars and char in "。！？、,.!?":
            parts.append(current.strip())
            current = ""
    if current.strip():
        parts.append(current.strip())
    return parts or [cleaned[:max_chars] or "Claude Code広告自動化"]


def clean_display_text(text: str, scene_index: int) -> str:
    cleaned = " ".join((text or "").lstrip("\ufeff").replace("\r", " ").replace("\n", " ").split())
    if not cleaned:
        return FALLBACK_LINES[scene_index % len(FALLBACK_LINES)]
    question_ratio = cleaned.count("?") / max(1, len(cleaned))
    mojibake_hits = sum(1 for char in cleaned if char in MOJIBAKE_MARKERS)
    if "????" in cleaned or question_ratio > 0.08 or mojibake_hits >= 3:
        return FALLBACK_LINES[scene_index % len(FALLBACK_LINES)]
    return cleaned


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


def draw_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    x1, y1, x2, y2 = box
    size = getattr(fnt, "size", 24)
    draw_fit_text(draw, box, text, size, 12, fill, max_lines=1, align="center")


def draw_heavy_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    x, y = xy
    for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        draw.text((x + dx, y + dy), text, font=fnt, fill=fill)


def line_height(draw: ImageDraw.ImageDraw, fnt: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), "?", font=fnt)
    return max(1, box[3] - box[1])


def ellipsize_to_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_width: int) -> str:
    if text_width(draw, text, fnt) <= max_width:
        return text
    suffix = "..."
    trimmed = text
    while trimmed and text_width(draw, trimmed + suffix, fnt) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + suffix) if trimmed else suffix


def fit_wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int,
    max_lines: int,
    bold: bool = True,
) -> tuple[ImageFont.ImageFont, list[str], int]:
    for size in range(start_size, min_size - 1, -2):
        fnt = font(size, bold)
        lh = line_height(draw, fnt) + max(6, size // 7)
        lines = wrap_text(draw, text, fnt, max_width)
        if len(lines) <= max_lines and len(lines) * lh <= max_height:
            return fnt, lines, lh
    fnt = font(min_size, bold)
    lh = line_height(draw, fnt) + max(5, min_size // 7)
    lines = wrap_text(draw, text, fnt, max_width)[:max_lines]
    if lines:
        lines[-1] = ellipsize_to_width(draw, lines[-1], fnt, max_width)
    return fnt, lines, lh


def draw_fit_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    start_size: int,
    min_size: int,
    fill: tuple[int, int, int],
    max_lines: int = 1,
    bold: bool = True,
    align: str = "left",
    heavy: bool = False,
) -> None:
    x1, y1, x2, y2 = box
    fnt, lines, lh = fit_wrapped_lines(draw, text, x2 - x1, y2 - y1, start_size, min_size, max_lines, bold)
    total_h = len(lines) * lh
    y = y1 + max(0, (y2 - y1 - total_h) // 2)
    for line in lines:
        if align == "center":
            x = x1 + max(0, (x2 - x1 - text_width(draw, line, fnt)) // 2)
        else:
            x = x1
        if heavy:
            draw_heavy_text(draw, (int(x), int(y)), line, fnt, fill)
        else:
            draw.text((int(x), int(y)), line, font=fnt, fill=fill)
        y += lh



def draw_soft_background(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
    for y in range(H):
        mix = y / H
        draw.line([(0, y), (W, y)], fill=(int(250 - mix * 8), int(253 - mix * 12), 255))
    draw.rectangle([0, 0, W, 70], fill=(10, 24, 38))
    draw.rectangle([0, 68, W, 73], fill=(255, 171, 48))
    draw.polygon([(850, 72), (W, 72), (W, 318), (1015, 286)], fill=(220, 239, 251))
    draw.polygon([(0, SUBTITLE_SAFE_Y), (W, SUBTITLE_SAFE_Y - 46), (W, H), (0, H)], fill=(245, 250, 253))
    for x in range(0, W, 80):
        draw.line([(x, 74), (x, SUBTITLE_SAFE_Y - 6)], fill=(237, 246, 252), width=1)
    draw.line([(0, SUBTITLE_SAFE_Y), (W, SUBTITLE_SAFE_Y)], fill=(212, 232, 246), width=2)


def draw_brand_header(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
    draw.text((42, 18), "Claude Code", font=font(30), fill=(255, 255, 255))
    draw.rounded_rectangle([1038, 15, 1218, 55], radius=20, fill=(255, 171, 48))
    draw_fit_text(draw, [1060, 17, 1196, 53], "\u81ea\u52d5\u5316", 25, 18, (18, 34, 48), max_lines=1, align="center", heavy=True)


def draw_laptop_visual(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, accent: tuple[int, int, int]) -> None:
    screen = [x + 18, y, x + w - 18, y + h - 28]
    draw.rounded_rectangle(screen, radius=20, fill=(18, 58, 92), outline=(116, 172, 208), width=3)
    draw.rectangle([screen[0] + 26, screen[1] + 22, screen[2] - 26, screen[3] - 22], fill=(9, 32, 55))
    cx = (screen[0] + screen[2]) // 2
    cy = (screen[1] + screen[3]) // 2
    draw.ellipse([cx - 42, cy - 42, cx + 42, cy + 42], fill=(42, 119, 170))
    draw.polygon([(cx - 10, cy - 25), (cx - 10, cy + 25), (cx + 30, cy)], fill=(255, 255, 255))
    for i in range(3):
        yy = screen[1] + 42 + i * 42
        draw.rounded_rectangle([screen[2] - 110, yy, screen[2] - 36, yy + 26], radius=6, fill=(45, 103, 143))
    base_y = y + h - 20
    draw.polygon([(x, base_y), (x + w, base_y), (x + w - 82, base_y + 38), (x + 78, base_y + 38)], fill=(188, 214, 232))
    draw.rectangle([x + 96, base_y + 13, x + w - 96, base_y + 22], fill=(143, 183, 210))
    bx = x - 110
    for i, bh in enumerate([36, 62, 88, 122]):
        draw.rounded_rectangle([bx + i * 34, y + 150 - bh, bx + i * 34 + 20, y + 150], radius=5, fill=(178, 216, 240))
    draw.line([bx - 8, y + 128, bx + 92, y + 42, bx + 150, y + 18], fill=(255, 255, 255), width=6)
    draw.line([bx - 8, y + 128, bx + 92, y + 42, bx + 150, y + 18], fill=accent, width=3)


def headline_from_text(text: str, scene_index: int) -> str:
    cleaned = clean_display_text(text, scene_index)
    endings = ["\u3002", "\uff01", "\uff1f", "!", "?"]
    cut = len(cleaned)
    for mark in endings:
        pos = cleaned.find(mark)
        if 10 <= pos < cut:
            cut = pos + 1
    headline = cleaned[:cut].strip()
    if len(headline) > 46:
        headline = headline[:46].rstrip("\u3001, ") + "\u3002"
    return headline


def draw_big_headline(draw: ImageDraw.ImageDraw, text: str, accent: tuple[int, int, int], scene_index: int) -> None:
    headline = headline_from_text(text, scene_index)
    fnt, lines, lh = fit_wrapped_lines(draw, headline, 760, 245, 64, 40, 3, True)
    y = 122
    key_terms = ["Claude Code", "\u81ea\u52d5\u5316", "LP", "PDF", "\u767b\u9332", "\u7121\u6599\u6559\u6750", "\u30bb\u30df\u30ca\u30fc", "\u53d7\u3051\u76bf"]
    for line in lines:
        fill = accent if any(k in line for k in key_terms) else (17, 28, 42)
        draw_heavy_text(draw, (52, y), line, fnt, fill)
        y += lh


def draw_large_step(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, body: str, accent: tuple[int, int, int], orange: tuple[int, int, int]) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=18, fill=(255, 255, 255), outline=(202, 224, 239), width=2)
    draw.rounded_rectangle([x1 + 18, y1 + 18, x1 + 72, y1 + 72], radius=14, fill=(232, 244, 252))
    draw.rectangle([x1 + 35, y1 + 33, x1 + 55, y1 + 57], fill=accent)
    draw_fit_text(draw, [x1 + 92, y1 + 15, x2 - 18, y1 + 62], title, 34, 22, accent, max_lines=1, heavy=True)
    draw_fit_text(draw, [x1 + 92, y1 + 70, x2 - 18, y2 - 16], body, 26, 18, (29, 43, 58), max_lines=2)


def draw_flow_summary(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int], orange: tuple[int, int, int]) -> None:
    labels = ["\u6295\u7a3f", "LP", "PDF", "\u767b\u9332"]
    x = 612
    y = 410
    for i, label in enumerate(labels):
        draw.rounded_rectangle([x, y, x + 106, y + 70], radius=15, fill=(255, 255, 255), outline=(190, 215, 232), width=2)
        draw_fit_text(draw, [x + 8, y + 12, x + 98, y + 58], label, 32, 22, (17, 28, 42), max_lines=1, align="center", heavy=True)
        if i < len(labels) - 1:
            draw.line([x + 112, y + 35, x + 166, y + 35], fill=orange, width=6)
            draw.polygon([(x + 166, y + 35), (x + 150, y + 24), (x + 150, y + 46)], fill=orange)
        x += 164
    draw_fit_text(draw, [612, 494, 1218, 540], "\u767a\u4fe1\u3092\u3001\u767b\u9332\u307e\u3067\u306e\u6d41\u308c\u306b\u5909\u3048\u308b", 31, 22, accent, max_lines=1, align="center", heavy=True)


def draw_scene(text: str, scene_index: int, total_scenes: int) -> Image.Image:
    accents = [(7, 62, 145), (16, 112, 172), (31, 126, 95), (26, 77, 150)]
    accent = accents[scene_index % len(accents)]
    orange = (255, 171, 48)
    img = Image.new("RGB", (W, H), (248, 252, 255))
    draw = ImageDraw.Draw(img)
    draw_soft_background(draw, accent)
    draw_brand_header(draw, accent)
    draw_big_headline(draw, text, accent, scene_index)
    draw_laptop_visual(draw, 910, 116, 260, 160, accent)

    draw_large_step(
        draw,
        (52, 392, 590, 535),
        "\u53d7\u3051\u76bf\u3092\u5148\u306b\u6574\u3048\u308b",
        "\u6295\u7a3f\u306e\u524d\u306b\u3001\u30d7\u30ed\u30d5\u30a3\u30fc\u30eb\u30fbLP\u30fb\u7121\u6599\u6559\u6750\u307e\u3067\u6e96\u5099\u3059\u308b",
        accent,
        orange,
    )
    draw_flow_summary(draw, accent, orange)

    draw_fit_text(draw, [1010, 525, 1218, 552], f"{scene_index + 1}/{total_scenes}", 22, 16, (92, 121, 145), max_lines=1, align="right")
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


