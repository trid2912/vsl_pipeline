#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Giảm lặp tên trong hội thoại cho tự nhiên: giữ tên ở LẦN ĐẦU, các lần sau thay bằng đại từ.
Quy ước hội thoại Q&A (dialog_turns): lượt lẻ = người HỎI (gọi người kia -> 'bạn'),
lượt chẵn = người TRẢ LỜI (nói về mình -> 'mình'). Giữ nguyên casing (đầu câu viết hoa).

Chỉ xử lý các tên đã thay ở bước trước (đọc name_fixes_review.csv) và segment dạng dialog_turns.
Backup .bak3. In before/after để review; --apply để ghi.
"""
import argparse, os, re
import pandas as pd

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(R, "annotation", "data", "recording")
CSV = os.path.join(REC, "final_recording_script.csv")
REVIEW = os.path.join(REC, "name_fixes_review.csv")

def sentence_initial(turn_text, pos):
    """True nếu vị trí pos đứng đầu câu (đầu lượt hoặc sau . ? ! …)."""
    j = pos - 1
    while j >= 0 and turn_text[j] == " ":
        j -= 1
    return j < 0 or turn_text[j] in ".?!…:"

def derepeat_turn(turn, name, pronoun, keep_first):
    """Thay các occurrence của name trong 1 lượt bằng pronoun (trừ khi keep_first cho lần đầu)."""
    out = []
    i = 0
    n_kept = 0
    low = turn.casefold()
    nl = name.casefold()
    while i < len(turn):
        if low.startswith(nl, i) and (i == 0 or not turn[i-1].isalpha()) and \
           (i + len(name) >= len(turn) or not turn[i+len(name)].isalpha()):
            if keep_first and n_kept == 0:
                out.append(turn[i:i+len(name)]); n_kept += 1
            else:
                p = pronoun.capitalize() if sentence_initial(turn, i) else pronoun
                out.append(p)
            i += len(name)
        else:
            out.append(turn[i]); i += 1
    return "".join(out)

def answerer_parity(turns):
    """Vai HỎI = parity có nhiều câu '?' hơn; người TRẢ LỜI = parity còn lại."""
    q = {0: 0, 1: 0}
    for k, t in enumerate(turns):
        if t.strip().endswith("?"):
            q[k % 2] += 1
    asker = 0 if q[0] > q[1] else 1        # parity hỏi nhiều hơn
    return 1 - asker                        # người trả lời (tự xưng 'mình')

def derepeat_segment(segment_text, name):
    turns = segment_text.split("|")
    first_turn = next((k for k, t in enumerate(turns) if name.casefold() in t.casefold()), None)
    if first_turn is None:
        return segment_text
    ans = answerer_parity(turns)
    new = []
    for k, t in enumerate(turns):
        pronoun = "mình" if (k % 2 == ans) else "bạn"   # lượt của người trả lời -> mình
        new.append(derepeat_turn(t, name, pronoun, keep_first=(k == first_turn)))
    return "|".join(new)

def wc(t): return len(str(t).replace("|", " ").split())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="chỉ xử lý segment_id này (test)")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    d = pd.read_csv(CSV)
    rev = pd.read_csv(REVIEW)
    fixed = rev[rev.quyet_dinh.astype(str).str.startswith("thay")]
    name_of = {r.segment_id: r.thay_bang for _, r in fixed.iterrows() if isinstance(r.thay_bang, str) and r.thay_bang}

    idx = d.set_index("segment_id")
    changed = 0
    shown = 0
    skip_nondialog = 0
    for sid, name in name_of.items():
        if sid not in idx.index:
            continue
        row = idx.loc[sid]
        if args.only and sid != args.only:
            continue
        if str(row.text_format) != "dialog_turns":     # chỉ hội thoại luân phiên
            skip_nondialog += 1
            continue
        old = str(row.segment_text)
        new = derepeat_segment(old, name)
        if new != old:
            changed += 1
            m = d.segment_id == sid
            d.loc[m, "segment_text"] = new
            d.loc[m, "segmented_word_count"] = wc(new)
            if shown < args.show or args.only:
                print(f"\n### {sid}  (tên: {name})")
                for o, n in zip(old.split("|"), new.split("|")):
                    print(("  = " if o == n else "  CŨ : ") + o)
                    if o != n: print("  MỚI:", n)
                shown += 1

    print(f"\nSegment giảm lặp tên: {changed} | bỏ qua (không phải dialog_turns): {skip_nondialog}")
    if args.apply:
        if not os.path.exists(CSV + ".bak3"):
            import shutil; shutil.copy2(CSV, CSV + ".bak3")
        d.to_csv(CSV, index=False, encoding="utf-8-sig")
        print(f"ĐÃ GHI -> {CSV}")
    else:
        print("(chỉ xem — thêm --apply để ghi)")

if __name__ == "__main__":
    main()
