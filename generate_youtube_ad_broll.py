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
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2 - 2), text, font=fnt, fill=fill)


def draw_heavy_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    x, y = xy
    for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        draw.text((x + dx, y + dy), text, font=fnt, fill=fill)


def draw_soft_background(draw: ImageDraw.ImageDraw) -> None:
    for y in range(H):
        mix = y / H
        draw.line([(0, y), (W, y)], fill=(int(249 - mix * 12), int(253 - mix * 8), 255))
    draw.polygon([(835, 0), (W, 0), (W, 282), (1010, 252)], fill=(215, 236, 250))
    draw.polygon([(0, 610), (430, 548), (W, 640), (W, H), (0, H)], fill=(244, 250, 254))
    draw.ellipse([960, -60, 1290, 270], outline=(204, 228, 245), width=2)


def draw_laptop_visual(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, accent: tuple[int, int, int]) -> None:
    screen = [x + 18, y, x + w - 18, y + h - 24]
    draw.rounded_rectangle(screen, radius=18, fill=(17, 55, 88), outline=(100, 157, 194), width=3)
    draw.rectangle([screen[0] + 26, screen[1] + 24, screen[2] - 26, screen[3] - 24], fill=(9, 33, 57))
    play_cx = (screen[0] + screen[2]) // 2
    play_cy = (screen[1] + screen[3]) // 2
    draw.ellipse([play_cx - 42, play_cy - 42, play_cx + 42, play_cy + 42], fill=(34, 105, 154))
    draw.polygon([(play_cx - 12, play_cy - 24), (play_cx - 12, play_cy + 24), (play_cx + 26, play_cy)], fill=(255, 255, 255))
    for i in range(4):
        yy = screen[1] + 42 + i * 36
        draw.rounded_rectangle([screen[2] - 96, yy, screen[2] - 36, yy + 22], radius=5, fill=(36, 92, 132))
    base_y = y + h - 16
    draw.polygon([(x, base_y), (x + w, base_y), (x + w - 76, base_y + 34), (x + 70, base_y + 34)], fill=(188, 213, 231))
    draw.rectangle([x + 92, base_y + 11, x + w - 92, base_y + 19], fill=(146, 182, 207))
    bar_x = x - 110
    for i, bh in enumerate([30, 54, 76, 108]):
        draw.rounded_rectangle([bar_x + i * 30, y + 142 - bh, bar_x + i * 30 + 18, y + 142], radius=5, fill=(178, 216, 240))
    draw.line([bar_x - 6, y + 118, bar_x + 96, y + 42, bar_x + 142, y + 18], fill=(255, 255, 255), width=5)
    draw.line([bar_x - 6, y + 118, bar_x + 96, y + 42, bar_x + 142, y + 18], fill=accent, width=3)


def draw_icon_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], icon: str, title: str, accent: tuple[int, int, int]) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=10, fill=(245, 250, 254), outline=(210, 228, 241), width=2)
    cx = (x1 + x2) // 2
    if icon == "edit":
        draw.rectangle([cx - 30, y1 + 18, cx + 28, y1 + 58], fill=(31, 55, 78))
        draw.rectangle([cx - 24, y1 + 24, cx + 22, y1 + 31], fill=(113, 174, 214))
        draw.rectangle([cx - 24, y1 + 38, cx + 16, y1 + 45], fill=(80, 134, 173))
        draw.polygon([(cx - 34, y1 + 15), (cx + 20, y1 + 15), (cx + 34, y1 + 28), (cx - 20, y1 + 28)], fill=(55, 79, 101))
    else:
        draw.rounded_rectangle([cx - 22, y1 + 20, cx + 22, y1 + 58], radius=8, fill=(35, 45, 58))
        draw.rounded_rectangle([cx - 15, y1 + 29, cx + 15, y1 + 49], radius=5, fill=(230, 53, 42))
        draw.polygon([(cx - 4, y1 + 32), (cx - 4, y1 + 47), (cx + 9, y1 + 39)], fill=(255, 255, 255))
    title_font = font(21)
    lines = wrap_text(draw, title, title_font, x2 - x1 - 20)[:3]
    yy = y1 + 68
    for line in lines:
        draw_centered(draw, (x1 + 8, yy, x2 - 8, yy + 28), line, title_font, accent)
        yy += 27


def draw_flow_cards(draw: ImageDraw.ImageDraw, y: int, accent: tuple[int, int, int], orange: tuple[int, int, int]) -> None:
    cards = [("投稿", 92), ("プロフィール", 150), ("LP", 94), ("PDF", 96), ("登録", 96)]
    x = 525
    gap = 17
    for idx, (label, card_w) in enumerate(cards):
        x1 = x
        draw.rounded_rectangle([x1, y, x1 + card_w, y + 72], radius=12, fill=(255, 255, 255), outline=(188, 213, 231), width=2)
        label_font = font(23 if label == "プロフィール" else 24)
        draw_centered(draw, (x1 + 4, y + 13, x1 + card_w - 4, y + 59), label, label_font, (18, 34, 52))
        x = x1 + card_w
        if idx < len(cards) - 1:
            draw.line([x + 3, y + 36, x + gap - 4, y + 36], fill=orange, width=5)
            draw.polygon([(x + gap - 4, y + 36), (x + gap - 15, y + 28), (x + gap - 15, y + 44)], fill=orange)
            x += gap
    draw.rounded_rectangle([525, y + 100, 1165, y + 150], radius=12, fill=accent)
    draw_centered(draw, (525, y + 103, 1165, y + 147), "流れを整えると、発信が成果につながる", font(25), (255, 255, 255))


def draw_main_headline(draw: ImageDraw.ImageDraw, text: str, accent: tuple[int, int, int]) -> None:
    headline = text.strip()
    sentence_parts = [part.strip() for part in headline.split("。") if part.strip()]
    if len(sentence_parts) > 1 and len(headline) > 42:
        headline = sentence_parts[0] + "。"
    headline_font = font(54)
    lines = wrap_text(draw, headline, headline_font, 720)[:3]
    y = 68
    keywords = ["Claude Code", "自動化", "LP", "PDF", "登録", "無料教材", "セミナー", "受け皿", "根本的"]
    for line in lines:
        fill = accent if any(k in line for k in keywords) else (18, 26, 38)
        draw_heavy_text(draw, (52, y), line, headline_font, fill)
        y += 68


def draw_scene(text: str, scene_index: int, total_scenes: int) -> Image.Image:
    accents = [(7, 62, 145), (16, 112, 172), (31, 126, 95), (26, 77, 150)]
    accent = accents[scene_index % len(accents)]
    orange = (255, 171, 48)
    img = Image.new("RGB", (W, H), (248, 252, 255))
    draw = ImageDraw.Draw(img)
    draw_soft_background(draw)

    draw.rounded_rectangle([44, 22, 206, 58], radius=18, fill=(241, 247, 252), outline=(210, 229, 243), width=1)
    draw_centered(draw, (44, 21, 206, 57), "Claude Code", font(22), (16, 42, 72))
    draw.rounded_rectangle([1038, 22, 1218, 62], radius=20, fill=orange)
    draw_centered(draw, (1038, 20, 1218, 60), "自動化", font(26), (16, 34, 48))

    draw_main_headline(draw, text, accent)
    draw_laptop_visual(draw, 875, 92, 315, 188, accent)

    draw.line([0, 308, W, 308], fill=(204, 226, 242), width=2)
    draw_icon_card(draw, (58, 350, 236, 490), "edit", "投稿文だけで終わらせない", accent)
    draw.text((285, 382), "×", font=font(72), fill=(199, 221, 238))
    draw_icon_card(draw, (330, 350, 508, 490), "yt", "動画台本を増やすだけにしない", accent)
    draw_flow_cards(draw, 358, accent, orange)

    draw.line([0, SUBTITLE_SAFE_Y, W, SUBTITLE_SAFE_Y], fill=(218, 235, 247), width=2)
    draw.text((1160, 525), f"{scene_index + 1}/{total_scenes}", font=font(22), fill=(93, 122, 146))
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
