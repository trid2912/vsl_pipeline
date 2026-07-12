#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VSL annotation web app — single shared login, full access for everyone who logs in.

Upload recording videos + fill gloss annotations against the recording sheet, in the browser.
One shared password protects the app when exposed to the internet; leave it unset for open localhost use.

Data (read-only clip list + the two working sheets + videos) lives in a recording dir:
  $ANNOTATION_DATA_DIR   (if set)   else  <repo>/final_corpus/recording
Auth: shared password from $ANNOTATION_PASSWORD, else hashed in ./auth/password.txt (set via `setpassword`),
      else the app runs OPEN (no login) — fine for localhost, NOT for the internet.

Deps: fastapi, uvicorn, jinja2, python-multipart. No database. See DEPLOY.md.

Usage:
  python3 annotation_server.py                       # serve http://127.0.0.1:8000
  python3 annotation_server.py --host 0.0.0.0 --port 8000
  python3 annotation_server.py setpassword           # set the shared password (prompts)
"""
import argparse, csv, os, shutil, threading, datetime, json, hmac, hashlib, base64, secrets, getpass, sys
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse
from jinja2 import Environment, DictLoader
import uvicorn

# ------------------------------------------------------------------ locations
def find_data_dir():
    env = os.environ.get("ANNOTATION_DATA_DIR")
    if env:
        return Path(env).resolve()
    root = Path(__file__).resolve()
    while root != root.parent and not (root / "final_corpus").is_dir():
        root = root.parent
    cand = root / "final_corpus" / "recording"
    if cand.is_dir():
        return cand
    here = Path(__file__).resolve().parent
    bundled = here / "data" / "recording"          # full recording package shipped in the repo
    if (bundled / "final_recording_script.csv").exists():
        return bundled
    return here / "sample_data" / "recording"       # last-resort tiny demo

REC_DIR = find_data_dir()
SCRIPT_CSV = REC_DIR / "final_recording_script.csv"
CAP_CSV = REC_DIR / "capture_log.csv"
GLOSS_CSV = REC_DIR / "gloss_annotation_sheet.csv"
VIDEO_DIR = REC_DIR / "videos"
AUTH_DIR = Path(__file__).resolve().parent / "auth"
PW_FILE = AUTH_DIR / "password.txt"
SECRET_FILE = AUTH_DIR / "secret.key"
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
PAGE_SIZE = 60
SESSION_DAYS = 30
SECURE_COOKIE = os.environ.get("ANNOTATION_SECURE", "") == "1"  # set when served over HTTPS
LOCK = threading.Lock()

# ------------------------------------------------------------------ auth (single shared password)
def get_secret():
    env = os.environ.get("ANNOTATION_SECRET")
    if env:
        return env.encode()
    AUTH_DIR.mkdir(exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32)); os.chmod(SECRET_FILE, 0o600)
    return SECRET_FILE.read_text().strip().encode()

def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()

def check_credentials(username, pw):
    """True if username+password match the configured shared login."""
    envp = os.environ.get("ANNOTATION_PASSWORD")
    if envp:
        envu = os.environ.get("ANNOTATION_USER", "admin")
        return hmac.compare_digest(username, envu) and hmac.compare_digest(pw, envp)
    if PW_FILE.exists():
        rec = json.loads(PW_FILE.read_text())
        return (hmac.compare_digest(username, rec.get("user", "admin"))
                and hmac.compare_digest(_hash(pw, rec["salt"]), rec["hash"]))
    return True  # nothing configured -> open

def auth_required():
    return bool(os.environ.get("ANNOTATION_PASSWORD") or PW_FILE.exists())

def make_token():
    exp = int((datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_DAYS)).timestamp())
    payload = base64.urlsafe_b64encode(json.dumps({"e": exp}).encode()).decode()
    sig = hmac.new(get_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def token_ok(token):
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, hmac.new(get_secret(), payload.encode(), hashlib.sha256).hexdigest()):
            return False
        return json.loads(base64.urlsafe_b64decode(payload.encode()))["e"] >= datetime.datetime.utcnow().timestamp()
    except Exception:
        return False

def logged_in(request):
    return (not auth_required()) or token_ok(request.cookies.get("session", ""))

# ------------------------------------------------------------------ data layer
def load_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return rd.fieldnames, {r["recording_id"]: r for r in rd}

def save_csv(path, fields, rows_by_id):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for rid in ORDER:
            if rid in rows_by_id:
                w.writerow(rows_by_id[rid])
    os.replace(tmp, path)

SCRIPT_FIELDS = CAP_FIELDS = GLOSS_FIELDS = None
CLIPS = CAP = GLOSS = {}
ORDER = []

def load_all():
    global SCRIPT_FIELDS, CLIPS, CAP_FIELDS, CAP, GLOSS_FIELDS, GLOSS, ORDER
    SCRIPT_FIELDS, CLIPS = load_csv(SCRIPT_CSV)
    CAP_FIELDS, CAP = load_csv(CAP_CSV)
    GLOSS_FIELDS, GLOSS = load_csv(GLOSS_CSV)
    ORDER = list(CLIPS.keys())

def video_file(rid):
    for ext in VIDEO_EXTS:
        p = VIDEO_DIR / f"{rid}{ext}"
        if p.exists():
            return p
    return None

def has_video(rid):
    return bool((CAP.get(rid, {}).get("video_path") or "").strip()) or video_file(rid) is not None

def ann_done(rid):
    return (GLOSS.get(rid, {}).get("annotation_status") or "").strip() == "done"

# ------------------------------------------------------------------ templates
BASE = """
<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}VSL Annotation{% endblock %}</title>
<style>
  :root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e6e5e2;--blue:#2a78d6;--ok:#0ca30c;--warn:#eda100;--card:#fff}
  *{box-sizing:border-box} body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,"DejaVu Sans",sans-serif;background:var(--bg);color:var(--ink)}
  a{color:var(--blue);text-decoration:none} a:hover{text-decoration:underline}
  header{background:#fff;border-bottom:1px solid var(--line);padding:10px 20px;position:sticky;top:0;z-index:5;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  header h1{font-size:16px;margin:0} header .stat{color:var(--ink2);font-size:13px} header .who{margin-left:auto;font-size:13px}
  .bar{height:7px;background:var(--line);border-radius:4px;width:150px;display:inline-block;vertical-align:middle;overflow:hidden}
  .bar>i{display:block;height:100%;background:var(--blue)}
  main{padding:18px 20px;max-width:1180px;margin:0 auto}
  table{border-collapse:collapse;width:100%;background:var(--card);font-size:13px}
  th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--ink2);font-weight:600;position:sticky;top:52px;background:#fbfbfa}
  tr:hover td{background:#f6f8fc}
  .pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid var(--line);color:var(--ink2)}
  .yes{color:var(--ok);font-weight:600}.no{color:#b9b7b1}
  .split-train{color:#256abf}.split-dev{color:#8a6d00}.split-sd_test{color:#4a3aa7}.split-si_test{color:#e34948}
  form.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
  select,input[type=text],input[type=password],textarea{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink)}
  textarea{width:100%;resize:vertical}
  .btn{background:var(--blue);color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer;font:inherit}
  .btn.ghost{background:#fff;color:var(--blue);border:1px solid var(--blue)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:860px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:18px}
  .card h2{margin:0 0 12px;font-size:15px}
  .meta{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--ink2);font-size:13px;margin-bottom:10px}
  .turn{padding:6px 10px;border-left:3px solid var(--blue);background:#f6f8fc;margin:5px 0;border-radius:0 6px 6px 0}
  .turn.q{border-color:#eda100;background:#fdf8ec}
  .field{margin-bottom:10px} .field label{display:block;font-weight:600;margin-bottom:3px;font-size:13px}
  video{width:100%;max-height:52vh;background:#000;border-radius:8px}
  .nav{display:flex;justify-content:space-between;margin:6px 0 16px}
  .flash{background:#eaf6ea;border:1px solid #bfe6bf;color:#186818;padding:8px 12px;border-radius:6px;margin-bottom:14px}
  .instr{background:#fdf8ec;border:1px solid #f0e4c4;padding:8px 12px;border-radius:6px;color:#6b5800;font-size:13px}
  .muted{color:var(--ink2)} code{background:#f0efec;padding:1px 4px;border-radius:4px}
  .login{max-width:340px;margin:9vh auto;background:#fff;border:1px solid var(--line);border-radius:12px;padding:26px}
  .login h1{font-size:20px;margin:0 0 4px}.err{color:#c0392b;margin:8px 0}
</style></head><body>
{% if header %}<header>
  <h1><a href="/">VSL Annotation</a></h1>
  <span class="stat">🎬 <b>{{g.vid}}</b>/{{g.total}} <span class="bar"><i style="width:{{g.vidpct}}%"></i></span></span>
  <span class="stat">📝 <b>{{g.ann}}</b>/{{g.total}} <span class="bar"><i style="width:{{g.annpct}}%"></i></span></span>
  {% if authed %}<span class="who"><a href="/logout">Đăng xuất</a></span>{% endif %}
</header>{% endif %}
<main>{% if flash %}<div class="flash">{{flash}}</div>{% endif %}{% block body %}{% endblock %}</main>
</body></html>
"""

LOGIN = """{% extends "base" %}{% block body %}
<div class="login">
  <h1>VSL Annotation</h1>
  <p class="muted">Đăng nhập bằng tài khoản chung.</p>
  {% if error %}<div class="err">{{error}}</div>{% endif %}
  <form method="post" action="/login">
    <div class="field"><label>Tên đăng nhập</label><input type="text" name="username" autofocus style="width:100%"></div>
    <div class="field"><label>Mật khẩu</label><input type="password" name="password" style="width:100%"></div>
    <button class="btn" type="submit" style="width:100%">Đăng nhập</button>
  </form>
</div>
{% endblock %}"""

INDEX = """{% extends "base" %}{% block body %}
<form class="filters" method="get">
  <input type="text" name="q" value="{{f.q}}" placeholder="Tìm id / nội dung…" size="30">
  <select name="split"><option value="">— split —</option>{% for s in splits %}<option {{'selected' if f.split==s}}>{{s}}</option>{% endfor %}</select>
  <select name="signer"><option value="">— signer —</option>{% for s in signers %}<option {{'selected' if f.signer==s}}>{{s}}</option>{% endfor %}</select>
  <select name="fmt"><option value="">— format —</option>{% for s in fmts %}<option {{'selected' if f.fmt==s}}>{{s}}</option>{% endfor %}</select>
  <select name="vid"><option value="">video: any</option><option value="yes" {{'selected' if f.vid=='yes'}}>đã có</option><option value="no" {{'selected' if f.vid=='no'}}>chưa có</option></select>
  <select name="ann"><option value="">annot: any</option><option value="done" {{'selected' if f.ann=='done'}}>done</option><option value="pending" {{'selected' if f.ann=='pending'}}>pending</option></select>
  <button class="btn" type="submit">Lọc</button><a class="btn ghost" href="/">Reset</a>
</form>
<p class="muted">{{total}} clip — trang {{page}}/{{pages}}</p>
<table><thead><tr>
  <th>recording_id</th><th>signer</th><th>split</th><th>genre</th><th>format</th><th>từ</th><th>🎬</th><th>📝</th><th>nội dung</th>
</tr></thead><tbody>
{% for r in rows %}<tr>
  <td><a href="/clip/{{r.recording_id}}">{{r.recording_id}}</a><br><span class="muted">{{r.segment_id}}</span></td>
  <td>{{r.signer_id}}</td><td class="split-{{r.split}}">{{r.split}}</td>
  <td>{{r.genre or '—'}}</td><td><span class="pill">{{r.text_format}}</span></td><td>{{r.segmented_word_count}}</td>
  <td>{% if r._vid %}<span class="yes">✓</span>{% else %}<span class="no">—</span>{% endif %}</td>
  <td>{% if r._ann %}<span class="yes">✓</span>{% else %}<span class="no">—</span>{% endif %}</td>
  <td class="muted">{{r.segment_text[:80]}}{% if r.segment_text|length>80 %}…{% endif %}</td>
</tr>{% endfor %}
</tbody></table>
<div class="nav">
  {% if page>1 %}<a class="btn ghost" href="?{{qs}}&page={{page-1}}">← Trước</a>{% else %}<span></span>{% endif %}
  {% if page<pages %}<a class="btn ghost" href="?{{qs}}&page={{page+1}}">Sau →</a>{% endif %}
</div>
{% endblock %}"""

CLIP = """{% extends "base" %}{% block body %}
<div class="nav"><a href="/">← Danh sách</a>
  <span>{% if prev %}<a href="/clip/{{prev}}">← {{prev}}</a>{% endif %} &nbsp; {% if nxt %}<a href="/clip/{{nxt}}">{{nxt}} →</a>{% endif %}</span></div>
<h2 style="margin:4px 0">{{c.recording_id}} <span class="muted" style="font-size:14px">· {{c.segment_id}}</span></h2>
<div class="meta">
  <span class="split-{{c.split}}"><b>{{c.split}}</b></span><span>signer <b>{{c.signer_id}}</b></span>
  <span>{{c.domain}}</span><span>{{c.genre or 'no-genre'}}</span><span>{{c.emotion}}</span>
  <span class="pill">{{c.length_bucket}}</span><span class="pill">{{c.text_format}}</span>
  <span>{{c.segmented_word_count}} từ · ~{{c.estimated_duration_seconds}}s</span>
  {% if c.safety_level!='green' %}<span class="pill" style="color:#e34948;border-color:#e34948">{{c.safety_level}}</span>{% endif %}
</div>
<div class="instr">📋 {{c.signer_instruction}}</div>
<div class="card" style="margin-top:14px">
  <h2>Nội dung ký {% if c.text_format=='dialog_turns' %}<span class="muted">(mỗi khối = 1 lượt)</span>{% endif %}</h2>
  {% if c.text_format=='dialog_turns' %}{% for t in turns %}<div class="turn {{'q' if t.endswith('?')}}">{{t}}</div>{% endfor %}
  {% else %}<p style="font-size:15px">{{c.segment_text}}</p>{% endif %}
</div>
<div class="grid">
  <div class="card">
    <h2>🎬 Video</h2>
    {% if vid_url %}<video controls src="{{vid_url}}"></video><p class="muted">Đã tải: <code>{{cap.video_path}}</code>{% if cap.recording_date %} · {{cap.recording_date}}{% endif %}</p>{% else %}<p class="muted">Chưa có video.</p>{% endif %}
    <form method="post" action="/clip/{{c.recording_id}}/video" enctype="multipart/form-data">
      <div class="field"><label>Chọn file video</label><input type="file" name="file" accept="video/*" required></div>
      <div class="field"><label>Take #</label><input type="text" name="take_number" value="{{cap.take_number}}" size="6"></div>
      <div class="field"><label>Ghi chú</label><input type="text" name="notes" value="{{cap.notes}}" style="width:100%"></div>
      <label style="font-weight:400"><input type="checkbox" name="q_ok" {{'checked' if cap.q_framing_full_signing_space=='y'}}> Đạt chuẩn quay (khung hình/nét/ánh sáng/≥25fps)</label>
      <div style="margin-top:10px"><button class="btn" type="submit">Tải video lên</button></div>
    </form>
  </div>
  <div class="card">
    <h2>📝 Gán nhãn gloss</h2>
    <form method="post" action="/clip/{{c.recording_id}}/annotation">
      <div class="field"><label>Chuỗi gloss vàng</label><textarea name="gold_gloss_sequence" rows="3">{{gl.gold_gloss_sequence}}</textarea></div>
      <div class="field"><label>Bản dịch tiếng Việt (đúng nội dung đã ký)</label><textarea name="vietnamese_translation" rows="3">{{gl.vietnamese_translation}}</textarea></div>
      <div class="field"><label>Đánh vần tay (FS:)</label><input type="text" name="fingerspelled_items_FS" value="{{gl.fingerspelled_items_FS}}" style="width:100%"></div>
      <div class="field"><label>Ghi chú CL:/NM:</label><input type="text" name="classifier_or_NM_notes" value="{{gl.classifier_or_NM_notes}}" style="width:100%"></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <div class="field"><label>Annotator</label><input type="text" name="annotator_id" value="{{gl.annotator_id}}" size="12"></div>
        <div class="field"><label>Trạng thái</label><select name="annotation_status">{% for s in ['pending','in_progress','done'] %}<option {{'selected' if gl.annotation_status==s}}>{{s}}</option>{% endfor %}</select></div>
        <div class="field"><label>Double</label><select name="double_annotated">{% for s in ['no','yes'] %}<option {{'selected' if gl.double_annotated==s}}>{{s}}</option>{% endfor %}</select></div>
        <div class="field"><label>IAA</label><select name="iaa_reviewed">{% for s in ['no','yes'] %}<option {{'selected' if gl.iaa_reviewed==s}}>{{s}}</option>{% endfor %}</select></div>
      </div>
      <div class="field"><label>Ghi chú</label><input type="text" name="notes" value="{{gl.notes}}" style="width:100%"></div>
      <button class="btn" type="submit">Lưu annotation</button>
    </form>
  </div>
</div>
{% endblock %}"""

env = Environment(loader=DictLoader({"base": BASE, "login": LOGIN, "index": INDEX, "clip": CLIP}), autoescape=True)

def gstats():
    total = len(ORDER)
    vid = sum(1 for r in ORDER if has_video(r))
    ann = sum(1 for r in ORDER if ann_done(r))
    return {"total": total, "vid": vid, "ann": ann,
            "vidpct": round(vid / total * 100) if total else 0,
            "annpct": round(ann / total * 100) if total else 0}

app = FastAPI()

def gate(request):
    """Return None if allowed to proceed, else a redirect to /login."""
    return None if logged_in(request) else RedirectResponse("/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if logged_in(request):
        return RedirectResponse("/", status_code=303)
    return env.get_template("login").render(header=False, authed=False, error=request.query_params.get("e"))

@app.post("/login")
def login_post(username: str = Form(""), password: str = Form(...)):
    if not check_credentials(username.strip(), password):
        return RedirectResponse("/login?e=Sai+tên+đăng+nhập+hoặc+mật+khẩu", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("session", make_token(), httponly=True, samesite="lax",
                    secure=SECURE_COOKIE, max_age=SESSION_DAYS * 86400, path="/")
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session", path="/")
    return resp

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    g = gate(request)
    if g: return g
    qp = request.query_params
    f = {k: qp.get(k, "") for k in ("q", "split", "signer", "fmt", "vid", "ann")}
    page = max(1, int(qp.get("page", "1") or 1))
    ql = f["q"].lower()
    rows = []
    for rid in ORDER:
        c = CLIPS[rid]
        if f["split"] and c["split"] != f["split"]: continue
        if f["signer"] and c["signer_id"] != f["signer"]: continue
        if f["fmt"] and c["text_format"] != f["fmt"]: continue
        hv = has_video(rid)
        if f["vid"] == "yes" and not hv: continue
        if f["vid"] == "no" and hv: continue
        ad = ann_done(rid)
        if f["ann"] == "done" and not ad: continue
        if f["ann"] == "pending" and ad: continue
        if ql and ql not in rid.lower() and ql not in c["segment_id"].lower() and ql not in c["segment_text"].lower():
            continue
        rows.append({**c, "_vid": hv, "_ann": ad})
    total = len(rows)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    qs = "&".join(f"{k}={v}" for k, v in f.items() if v)
    return env.get_template("index").render(
        header=True, authed=auth_required(), rows=rows, total=total, page=page, pages=pages, qs=qs, f=f,
        signers=sorted({CLIPS[r]["signer_id"] for r in ORDER}), splits=["train", "dev", "sd_test", "si_test"],
        fmts=sorted({CLIPS[r]["text_format"] for r in ORDER}), g=gstats(), flash=qp.get("msg"))

@app.get("/clip/{rid}", response_class=HTMLResponse)
def clip(rid: str, request: Request):
    g = gate(request)
    if g: return g
    if rid not in CLIPS:
        return HTMLResponse("Unknown recording_id", status_code=404)
    c = CLIPS[rid]
    i = ORDER.index(rid)
    turns = [t.strip() for t in c["segment_text"].split("|") if t.strip()]
    return env.get_template("clip").render(
        header=True, authed=auth_required(), c=c, cap=CAP.get(rid, {}), gl=GLOSS.get(rid, {}), turns=turns,
        vid_url=(f"/video/{rid}" if video_file(rid) else None),
        prev=(ORDER[i - 1] if i > 0 else None), nxt=(ORDER[i + 1] if i < len(ORDER) - 1 else None),
        g=gstats(), flash=request.query_params.get("msg"))

@app.get("/video/{rid}")
def serve_video(rid: str, request: Request):
    if not logged_in(request):
        return PlainTextResponse("login required", status_code=401)
    vf = video_file(rid)
    return FileResponse(str(vf)) if vf else PlainTextResponse("no video", status_code=404)

@app.post("/clip/{rid}/video")
async def upload_video(rid: str, request: Request, file: UploadFile = File(...),
                       take_number: str = Form(""), notes: str = Form(""), q_ok: str = Form(None)):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if rid not in CLIPS:
        return PlainTextResponse("unknown id", status_code=404)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in VIDEO_EXTS:
        return RedirectResponse(f"/clip/{rid}?msg=Định+dạng+video+không+hợp+lệ", status_code=303)
    VIDEO_DIR.mkdir(exist_ok=True)
    for old in VIDEO_DIR.glob(f"{rid}.*"):
        old.unlink()
    dest = VIDEO_DIR / f"{rid}{ext}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    with LOCK:
        row = CAP.setdefault(rid, {k: "" for k in CAP_FIELDS})
        row.update({"recording_id": rid, "segment_id": CLIPS[rid]["segment_id"], "signer_id": CLIPS[rid]["signer_id"],
                    "split": CLIPS[rid]["split"], "video_path": f"videos/{dest.name}", "status": "recorded",
                    "take_number": take_number, "notes": notes, "recording_date": datetime.date.today().isoformat()})
        yn = "y" if q_ok else ""
        for k in ("q_motion_blur_ok", "q_framing_full_signing_space", "q_fps_ge_25", "q_lighting_ok", "q_resolution_ge_1080p"):
            row[k] = yn
        save_csv(CAP_CSV, CAP_FIELDS, CAP)
    return RedirectResponse(f"/clip/{rid}?msg=Đã+tải+video", status_code=303)

@app.post("/clip/{rid}/annotation")
async def save_annotation(rid: str, request: Request, gold_gloss_sequence: str = Form(""),
                          vietnamese_translation: str = Form(""), fingerspelled_items_FS: str = Form(""),
                          classifier_or_NM_notes: str = Form(""), annotator_id: str = Form(""),
                          annotation_status: str = Form("pending"), double_annotated: str = Form("no"),
                          iaa_reviewed: str = Form("no"), notes: str = Form("")):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if rid not in CLIPS:
        return PlainTextResponse("unknown id", status_code=404)
    with LOCK:
        row = GLOSS.setdefault(rid, {k: "" for k in GLOSS_FIELDS})
        row.update({"recording_id": rid, "segment_id": CLIPS[rid]["segment_id"], "split": CLIPS[rid]["split"],
                    "signer_id": CLIPS[rid]["signer_id"], "text_format": CLIPS[rid]["text_format"],
                    "segment_text": CLIPS[rid]["segment_text"], "gold_gloss_sequence": gold_gloss_sequence.strip(),
                    "vietnamese_translation": vietnamese_translation.strip(),
                    "fingerspelled_items_FS": fingerspelled_items_FS.strip(),
                    "classifier_or_NM_notes": classifier_or_NM_notes.strip(), "annotator_id": annotator_id.strip(),
                    "annotation_status": annotation_status, "double_annotated": double_annotated,
                    "iaa_reviewed": iaa_reviewed, "notes": notes.strip()})
        save_csv(GLOSS_CSV, GLOSS_FIELDS, GLOSS)
    return RedirectResponse(f"/clip/{rid}?msg=Đã+lưu+annotation", status_code=303)

# ------------------------------------------------------------------ CLI
def cmd_setpassword():
    user = input("Tên đăng nhập [admin]: ").strip() or "admin"
    pw = getpass.getpass("Đặt mật khẩu: ")
    if not pw or pw != getpass.getpass("Nhập lại: "):
        sys.exit("mật khẩu rỗng hoặc không khớp")
    AUTH_DIR.mkdir(exist_ok=True)
    salt = secrets.token_hex(16)
    PW_FILE.write_text(json.dumps({"user": user, "salt": salt, "hash": _hash(pw, salt)}))
    os.chmod(PW_FILE, 0o600)
    print(f"Đã lưu tài khoản '{user}' ({PW_FILE}). Khởi động lại server để áp dụng.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setpassword")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if a.cmd == "setpassword":
        cmd_setpassword()
    else:
        for pth in (SCRIPT_CSV, CAP_CSV, GLOSS_CSV):
            if not pth.exists():
                sys.exit(f"Không thấy {pth}. Đặt ANNOTATION_DATA_DIR hoặc chạy build_recording_script.py.")
        load_all(); get_secret()
        mode = "CÓ mật khẩu" if auth_required() else "MỞ (không mật khẩu — chỉ nên dùng localhost)"
        print(f"VSL annotation: http://{a.host}:{a.port}  ({len(ORDER)} clips, data={REC_DIR}) · {mode}")
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
