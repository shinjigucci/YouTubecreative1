import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx


ROOT = Path(__file__).resolve().parent
PYTHON = Path(r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
if not PYTHON.exists():
    PYTHON = "python"

JOBS = {}
JOBS_LOCK = threading.Lock()
KLING_LOCK = threading.Lock()
KLING_MODEL = "kling-v1"
KLING_MODE = "std"
KLING_CREATE_RETRY_DELAYS = [180, 300, 420, 600, 900]


def now_project_name():
    return "webapp_" + time.strftime("%Y%m%d_%H%M%S")


def safe_project_name(name):
    name = (name or "").strip() or now_project_name()
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
    return name[:80] or now_project_name()


def first_sentences(script, count=3):
    parts = [p.strip() for p in script.replace("\r\n", "\n").split("。") if p.strip()]
    sentences = [p + "。" for p in parts]
    return sentences[:count]


def fallback_kling_prompts(script):
    sentences = first_sentences(script, 3)
    hook = sentences[0] if sentences else "A strong business problem appears."
    next_line = sentences[1] if len(sentences) > 1 else "The business owner creates many posts but has no clear next step."
    prompt1 = (
        "A cinematic 5-second Japanese YouTube ad opening. A small business owner sits at a laptop, "
        "overwhelmed by marketing work and social media planning, then notices an AI coding automation workflow "
        "on the screen. Show a polished landing-page mockup, document cards, and automation flow elements. "
        "The mood clearly expresses the hook: "
        f"{hook} Modern Japanese business atmosphere, realistic live-action style, clean cinematic lighting, "
        "smooth camera move, no readable text, no logos, 16:9, professional ad hook, high quality. "
        "Leave clear space near the top left and top right for brand overlays."
    )
    prompt2 = (
        "A cinematic 5-second continuation for a Japanese YouTube ad. A small business owner rapidly creates "
        "many social media posts, landing page sections, PDF lead magnet screens, and video script drafts on a laptop. "
        "Multiple document windows and landing-page mockups connect into an automation funnel, but the viewer can "
        "clearly feel the contrast between scattered content and an organized automated path. "
        f"The scene should match this narration idea: {next_line} Show abstract UI cards flowing but stopping at a blank dead end. "
        "Modern Japanese business office, realistic live-action style, clean lighting, smooth camera movement, "
        "no readable text, no logos, 16:9, professional ad b-roll, high quality. "
        "Leave clear space near the top left and top right for brand overlays."
    )
    return prompt1, prompt2


def openai_kling_prompts(openai_key, model, script):
    if not openai_key:
        return fallback_kling_prompts(script)
    instruction = (
        "Create two English prompts for Kling text-to-video. Each prompt must be exactly one 5-second 16:9 live-action "
        "business ad scene. No readable text, no logos, no native audio. The final app will overlay a Claude Code brand "
        "plate and an orange automation badge, so leave clean space near the top left and top right. Prompt 1 visualizes "
        "the opening hook with a premium AI automation workflow and attractive landing-page mockup elements. "
        "Prompt 2 visualizes the next narration line: creating many posts/scripts, LP sections, PDF lead magnet elements, "
        "and then organizing them into a marketing automation funnel. Emphasize automation visually. "
        "Return JSON with keys prompt1 and prompt2 only."
    )
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or "gpt-4.1-mini",
                "input": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": script},
                ],
                "text": {"format": {"type": "json_object"}},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("output_text")
        if not text:
            chunks = []
            for item in data.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        chunks.append(content.get("text", ""))
            text = "\n".join(chunks)
        parsed = json.loads(text)
        return parsed["prompt1"], parsed["prompt2"]
    except Exception:
        return fallback_kling_prompts(script)


def retry_after_seconds(response, fallback):
    value = response.headers.get("retry-after")
    if value:
        try:
            return max(1, min(900, int(float(value))))
        except ValueError:
            return fallback
    return fallback


def kling_rate_limit_error():
    return RuntimeError(
        "Kling側の一時制限に当たりました。無料枠、APIクレジット不足、または同時生成制限の可能性があります。"
        "Kling Open Platform側でAPIクレジット/課金状態を確認してください。短時間に何度も生成すると 429 Too Many Requests が出ます。"
    )


def kling_create(api_key, prompt, mode, duration, aspect_ratio, model_name):
    payload = {
        "model_name": model_name or "kling-v1",
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
        "mode": mode,
    }
    response = None
    for attempt in range(len(KLING_CREATE_RETRY_DELAYS) + 1):
        response = httpx.post(
            "https://api.klingai.com/v1/videos/text2video",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if response.status_code != 429:
            break
        if attempt >= len(KLING_CREATE_RETRY_DELAYS):
            raise kling_rate_limit_error()
        time.sleep(retry_after_seconds(response, KLING_CREATE_RETRY_DELAYS[attempt]))
    if response.status_code == 429:
        raise kling_rate_limit_error()
    response.raise_for_status()
    data = response.json()
    task_id = (data.get("data") or {}).get("task_id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"Kling task_id was not returned: {data}")
    return task_id


def kling_poll(api_key, task_id, timeout_seconds=900):
    url = f"https://api.klingai.com/v1/videos/text2video/{task_id}"
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        response = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
        if response.status_code == 429:
            time.sleep(retry_after_seconds(response, 180))
            continue
        response.raise_for_status()
        data = response.json()
        last = data
        body = data.get("data") or data
        status = str(body.get("task_status") or body.get("status") or "").lower()
        result = body.get("task_result") or body.get("result") or {}
        videos = result.get("videos") or body.get("videos") or []
        if videos:
            video_url = videos[0].get("url") or videos[0].get("video_url")
            if video_url:
                return video_url
        if status in {"failed", "fail", "error"}:
            raise RuntimeError(f"Kling generation failed: {data}")
        time.sleep(8)
    raise TimeoutError(f"Kling generation timed out: {last}")


def download_file(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=180) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_bytes():
                if chunk:
                    handle.write(chunk)


def set_status(job_id, status=None, progress=None, message=None, result=None, error=None):
    with JOBS_LOCK:
        job = JOBS[job_id]
        if status:
            job["status"] = status
        if progress is not None:
            job["progress"] = progress
        if message is not None:
            job["message"] = message
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error


def run_job(job_id, form):
    project = safe_project_name(form.get("project", [""])[0])
    script = form.get("script", [""])[0].strip()
    openai_key = form.get("openai_key", [""])[0].strip()
    openai_model = form.get("openai_model", ["gpt-4.1-mini"])[0].strip()
    kling_api_key = form.get("kling_api_key", [""])[0].strip()
    kling_model = KLING_MODEL
    kling_mode = KLING_MODE
    fish_key = form.get("fish_key", [""])[0].strip()
    fish_reference_id = form.get("fish_reference_id", ["63bc41e652214372b15d9416a30a60b4"])[0].strip()
    fish_model = form.get("fish_model", ["s2-pro"])[0].strip()
    fish_speed = form.get("fish_speed", ["1.03"])[0].strip()

    try:
        if not script:
            raise ValueError("台本が空です。")
        if not kling_api_key:
            raise ValueError("Kling API Key が必要です。")
        if not fish_key:
            raise ValueError("Fish Audio APIキーが必要です。")

        set_status(job_id, "running", 5, "台本を保存しています")
        input_dir = ROOT / "output" / project / "web_input"
        input_dir.mkdir(parents=True, exist_ok=True)
        script_path = input_dir / "script.txt"
        script_path.write_text(script, encoding="utf-8")

        set_status(job_id, "running", 10, "Kling用プロンプトを作成しています")
        prompt1, prompt2 = openai_kling_prompts(openai_key, openai_model, script)
        (input_dir / "kling_prompts.json").write_text(
            json.dumps({"prompt1": prompt1, "prompt2": prompt2}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not KLING_LOCK.acquire(blocking=False):
            raise RuntimeError("別の動画でKling生成中です。完了してから1本ずつ再実行してください。")
        try:
            set_status(job_id, "running", 18, "Klingでフック動画を生成しています（公式API / std）")
            task1 = kling_create(kling_api_key, prompt1, kling_mode, 5, "16:9", kling_model)
            url1 = kling_poll(kling_api_key, task1)
            hook_path = input_dir / "hook.mp4"
            download_file(url1, hook_path)

            set_status(job_id, "running", 42, "Klingで次の5秒動画を生成しています（1本ずつ処理中）")
            task2 = kling_create(kling_api_key, prompt2, kling_mode, 5, "16:9", kling_model)
            url2 = kling_poll(kling_api_key, task2)
            next_path = input_dir / "kling_next5.mp4"
            download_file(url2, next_path)
        finally:
            KLING_LOCK.release()

        set_status(job_id, "running", 68, "Fish AudioナレーションとBロールを生成しています")
        env = os.environ.copy()
        env["FISH_API_KEY"] = fish_key
        env["FISH_REFERENCE_ID"] = fish_reference_id
        env["FISH_MODEL"] = fish_model
        env["FISH_SPEED"] = fish_speed
        generate_cmd = [
            str(PYTHON),
            "scripts/generate_youtube_ad_broll.py",
            "--script",
            str(script_path),
            "--project",
            project,
            "--fish-reference-id",
            fish_reference_id,
            "--fish-model",
            fish_model,
            "--fish-speed",
            fish_speed,
            "--max-chars",
            "34",
        ]
        subprocess.run(generate_cmd, cwd=ROOT, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        set_status(job_id, "running", 88, "Kling 10秒と本編を結合しています")
        assemble_cmd = [
            str(PYTHON),
            "scripts/assemble_two_kling_intro.py",
            "--project",
            project,
            "--script",
            str(script_path),
            "--kling-hook",
            str(hook_path),
            "--kling-next",
            str(next_path),
        ]
        subprocess.run(assemble_cmd, cwd=ROOT, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        final_path = ROOT / "output" / project / "youtube_ad_broll_kling10.mp4"
        final_copy = ROOT / "output" / "final_complete" / f"{project}_kling10.mp4"
        final_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_path, final_copy)
        set_status(
            job_id,
            "complete",
            100,
            "完成しました",
            result={
                "project": project,
                "video": str(final_path),
                "final_copy": str(final_copy),
                "download": f"/download?job={job_id}",
                "open_folder": f"/open-folder?job={job_id}",
            },
        )
    except Exception as exc:
        set_status(job_id, "error", message="生成に失敗しました", error=str(exc))


HTML = r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Claude Code広告自動化クリエイター 完全版</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17202a;
      --muted: #65758b;
      --line: #d9e1ea;
      --panel: #ffffff;
      --bg: #eef3f7;
      --accent: #1d8f7a;
      --danger: #c0392b;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Yu Gothic", "Meiryo", system-ui, sans-serif; background: var(--bg); color: var(--ink); }
    header { background: #0d1724; color: #fff; padding: 18px 28px; display: flex; justify-content: space-between; gap: 20px; align-items: center; }
    header h1 { font-size: 20px; margin: 0; letter-spacing: 0; }
    header span { color: #8ee0d4; font-size: 13px; }
    main { max-width: 1180px; margin: 0 auto; padding: 24px; }
    form { display: grid; grid-template-columns: 1.2fr .8fr; gap: 18px; align-items: start; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; }
    h2 { margin: 0 0 14px; font-size: 16px; }
    label { display: block; font-size: 13px; font-weight: 700; margin: 12px 0 6px; }
    input, textarea, select { width: 100%; border: 1px solid #c9d4df; border-radius: 6px; padding: 10px 11px; font: inherit; background: #fff; }
    textarea { min-height: 430px; resize: vertical; line-height: 1.7; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 8px; }
    .actions { display: flex; gap: 10px; align-items: center; margin-top: 16px; }
    button { border: 0; border-radius: 6px; background: var(--accent); color: white; padding: 11px 18px; font-weight: 700; cursor: pointer; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    #status { margin-top: 18px; display: none; }
    .bar { height: 10px; background: #dfe8ef; border-radius: 999px; overflow: hidden; }
    .bar > div { height: 100%; width: 0; background: var(--accent); transition: width .3s ease; }
    .message { margin-top: 10px; color: var(--muted); }
    .error { color: var(--danger); white-space: pre-wrap; }
    .result a { display: inline-block; margin-top: 12px; color: var(--accent); font-weight: 700; }
    @media (max-width: 900px) {
      form { grid-template-columns: 1fr; }
      textarea { min-height: 300px; }
    }
  </style>
</head>
<body>
<header>
  <h1>Claude Code広告自動化クリエイター 完全版</h1>
  <span>0〜10秒Kling / 10秒以降Claude CodeブランドBロール / Fish Audio / MP4</span>
</header>
<main>
  <form id="jobForm">
    <section>
      <h2>台本</h2>
      <label for="project">プロジェクト名</label>
      <input id="project" name="project" placeholder="claude_code_ad_01">
      <label for="script">広告台本</label>
      <textarea id="script" name="script" required placeholder="ここにYouTube動画広告の台本を貼り付け"></textarea>
    </section>
    <section>
      <h2>APIキー</h2>
      <label>OpenAI APIキー</label>
      <input name="openai_key" type="password" autocomplete="off" placeholder="sk-...">
      <div class="hint">Kling用プロンプト作成に使います。未入力でも固定テンプレートで生成します。</div>
      <label>OpenAIモデル</label>
      <input name="openai_model" value="gpt-4.1-mini">

      <div class="grid">
        <div>
          <label>Kling API Key</label>
          <input name="kling_api_key" type="password" required autocomplete="off" placeholder="api-key-kling-...">
        </div>
      </div>
      <div class="hint">Kling 3.0以降の新しいAPI Keyを貼り付けます。スクショに映ったキーは無効化し、新しく作り直したキーを使ってください。</div>
      <div class="grid">
        <div>
          <label>Klingモデル</label>
          <input name="kling_model" value="kling-v1" readonly>
        </div>
        <div>
          <label>Klingモード</label>
          <input name="kling_mode" value="std" readonly>
        </div>
      </div>
      <div class="hint">Klingは公式APIの有料クレジット前提で、通りやすさ優先の kling-v1 / std に固定しています。生成は1本ずつ処理します。</div>

      <label>Fish Audio APIキー</label>
      <input name="fish_key" type="password" required autocomplete="off">
      <label>Fish Reference ID</label>
      <input name="fish_reference_id" value="63bc41e652214372b15d9416a30a60b4">
      <div class="grid">
        <div>
          <label>Fishモデル</label>
          <input name="fish_model" value="s2-pro">
        </div>
        <div>
          <label>速度</label>
          <input name="fish_speed" value="1.03">
        </div>
      </div>
      <div class="actions">
        <button id="submitBtn" type="submit">MP4生成</button>
      </div>
      <div class="hint">APIキーはこの生成処理の中だけで使い、アプリ側では保存しません。</div>
    </section>
  </form>
  <section id="status">
    <h2>生成状況</h2>
    <div class="bar"><div id="bar"></div></div>
    <div id="message" class="message"></div>
    <div id="error" class="error"></div>
    <div id="result" class="result"></div>
  </section>
</main>
<script>
const form = document.getElementById('jobForm');
const statusBox = document.getElementById('status');
const bar = document.getElementById('bar');
const message = document.getElementById('message');
const error = document.getElementById('error');
const result = document.getElementById('result');
const submitBtn = document.getElementById('submitBtn');
let timer = null;

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submitBtn.disabled = true;
  error.textContent = '';
  result.innerHTML = '';
  statusBox.style.display = 'block';
  message.textContent = '開始しています';
  bar.style.width = '2%';
  const res = await fetch('/api/jobs', { method: 'POST', body: new URLSearchParams(new FormData(form)) });
  const data = await res.json();
  if (!res.ok) {
    submitBtn.disabled = false;
    error.textContent = data.error || '開始できませんでした';
    return;
  }
  poll(data.job_id);
});

async function poll(jobId) {
  clearTimeout(timer);
  const res = await fetch('/api/jobs/' + encodeURIComponent(jobId));
  const data = await res.json();
  bar.style.width = (data.progress || 0) + '%';
  message.textContent = data.message || '';
  if (data.status === 'error') {
    error.textContent = data.error || '生成に失敗しました';
    submitBtn.disabled = false;
    return;
  }
  if (data.status === 'complete') {
    submitBtn.disabled = false;
    result.innerHTML = `
      <a href="${data.result.download}">完成MP4をダウンロード</a>
      <button type="button" id="openFolderBtn">保存フォルダを開く</button>
      <div class="hint">${data.result.video}</div>
    `;
    document.getElementById('openFolderBtn').addEventListener('click', async () => {
      await fetch(data.result.open_folder);
    });
    return;
  }
  timer = setTimeout(() => poll(jobId), 3000);
}
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        origin = self.headers.get("Origin")
        allowed = os.environ.get("ALLOWED_ORIGIN", "*")
        if allowed == "*" or (origin and origin in [item.strip() for item in allowed.split(",")]):
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        super().end_headers()

    def send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, payload):
        self.send_text(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_text(200, HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/health":
            self.send_json(200, {"ok": True})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.send_json(404, {"error": "job not found"})
                return
            self.send_json(200, job)
            return
        if parsed.path == "/download":
            query = parse_qs(parsed.query)
            job_id = (query.get("job") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            path = Path((job or {}).get("result", {}).get("video", ""))
            if not job or not path.exists():
                self.send_json(404, {"error": "file not found"})
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/open-folder":
            query = parse_qs(parsed.query)
            job_id = (query.get("job") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            path = Path((job or {}).get("result", {}).get("video", ""))
            if not job or not path.exists():
                self.send_json(404, {"error": "folder not found"})
                return
            if hasattr(os, "startfile"):
                os.startfile(str(path.parent))
                self.send_json(200, {"ok": True})
            else:
                self.send_json(200, {"ok": False, "message": "open-folder is available only on local Windows."})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/jobs":
            self.send_json(404, {"error": "not found"})
            return
        if KLING_LOCK.locked():
            self.send_json(429, {"error": "別の動画でKling生成中です。完了してから1本ずつ再実行してください。"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = parse_qs(body)
        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0, "message": "待機中", "error": "", "result": {}}
        thread = threading.Thread(target=run_job, args=(job_id, form), daemon=True)
        thread.start()
        self.send_json(202, {"job_id": job_id})

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def main():
    port = int(os.environ.get("PORT", "7860"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), Handler)
    shown_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"Web app: http://{shown_host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
