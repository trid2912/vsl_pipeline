# VSL Annotation Web App

A small, self-contained **FastAPI** web app to collect a continuous Vietnamese Sign Language (VSL)
dataset: signers **upload recording videos** and annotators **fill gloss annotations**, in the browser,
against a fixed recording script. One shared login; data stored as CSV + video files (no database).

- **Clip list** comes from `final_recording_script.csv` (read-only) — id, split, signer, the text to sign, and the signing instruction.
- **Video uploads** are written to `videos/<recording_id>.<ext>` and logged in `capture_log.csv`.
- **Gloss annotations** (gold gloss sequence, Vietnamese translation, `FS:` items, status) are written to `gloss_annotation_sheet.csv`.

## Quickstart

```bash
pip install -r requirements.txt

# Demo with the bundled sample dataset (7 clips), no password (open — localhost only):
python3 annotation_server.py                 # http://127.0.0.1:8000

# With your own data + a shared login:
export ANNOTATION_DATA_DIR=/path/to/recording
python3 annotation_server.py setpassword     # sets username + password (stored hashed in auth/)
python3 annotation_server.py --host 0.0.0.0 --port 8000
```

If `ANNOTATION_DATA_DIR` is unset, the app auto-detects `<repo>/final_corpus/recording`; otherwise it uses the
**bundled full recording package** at `data/recording/` (1,540 clips), and finally the tiny `sample_data/recording/`
demo — so a fresh clone runs on the real dataset immediately. Uploaded videos are written next to those CSVs
under `videos/` and are **not** committed.

## Login (single shared account)

Everyone uses the same credentials; anyone logged in has full access (view all clips, upload, annotate).

**There is no hardcoded username/password — you set your own, and it is deliberately NOT stored in this repo.**
The password lives only in `auth/` (gitignored), so a public repo never leaks it. On first run, set it one of two ways:

```bash
# option A — CLI (prompts for username [default: admin] + password; hashed into auth/password.txt)
python3 annotation_server.py setpassword

# option B — environment variables
export ANNOTATION_USER=admin
export ANNOTATION_PASSWORD='choose-a-strong-password'
```

Then open the app and log in with what you set. If **neither** is configured the app runs **open (no login)** —
acceptable only on localhost. Rotate the password by re-running `setpassword` (or changing the env) and restarting.

## The recording package (data directory)

Produced by the dataset's `build_recording_script.py`. Three CSVs + a `videos/` folder:

| File | Role |
|---|---|
| `final_recording_script.csv` | read-only clip list (id, split, signer_id, genre, `text_format`, `segment_text`, `signer_instruction`, …) |
| `capture_log.csv` | written on video upload (video_path, status, date, quality flags) |
| `gloss_annotation_sheet.csv` | written on annotation (gold gloss, translation, `FS:`, status) |
| `videos/` | uploaded `<recording_id>.<ext>` files |

`segment_text` uses `text_format`: `dialog_turns` (turns split by `|`), `continuous` (one passage), or `eval_sentence`.

## Configuration (env vars)

| Var | Meaning |
|---|---|
| `ANNOTATION_DATA_DIR` | path to the recording package (see above) |
| `ANNOTATION_USER` / `ANNOTATION_PASSWORD` | shared login (alternative to `auth/password.txt`) |
| `ANNOTATION_SECRET` | session-signing key; set a fixed value so logins survive restarts |
| `ANNOTATION_SECURE` | `1` when served over HTTPS (marks the session cookie Secure) |

See `.env.example`. **Deployment (internet-facing, HTTPS, systemd/Nginx/tunnels): see [DEPLOY.md](DEPLOY.md).**

## Security notes

- `auth/` (password hash + session secret) and uploaded `videos/` are **gitignored** — never commit them.
- Always serve over **HTTPS** when exposed to the internet (login is sent on submit) and set `ANNOTATION_SECURE=1`.
- Don't run the dataset's `build_recording_script.py` against a live data dir — it resets `capture_log.csv` and `gloss_annotation_sheet.csv` to blank.

## Requirements

Python 3.10+, and the packages in `requirements.txt` (FastAPI, uvicorn, Jinja2, python-multipart). No database.
