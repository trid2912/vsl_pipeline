#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_recording_script.py — Export the VSL recording sheet/script package.

Fulfils the two critical flaws flagged in papers/VSL-Pipeline-Assessment.md:
  * flaw #2 (no signer assignment / no train-dev-test split)  -> this script
  * flaw #1 (no video-grounded gold gloss)                    -> gloss_annotation_sheet.csv (blank, to fill after recording)

Design (locked with the user):
  * Content   : ~846 standard/hard segments (to record) + 347 FLEURS transcripts (eval).
  * Split     : 8/2 dual-split. SIGNER_01..08 = training pool (train/dev/sd_test).
                SIGNER_09, SIGNER_10 = held-out signer-independent (SI) test; they
                record ONLY the FLEURS content, which never appears in training.
  * Glosses   : left blank for the post-recording, video-grounded annotation pass.
  * Clips     : segment-level only (one row = one video to record).

All partitioning is deterministic (hashlib), so re-running is reproducible.
Inputs are read-only; nothing upstream is modified.
"""
import csv, glob, os, hashlib, re, statistics
from collections import Counter, defaultdict

# repo root = nearest ancestor containing final_corpus (works wherever this script lives)
ROOT = os.path.abspath(__file__)
while ROOT != os.path.dirname(ROOT) and not os.path.isdir(os.path.join(ROOT, "final_corpus")):
    ROOT = os.path.dirname(ROOT)
REVIEW_DIR = f"{ROOT}/final_corpus/gen_by_category/tong_hop_review"
FINAL_CSV = f"{ROOT}/final_corpus/balanced_corpus_final.csv"
OUT_DIR = f"{ROOT}/final_corpus/recording"
os.makedirs(OUT_DIR, exist_ok=True)

TRAIN_SIGNERS = [f"SIGNER_{i:02d}" for i in range(1, 9)]   # 8 training-pool signers
SI_SIGNERS = ["SIGNER_09", "SIGNER_10"]                    # 2 held-out SI-test signers
DEV_RATIO, SD_RATIO = 0.07, 0.08                           # ~85/7/8 content split (Phoenix/CSL-Daily-like)
SECONDS_PER_WORD = 0.75                                    # signing-load estimate used throughout the project

# Interview segments: '|' marks hỏi–đáp turn changes -> pause lightly at each '|'.
# INTERVIEW (dialog): '|' = hỏi–đáp turns -> pause lightly at each '|'.
SIGNER_INSTR_INTERVIEW = ("Ký tự nhiên theo nội dung đoạn. Đây là dạng phỏng vấn: tạm dừng nhẹ ở mỗi dấu '|' "
                          "để phân tách lượt hỏi–đáp. Giữ hai tay, khuôn mặt và thân trên trong khung hình, "
                          "ánh mắt hướng camera. Với tên riêng chưa có ký hiệu quy ước -> đánh vần tay (FS:).")
# Other dialog (conversation/classroom): '|' marks the CHANGE OF SPEAKER (for annotation), sign naturally, no hard pause.
SIGNER_INSTR_DIALOG = ("Ký tự nhiên theo nội dung đoạn hội thoại. Dấu '|' đánh dấu ĐỔI LƯỢT người nói (dùng để gán nhãn) — "
                       "ký liền mạch, không cần dừng hẳn ở '|'. Giữ hai tay, khuôn mặt và thân trên trong khung hình, "
                       "ánh mắt hướng camera. Với tên riêng chưa có ký hiệu quy ước -> đánh vần tay (FS:).")
# Continuous (storytelling/instruction/narrative): no '|', sign as one continuous passage.
SIGNER_INSTR_CONT = ("Ký tự nhiên, LIỀN MẠCH cả đoạn (văn bản không có dấu '|'). Kể/hướng dẫn trôi chảy từ đầu đến cuối. "
                     "Giữ hai tay, khuôn mặt và thân trên trong khung hình, ánh mắt hướng camera. "
                     "Với tên riêng chưa có ký hiệu quy ước -> đánh vần tay (FS:).")
SIGNER_INSTR_EVAL = ("Đây là 1 câu đơn (bộ đánh giá). Ký tự nhiên, trọn câu, không thêm bớt nội dung. "
                     "Giữ tay/mặt/thân trên trong khung hình. Tên riêng chưa có ký hiệu -> đánh vần tay (FS:).")
NE_HANDLING = ("Tên riêng không có ký hiệu VSL -> đánh vần tay (FS:); "
               "nếu có ký hiệu địa phương quy ước thì dùng ký hiệu đó (ghi lại vào phiếu gán gloss).")

# genre -> text format. '|' kept for dialog (turn/speaker boundary), removed for narrative (continuous).
DIALOG_GENRES = {"interview", "conversation", "classroom_dialogue"}
NARRATIVE_GENRES = {"storytelling", "instruction_procedure"}

def is_dialog(genre, source, text):
    """Dialog -> keep '|'. Narrative/monologue -> continuous (remove '|')."""
    g = (genre or "").strip()
    if g in DIALOG_GENRES:
        return True
    if g in NARRATIVE_GENRES:
        return False
    # empty genre = legacy r1_cu: 'generated' are conversations, 'genhistory.csv' are narratives
    if source == "genhistory.csv":
        return False
    if source == "generated":
        return True
    # fallback for any other unlabeled source: dialog if any turn is a question
    return any(u.strip().endswith("?") for u in text.split("|") if u.strip())

def to_continuous(text):
    """Join '|'-separated sentences into one continuous passage."""
    return re.sub(r"\s+", " ", text.replace("|", " ")).strip()

# --- dialog turn re-segmentation: legacy '|' = sentence -> '|' = turn (speaker change) ---
_FIRST_PERSON = re.compile(r"\b(tôi|mình|tớ|chúng tôi|chúng mình|chúng tớ)\b", re.I)

def _speaker_sig(unit):
    """Speaker signature used to decide if two consecutive sentences are the same speaker.
    First-person self-reference -> 'FP'; a leading multi-syllable proper name (e.g. 'Hiệp Hòa')
    -> 'N:<name>'. A single leading capitalized word (usually just sentence-initial) -> None (won't merge)."""
    if _FIRST_PERSON.search(unit):
        return "FP"
    caps = []
    for t in unit.split():
        w = t.strip(".,!?;:\"'()")
        if w[:1].isupper() and w[:1].isalpha():
            caps.append(w)
        else:
            break
    return "N:" + " ".join(caps) if len(caps) >= 2 else None

def _is_clean_qa(units):
    """Strict interview alternation: starts with a question, single-question asks alternating
    with short answer runs (<=3 sentences). In that structure every declarative after a question
    is the one answerer, so the whole answer run is safely one turn."""
    types = ["Q" if u.endswith("?") else "S" for u in units]
    if types.count("Q") < 2 or types[0] != "Q":
        return False
    runs = []
    for t in types:
        if runs and runs[-1][0] == t:
            runs[-1][1] += 1
        else:
            runs.append([t, 1])
    for i, (t, n) in enumerate(runs):
        if t != ("Q" if i % 2 == 0 else "S"):
            return False
        if t == "Q" and n != 1:
            return False
        if t == "S" and n > 3:
            return False
    return True

def merge_dialog_turns(text):
    """Re-segment '|' from per-sentence to per-turn (speaker change).
    Clean Q&A interviews: merge each full answer run. Otherwise: conservative — merge only
    consecutive declaratives with a matching speaker signature (never merges across a speaker change)."""
    units = [x.strip() for x in text.split("|") if x.strip()]
    turns = []
    if _is_clean_qa(units):
        for u in units:
            isq = u.endswith("?")
            if turns and not isq and not turns[-1]["q"]:      # continue the answer run
                turns[-1]["text"] += " " + u
            else:
                turns.append({"text": u, "q": isq})
    else:
        for u in units:
            isq = u.endswith("?")
            sig = _speaker_sig(u)
            if turns and not isq and not turns[-1]["q"] and sig is not None and sig == turns[-1].get("sig"):
                turns[-1]["text"] += " " + u
            else:
                turns.append({"text": u, "q": isq, "sig": sig})
    return "|".join(t["text"] for t in turns)

def _count_sentences(text):
    return len([s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()])

def h(s):  # stable int hash
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest(), 16)

def utts(text):
    return [u.strip() for u in text.split("|") if u.strip()]

def tokset(text):
    return set(re.findall(r"\w+", text.lower()))

# ---------------------------------------------------------------- load segments
seg_rows = []
for fn in sorted(glob.glob(f"{REVIEW_DIR}/*.csv")):
    if os.path.basename(fn).startswith("00_"):
        continue
    with open(fn, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            text = r["sentences/paragraph"]
            u = utts(text)
            try:
                w = int(r.get("so_tu") or 0)
            except ValueError:
                w = 0
            if not w:
                w = len(text.replace("|", " ").split())
            if w >= 100 and len(u) >= 8:
                bucket = "hard"
            elif w >= 70 and len(u) >= 6:
                bucket = "standard"
            else:
                continue  # below length policy -> not part of the recording set
            sid = (r.get("dialogue_id") or "").strip() or f"stt{r.get('stt_goc') or r.get('stt')}"
            seg_rows.append({
                "segment_id": sid, "text": text, "category": r["category"],
                "genre": (r.get("genre") or "").strip(), "emotion": (r.get("emotion") or "").strip(),
                "source": (r.get("source") or "").strip(), "round": (r.get("round") or "").strip(),
                "words": w, "utt": len(u), "bucket": bucket,
                "qc_flags": (r.get("qc_flags") or "").strip(),
            })

# ---------------------------------------------------------------- load FLEURS eval
eval_rows = []
with open(FINAL_CSV, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r["source"] != "transcriptions.csv":
            continue
        text = r["sentences/paragraph"]
        eval_rows.append({
            "segment_id": f"FLEURS_{int(r['stt']):05d}", "text": text, "category": r["category"],
            "genre": "eval_sentence", "emotion": "", "source": "transcriptions.csv", "round": "fleurs",
            "words": len(text.split()), "utt": 1, "bucket": "eval_sentence", "qc_flags": "",
        })

# ---------------------------------------------------------------- near-dup clusters (per category, Jaccard>=0.6)
def cluster(rows):
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    cid = 0
    for cat, items in by_cat.items():
        toks = [tokset(x["text"]) for x in items]
        parent = list(range(len(items)))
        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]; a = parent[a]
            return a
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                inter = len(toks[i] & toks[j])
                if not inter:
                    continue
                jac = inter / len(toks[i] | toks[j])
                if jac >= 0.6:
                    parent[find(i)] = find(j)
        roots = {}
        for i, it in enumerate(items):
            root = find(i)
            if root not in roots:
                cid += 1; roots[root] = f"C{cid:04d}"
            it["near_dup_cluster"] = roots[root]

cluster(seg_rows)

# ---------------------------------------------------------------- content-disjoint split (assign whole clusters)
clusters = defaultdict(list)
for r in seg_rows:
    clusters[(r["category"], r["near_dup_cluster"])].append(r)

# per category, order clusters deterministically and fill dev then sd_test then train
by_cat_clusters = defaultdict(list)
for (cat, cl), members in clusters.items():
    by_cat_clusters[cat].append((cl, members))

for cat, cls in by_cat_clusters.items():
    cls.sort(key=lambda cm: h(cat + cm[0]))           # deterministic shuffle
    n = sum(len(m) for _, m in cls)
    dev_target, sd_target = round(n * DEV_RATIO), round(n * SD_RATIO)
    dev_n = sd_n = 0
    for cl, members in cls:
        if dev_n < dev_target:
            split = "dev"; dev_n += len(members)
        elif sd_n < sd_target:
            split = "sd_test"; sd_n += len(members)
        else:
            split = "train"
        for m in members:
            m["split"] = split

for r in eval_rows:
    r["split"] = "si_test"
    r["near_dup_cluster"] = ""

# ---------------------------------------------------------------- signer assignment
# training pool: balance clip COUNT across the 8, deterministic order
train_pool = sorted(seg_rows, key=lambda r: h(r["segment_id"]))
load = Counter()
for r in train_pool:
    sgn = min(TRAIN_SIGNERS, key=lambda s: (load[s], s))
    r["signers"] = [sgn]; load[sgn] += 1
# SI eval: both held-out signers record every eval sentence (paired, per-signer disaggregation)
for r in eval_rows:
    r["signers"] = list(SI_SIGNERS)

# ---------------------------------------------------------------- build clip rows
def safety(qc):
    q = qc.lower()
    if "red" in q:
        return "red_block"
    if "yellow" in q:
        return "yellow_pending_review"
    return "green"

def review_status(qc):
    return "qc_pass_pending_human_vsl_review" if qc else "qc_pass_pending_human_vsl_review"

def license_class(source):
    return "flores_derived_CC-BY-SA-4.0" if source == "transcriptions.csv" else "synthetic_CC-BY-4.0"

clips = []
n = 0
for r in sorted(seg_rows, key=lambda x: (x["split"] != "train", x["category"], x["segment_id"])) + \
         sorted(eval_rows, key=lambda x: x["segment_id"]):
    est = round(r["words"] * SECONDS_PER_WORD)
    # classify dialog vs continuous, transform text + pick instruction accordingly
    if r["bucket"] == "eval_sentence":
        text_format, disp_text, instr, ucount = "eval_sentence", r["text"], SIGNER_INSTR_EVAL, 1
    elif is_dialog(r["genre"], r["source"], r["text"]):
        disp_text = merge_dialog_turns(r["text"])  # '|' = turn (speaker change), not sentence
        ucount = len([u for u in disp_text.split("|") if u.strip()])
        text_format = "dialog_turns"
        instr = SIGNER_INSTR_INTERVIEW if r["genre"] == "interview" else SIGNER_INSTR_DIALOG
    else:
        text_format, disp_text, instr = "continuous", to_continuous(r["text"]), SIGNER_INSTR_CONT
        ucount = _count_sentences(disp_text)
    for sgn in r["signers"]:
        n += 1
        prefix = "EVAL" if r["split"] == "si_test" else "SEG"
        clips.append({
            "recording_id": f"VSL_{prefix}_{n:06d}",
            "segment_id": r["segment_id"], "split": r["split"], "signer_id": sgn,
            "domain": r["category"], "genre": r["genre"], "emotion": r["emotion"],
            "length_bucket": r["bucket"], "text_format": text_format,
            "utterance_count": ucount,
            "segmented_word_count": r["words"], "estimated_duration_seconds": est,
            "segment_text": disp_text,
            "named_entities": "", "named_entity_handling": NE_HANDLING,
            "safety_level": safety(r["qc_flags"]), "qc_flags": r["qc_flags"],
            "near_dup_cluster": r.get("near_dup_cluster", ""),
            "review_status": review_status(r["qc_flags"]),
            "source": r["source"], "source_ids": r["segment_id"],
            "license_class": license_class(r["source"]),
            "signer_instruction": instr,
        })

# ---------------------------------------------------------------- writers
def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

REC_FIELDS = ["recording_id", "segment_id", "split", "signer_id", "domain", "genre", "emotion",
              "length_bucket", "text_format", "utterance_count", "segmented_word_count",
              "estimated_duration_seconds", "segment_text", "named_entities", "named_entity_handling",
              "safety_level", "qc_flags", "near_dup_cluster", "review_status", "source", "source_ids",
              "license_class", "signer_instruction"]
write_csv(f"{OUT_DIR}/final_recording_script.csv", clips, REC_FIELDS)

# companion: gloss annotation sheet (blank gold-gloss fields to fill against video)
gloss_rows = [{
    "recording_id": c["recording_id"], "segment_id": c["segment_id"], "split": c["split"],
    "signer_id": c["signer_id"], "text_format": c["text_format"], "segment_text": c["segment_text"],
    "gold_gloss_sequence": "", "vietnamese_translation": "", "fingerspelled_items_FS": "",
    "classifier_or_NM_notes": "", "annotator_id": "", "double_annotated": "no",
    "iaa_reviewed": "no", "annotation_status": "pending", "notes": "",
} for c in clips]
write_csv(f"{OUT_DIR}/gloss_annotation_sheet.csv", gloss_rows,
          ["recording_id", "segment_id", "split", "signer_id", "text_format", "segment_text",
           "gold_gloss_sequence", "vietnamese_translation", "fingerspelled_items_FS",
           "classifier_or_NM_notes", "annotator_id", "double_annotated", "iaa_reviewed",
           "annotation_status", "notes"])

# companion: per-clip capture log
cap_rows = [{
    "recording_id": c["recording_id"], "segment_id": c["segment_id"], "signer_id": c["signer_id"],
    "split": c["split"], "length_bucket": c["length_bucket"],
    "estimated_duration_seconds": c["estimated_duration_seconds"],
    "session_id": "", "recording_date": "", "take_number": "", "status": "pending",
    "actual_duration_seconds": "", "q_motion_blur_ok": "", "q_framing_full_signing_space": "",
    "q_fps_ge_25": "", "q_lighting_ok": "", "q_resolution_ge_1080p": "",
    "video_path": "", "notes": "",
} for c in clips]
write_csv(f"{OUT_DIR}/capture_log.csv", cap_rows,
          ["recording_id", "segment_id", "signer_id", "split", "length_bucket",
           "estimated_duration_seconds", "session_id", "recording_date", "take_number", "status",
           "actual_duration_seconds", "q_motion_blur_ok", "q_framing_full_signing_space", "q_fps_ge_25",
           "q_lighting_ok", "q_resolution_ge_1080p", "video_path", "notes"])

# content-disjointness ledger (one row per unique content segment)
ledger = []
for r in sorted(seg_rows, key=lambda x: (x["category"], x["segment_id"])) + eval_rows:
    ledger.append({
        "segment_id": r["segment_id"], "split": r["split"], "domain": r["category"],
        "length_bucket": r["bucket"], "segmented_word_count": r["words"],
        "near_dup_cluster": r.get("near_dup_cluster", ""),
        "signer_ids": "|".join(r["signers"]), "source": r["source"],
    })
write_csv(f"{OUT_DIR}/split_ledger.csv", ledger,
          ["segment_id", "split", "domain", "length_bucket", "segmented_word_count",
           "near_dup_cluster", "signer_ids", "source"])

# signer manifest
sm = []
for s in TRAIN_SIGNERS + SI_SIGNERS:
    scl = [c for c in clips if c["signer_id"] == s]
    mins = round(sum(c["estimated_duration_seconds"] for c in scl) / 60, 1)
    sm.append({
        "signer_id": s, "role": "training_pool" if s in TRAIN_SIGNERS else "si_test_heldout",
        "n_clips": len(scl), "est_signing_minutes": mins, "est_signing_hours": round(mins / 60, 2),
        "gender": "", "age_range": "", "region_dialect": "", "handedness": "",
        "vsl_proficiency": "", "consent_signed": "", "notes": "",
    })
write_csv(f"{OUT_DIR}/signer_manifest.csv", sm,
          ["signer_id", "role", "n_clips", "est_signing_minutes", "est_signing_hours", "gender",
           "age_range", "region_dialect", "handedness", "vsl_proficiency", "consent_signed", "notes"])

# ---------------------------------------------------------------- summary
def dist(rows, key):
    return Counter(r[key] for r in rows)

seg_clips = [c for c in clips if c["split"] != "si_test"]
eval_clips = [c for c in clips if c["split"] == "si_test"]
total_sec = sum(c["estimated_duration_seconds"] for c in clips)
by_split = dist(clips, "split")
uniq_seg = len(seg_rows)

lines = []
lines.append("# VSL Recording Script — Summary\n")
lines.append(f"Generated by `tools/build_recording_script.py`. Deterministic; re-runnable.\n")
lines.append("## Totals\n")
lines.append(f"- **Clips to record: {len(clips)}** ({len(seg_clips)} segment clips + {len(eval_clips)} eval clips)")
lines.append(f"- Unique content segments: {uniq_seg} (standard/hard) + {len(eval_rows)} FLEURS eval sentences")
lines.append(f"- Estimated signing time: **{total_sec/3600:.1f} h** (@ {SECONDS_PER_WORD}s/word)")
lines.append(f"- Utterances in segments: {sum(r['utt'] for r in seg_rows)}\n")
lines.append("## Split distribution (clips)\n")
lines.append("| Split | Clips | Unique segments | Role |")
lines.append("|---|---:|---:|---|")
for sp, role in [("train", "8 training signers"), ("dev", "held-out content, 8 signers"),
                 ("sd_test", "unseen content, 8 signers (signer-dependent)"),
                 ("si_test", "FLEURS, 2 held-out signers (signer-independent)")]:
    uq = len(set(c["segment_id"] for c in clips if c["split"] == sp))
    lines.append(f"| {sp} | {by_split.get(sp,0)} | {uq} | {role} |")
lines.append("")
lines.append("## Length buckets (segment clips)\n")
for b, n_ in dist(seg_clips, "length_bucket").most_common():
    lines.append(f"- {b}: {n_}")
lines.append("\n## Genre distribution (segment clips)\n")
for g, n_ in dist([c for c in seg_clips if c["genre"]], "genre").most_common():
    lines.append(f"- {g}: {n_}")
lines.append("\n## Domain distribution (unique segments)\n")
for d, n_ in dist(seg_rows, "category").most_common():
    lines.append(f"- {d}: {n_}")
lines.append("\n## Per-signer load\n")
lines.append("| Signer | Role | Clips | Est. hours |")
lines.append("|---|---|---:|---:|")
for r in sm:
    lines.append(f"| {r['signer_id']} | {r['role']} | {r['n_clips']} | {r['est_signing_hours']} |")
lines.append("\n## Safety / review status\n")
for k, n_ in dist(clips, "safety_level").most_common():
    lines.append(f"- safety `{k}`: {n_}")
lines.append(f"- near-dup clusters (segments): {len(set(r['near_dup_cluster'] for r in seg_rows))} "
             f"covering {uniq_seg} segments (multi-member clusters kept within one split)")
lines.append("\n> **review_status is pending_human_vsl_review for all rows** — this script is drafted over "
             "QC-passed candidates. Doc 09 (Vietnamese + Deaf/VSL-expert review) must complete and rejected "
             "rows be dropped before these become final recording orders.")
with open(f"{OUT_DIR}/final_summary.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("WROTE ->", OUT_DIR)
for fn in sorted(os.listdir(OUT_DIR)):
    print("  ", fn)
print(f"\nclips={len(clips)} segments={uniq_seg} eval={len(eval_rows)} est_hours={total_sec/3600:.1f}")
print("split clips:", dict(by_split))
print("per-signer clips:", {r['signer_id']: r['n_clips'] for r in sm})
