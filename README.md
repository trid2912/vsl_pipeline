# VSL Pipeline

Tooling for the continuous Vietnamese Sign Language dataset, split into two independent parts.
Both find the repo root automatically (nearest ancestor containing `final_corpus/`), so they can be
run from anywhere; the data itself stays in the repo (`final_corpus/`, `corpus_files/`, `data/processed/`, `feedback/`).

```
vsl_pipeline/
├── generation/     data-synthesis + corpus-build tools
└── annotation/     the web/API for uploading videos & gloss annotations
```

## `generation/` — synthesize & prepare data

| Script | Does |
|---|---|
| `gen_segment_llm.py` | LLM segment generation (interview/conversation/storytelling/…); `--turn-mode multi` for complex multi-sentence-turn dialogs |
| `qc_segments.py` | 8-layer QC (length, red/yellow safety, OOV, near-dup/leakage) — imports `gen_segment_llm` |
| `diversify_names.py` / `diversify_pronouns.py` | name / pronoun diversification passes over generated segments |
| `build_recording_script.py` | build the recording package from the corpus → `final_corpus/recording/` (script, capture log, gloss sheet, split ledger, signer manifest, summary) |
| `plot_segment_distributions.py` | topic + segment-length distribution charts |

```bash
python3 vsl_pipeline/generation/build_recording_script.py            # (re)build the recording package
python3 vsl_pipeline/generation/gen_segment_llm.py --provider ollama --model gemma4:31b \
        --turn-mode multi --per-genre 2,2,0,0,0 --out final_corpus/gen_by_category/round5_complex --prefix r5cx
python3 vsl_pipeline/generation/plot_segment_distributions.py
```

## `annotation/` — web app to upload videos & annotations

Consumes what `generation/build_recording_script.py` produced. Reads `final_recording_script.csv`
(read-only clip list); writes `capture_log.csv` (video) and `gloss_annotation_sheet.csv` (gold gloss).

```bash
python3 vsl_pipeline/annotation/annotation_server.py               # http://127.0.0.1:8000
python3 vsl_pipeline/annotation/annotation_server.py --host 0.0.0.0 --port 8000   # LAN
```

## Order of operations
1. `generation/` — generate segments, QC, then `build_recording_script.py` to produce the recording package.
2. Record + annotate through `annotation/` (the web app).

⚠️ Re-running `build_recording_script.py` **resets** `capture_log.csv` + `gloss_annotation_sheet.csv` to blank —
run all generation/build steps **before** annotation work begins, not after.
