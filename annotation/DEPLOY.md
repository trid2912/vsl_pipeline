# Deploying the VSL annotation web app

A FastAPI app with **one shared password** — everyone who logs in has full access (see all clips,
upload videos, fill gloss annotations). No per-user accounts. No database — it reads/writes CSVs in a
**data directory** and keeps the shared password (hashed) in `auth/`.

---

## 1. What to copy to the server

- `vsl_pipeline/annotation/`  (the app: `annotation_server.py`; `auth/` is created on first run)
- A **data directory** = the recording package produced by `build_recording_script.py`:
  `final_recording_script.csv`, `capture_log.csv`, `gloss_annotation_sheet.csv`, and a `videos/` folder.

Point the app at the data dir with an env var (recommended — no need to copy the whole repo):

```bash
export ANNOTATION_DATA_DIR=/srv/vsl/recording      # holds the 3 CSVs + videos/
```

If unset, the app looks for `<nearest ancestor with final_corpus/>/final_corpus/recording`.

## 2. Install (Python 3.10+)

```bash
python3 -m venv venv && . venv/bin/activate
pip install fastapi "uvicorn[standard]" jinja2 python-multipart
```

## 3. Set the shared password

```bash
cd vsl_pipeline/annotation
python3 annotation_server.py setpassword          # prompts; stores hashed in auth/password.txt (chmod 600)
# — or — set it via env instead of a file:
export ANNOTATION_PASSWORD='choose-a-strong-one'
```
Hashed with PBKDF2-SHA256 (200k iters). If **neither** is set, the app runs **open (no login)** — only do that on localhost.
Share the one password with all signers/annotators. To rotate it, run `setpassword` again (or change the env) and restart.

## 4. Run

```bash
export ANNOTATION_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"   # stable across restarts
export ANNOTATION_SECURE=1        # set ONLY when served over HTTPS (marks cookies Secure)
python3 annotation_server.py --host 0.0.0.0 --port 8000
```
`ANNOTATION_SECRET` keeps sessions valid across restarts (otherwise a random per-run key in `auth/secret.key` is used).

---

## 5. Expose to the internet — pick one

### A) Quickest: Cloudflare Tunnel (no server/DNS setup, free HTTPS URL)
Great for sharing a link with signers immediately.
```bash
# install cloudflared, then:
cloudflared tunnel --url http://localhost:8000
# prints a public https://<random>.trycloudflare.com URL -> send it to signers
```
For a stable custom domain, use a **named tunnel** (`cloudflared tunnel create`, map a DNS route). `ngrok http 8000` works the same way.

### B) Proper: your own server, Nginx + HTTPS + systemd
**systemd** service `/etc/systemd/system/vsl-annot.service`:
```ini
[Service]
WorkingDirectory=/srv/vsl/vsl_pipeline/annotation
Environment=ANNOTATION_DATA_DIR=/srv/vsl/recording
Environment=ANNOTATION_SECRET=<64-hex-chars>
Environment=ANNOTATION_SECURE=1
ExecStart=/srv/vsl/venv/bin/python annotation_server.py --host 127.0.0.1 --port 8000
Restart=always
User=vsl
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now vsl-annot
```
**Nginx** reverse proxy (then `sudo certbot --nginx -d annot.example.com` for HTTPS):
```nginx
server {
  server_name annot.example.com;
  client_max_body_size 500M;          # allow large video uploads
  location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host;
               proxy_set_header X-Forwarded-Proto $scheme; }
}
```

---

## 6. Security & ops checklist
- **Always use HTTPS** for internet access (passwords are sent on login) → tunnel gives it automatically; for Nginx use certbot, and set `ANNOTATION_SECURE=1`.
- **Set a strong shared password** (`setpassword` or `ANNOTATION_PASSWORD`) before exposing publicly — the demo password `vsl2026` must be changed.
- Set a fixed `ANNOTATION_SECRET` in production; keep `auth/` private (not world-readable, not in any public repo).
- **Back up** regularly: `capture_log.csv`, `gloss_annotation_sheet.csv`, `auth/password.txt`, and the `videos/` folder (videos can be large — provision disk).
- ⚠️ Do **not** run `build_recording_script.py` against a live data dir — it **resets** `capture_log.csv` and `gloss_annotation_sheet.csv` to blank. Build the recording package first, then deploy.
