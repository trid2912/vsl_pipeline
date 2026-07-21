#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sửa các segment trong final_recording_script.csv bị dùng DANH TỪ RIÊNG làm TÊN NGƯỜI
một cách bất thường — nhân vật lịch sử (vua/danh nhân), địa danh (tỉnh/huyện/chợ/bệnh viện),
hay thương hiệu/tổ chức — trong domain KHÔNG phải 'Lịch sử'.

LLM (Ollama API) vừa PHÁN vừa VIẾT LẠI:
  - Nếu danh từ riêng bị gán làm tên người trò chuyện -> thay bằng tên thường, viết lại tự nhiên.
  - Nếu danh từ riêng chỉ là địa điểm/sự vật ĐANG ĐƯỢC NHẮC TỚI (vd đi du lịch Sa Pa,
    bệnh viện Bạch Mai), hoặc đã là tên người thường (Lan, Yến) -> GIỮ NGUYÊN (changed=false).

Ứng viên = segment non-'Lịch sử' có một cụm tên riêng (>=2 âm tiết) LẶP >=2 lần
(dấu hiệu đặc trưng của lỗi sinh máy: hội thoại tự nhiên dùng đại từ, không lặp tên đầy đủ).

Backup .bak (giữ nếu đã có). Chỉ sửa `segment_text` (+ cập nhật segmented_word_count).

Chạy:
  python3 fix_proper_name_misuse.py --limit 4      # test
  python3 fix_proper_name_misuse.py                # full
  python3 fix_proper_name_misuse.py --model gemma4:31b
"""
import argparse, collections, json, os, re, shutil, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_R = os.path.abspath(__file__)
while _R != os.path.dirname(_R) and not os.path.isdir(os.path.join(_R, "annotation")):
    _R = os.path.dirname(_R)
REC = os.path.join(_R, "annotation", "data", "recording")
CSV = os.path.join(REC, "final_recording_script.csv")
REVIEW = os.path.join(REC, "proper_name_fixes.csv")
JSONL = os.path.join(REC, "_fix_propname_checkpoint.jsonl")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# Nhân vật lịch sử (để lưới an toàn strip nếu LLM sót; địa danh-as-person dựa vào LLM)
HIST = sorted([
    "an dương vương","lý thường kiệt","lý thái tổ","lý công uẩn","lý nam đế","lý bí","ngô quyền",
    "nguyễn trãi","nguyễn huệ","nguyễn ánh","nguyễn du","lê thánh tông","lê đại hành","lê hoàn","lê lợi",
    "trần thái tông","trần nhân tông","trần hưng đạo","trần quốc tuấn","gia long","minh mạng","tự đức",
    "hàm nghi","quang trung","thiệu trị","kiến phúc","trưng trắc","trưng nhị","hai bà trưng","bà triệu",
    "đinh tiên hoàng","đinh bộ lĩnh","hùng vương","vua hùng","phùng hưng","mai an tiêm","thánh gióng",
    "lang liêu","hồ xuân hương","chu văn an","lương thế vinh","phan thanh giản","mạc đăng dung",
    "anrê dũng lạc","mạc đĩnh chi","đĩnh chi",
], key=len, reverse=True)

NAME_POOL = ["nam","lan","minh","hoa","an","mai","tú","linh","thảo","hà","phong","quân",
             "my","bình","dũng","hương","long","hằng","sơn","trang","hải","yến","khoa","chi",
             "đạt","vy","nga","toàn","khánh","hiếu","thu","kiên"]

def is_title(t):
    t = t.strip(".,!?;:\"'()")
    return len(t) >= 2 and t[0].isupper() and t[1:].islower() and t.isalpha()

def repeated_propnames(text):
    toks = text.split(); out = []; i = 0
    while i < len(toks):
        if is_title(toks[i]):
            j = i
            while j < len(toks) and is_title(toks[j]):
                j += 1
            if j - i >= 2:
                out.append(" ".join(x.strip(".,!?;:\"'()") for x in toks[i:j]))
            i = j
        else:
            i += 1
    c = collections.Counter(out)
    return [n for n, k in c.items() if k >= 2]

SYSTEM = (
    "Bạn là biên tập ngữ liệu tiếng Việt cho ngôn ngữ ký hiệu. Bạn phát hiện khi một DANH TỪ "
    "RIÊNG bị dùng làm TÊN NGƯỜI trò chuyện một cách bất thường và viết lại cho tự nhiên như "
    "văn nói hằng ngày. GIỮ cách viết hoa/thường chuẩn tiếng Việt (viết hoa đầu câu và tên "
    "riêng người). Viết HOÀN TOÀN bằng tiếng Việt, không dùng ngoặc. Chỉ trả JSON hợp lệ."
)

USER_TMPL = """CHỦ ĐỀ: {domain}

Đoạn hội thoại (mỗi dòng một LƯỢT NÓI, đánh số):
{numbered}

Trong đoạn, có DANH TỪ RIÊNG nào bị dùng làm TÊN NGƯỜI trò chuyện một cách BẤT THƯỜNG không?
Bất thường = tên nhân vật lịch sử (vua chúa, danh nhân), địa danh (tỉnh/huyện/chợ/bệnh viện/
sân bay), hoặc thương hiệu/tổ chức... bị gán làm tên một người bình thường đang nói chuyện.
Ví dụ SAI cần sửa: "Hiệp Hòa làm ở viện nghiên cứu" (Hiệp Hòa là địa danh), "Lý Thường Kiệt là
phi công" (nhân vật lịch sử).
KHÔNG phải lỗi (GIỮ NGUYÊN): danh từ riêng chỉ là ĐỊA ĐIỂM/SỰ VẬT đang được nhắc tới
(vd "mình đi du lịch Sa Pa", "khám ở bệnh viện Bạch Mai"), hoặc đã là tên người bình thường
(Lan, Yến, Minh...).

- Nếu CÓ lỗi: viết lại đoạn TỰ NHIÊN, thay tên bất thường bằng tên người bình thường "{name}"
  (người kia gọi "{name}", "{name}" tự xưng "mình/tôi"). Bỏ HẲN danh từ riêng bất thường,
  câu phải hợp lý, có chủ ngữ, đúng chủ đề. GIỮ NGUYÊN số lượt = {n}.
- Nếu KHÔNG có lỗi: trả nguyên văn các lượt, "changed": false.

QUAN TRỌNG về định dạng lượt nói:
- Dùng ĐÚNG một tên "{name}" (viết đúng một lần, không lặp kiểu "{name} {name}").
- KHÔNG đánh số thứ tự đầu lượt (không "1.", "2.")... chỉ ghi nội dung câu.

Trả JSON: {{"changed": true/false, "luot": ["lượt 1", ..., "lượt {n}"]}}. Chỉ trả JSON."""

def parse_json(s):
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None

def clean_turn(s):
    """Bỏ số thứ tự lượt LLM lỡ chèn ('1. ', '2) ') và gộp khoảng trắng.
    KHÔNG gộp token lặp (giữ từ láy tiếng Việt: xanh xanh, nhỏ nhỏ)."""
    s = re.sub(r'^\s*\d+\s*[\.\)]\s*', '', s.strip())
    return re.sub(r'\s+', ' ', s).replace(' ,', ',').replace(' .', '.').strip()

def hist_in(t):
    t = str(t).casefold()
    return [n for n in HIST if n in t]

def strip_hist(turns):
    out = []
    for s in turns:
        for nm in HIST:
            if nm in s.casefold():
                s = re.compile(re.escape(nm), re.IGNORECASE).sub("bạn ấy", s)
        s = re.sub(r"\bchào bạn ấy\b", "xin chào", s, flags=re.IGNORECASE)
        out.append(re.sub(r"\s+", " ", s).replace(" ,", ",").replace(" .", ".").strip())
    return out

def process(client, model, domain, turns, name, retries=4):
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(turns))
    for a in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": USER_TMPL.format(
                              domain=domain, numbered=numbered, n=len(turns), name=name)}],
                response_format={"type": "json_object"}, temperature=0.3 + 0.15 * a)
            data = parse_json(r.choices[0].message.content)
            if not isinstance(data, dict) or not isinstance(data.get("luot"), list):
                continue
            changed = bool(data.get("changed"))
            luot = [clean_turn(str(x)) for x in data["luot"]]
            if not changed:
                return turns, "unchanged"
            if len(luot) != len(turns):
                continue
            if any(hist_in(x) for x in luot):   # lưới an toàn cho nhân vật lịch sử
                luot = strip_hist(luot)
            return luot, "fixed"
        except Exception:
            time.sleep(1.5 * (a + 1))
    return turns, "failed"

def wc(text):
    return len(str(text).replace("|", " ").split())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b-instruct-q4_K_M")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    import pandas as pd

    d = pd.read_csv(CSV)
    d["_rep"] = d.segment_text.map(lambda t: repeated_propnames(str(t)))
    # Ứng viên = non-'Lịch sử' VÀ (có tên riêng lặp >=2  HOẶC  chứa tên nhân vật lịch sử đã biết).
    # Nhánh HIST bù lại các segment mà tên chỉ xuất hiện 1 lần (bộ lặp bỏ sót).
    nonhist = d.domain.astype(str).str.strip().str.casefold() != "lịch sử"
    mask = nonhist & (d._rep.map(len) > 0) | (nonhist & d.segment_text.map(lambda t: len(hist_in(t)) > 0))
    targets = d[mask].copy()
    if args.limit:
        targets = targets.head(args.limit)
    print(f"Segment ứng viên (non-'Lịch sử', tên riêng lặp >=2): {len(targets)} | model: {args.model}")

    done = {}
    if os.path.exists(JSONL):
        for line in open(JSONL, encoding="utf-8"):
            try:
                r = json.loads(line); done[r["segment_id"]] = r
            except Exception:
                pass
    todo = targets[~targets.segment_id.isin(done)]
    print(f"Đã xử lý trước: {len(done)} | còn lại: {len(todo)}")

    from openai import OpenAI
    client = OpenAI(base_url=OLLAMA_URL, api_key="ollama")

    def work(row):
        turns = [t.strip() for t in str(row.segment_text).split("|")]
        name = NAME_POOL[hash(row.segment_id) % len(NAME_POOL)]
        new, status = process(client, args.model, row.domain, turns, name)
        return {"segment_id": row.segment_id, "domain": row.domain, "status": status,
                "old": row.segment_text, "new": "|".join(new)}

    t0 = time.time()
    if len(todo):
        with open(JSONL, "a", encoding="utf-8") as f:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(work, r): r for r in todo.itertuples(index=False)}
                for i, fut in enumerate(as_completed(futs), 1):
                    rec = fut.result(); f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                    done[rec["segment_id"]] = rec
                    if i % 5 == 0 or i == len(todo):
                        el = time.time() - t0; rate = i / el if el else 0
                        print(f"  [{i}/{len(todo)}] {rate:.3f}/s ETA {(len(todo)-i)/rate/60:.1f}p" if rate else f"  [{i}/{len(todo)}]")

    if not os.path.exists(CSV + ".bak"):
        shutil.copy2(CSV, CSV + ".bak"); print(f"Backup -> {CSV}.bak")

    fixes = {sid: r["new"] for sid, r in done.items() if r["status"] == "fixed"}
    m = d.segment_id.isin(fixes)
    d.loc[m, "segment_text"] = d.loc[m, "segment_id"].map(fixes)
    d.loc[m, "segmented_word_count"] = d.loc[m, "segment_text"].map(wc)
    d.drop(columns=["_rep"]).to_csv(CSV, index=False, encoding="utf-8-sig")

    rows = [{"segment_id": sid, "domain": r["domain"], "status": r["status"],
             "old_text": r["old"], "new_text": r["new"]} for sid, r in done.items()]
    pd.DataFrame(rows).to_csv(REVIEW, index=False, encoding="utf-8-sig")

    st = collections.Counter(r["status"] for r in done.values())
    print(f"\n===== XONG =====")
    print(f"Ứng viên: {len(done)} | fixed: {st['fixed']} | giữ nguyên: {st['unchanged']} | failed: {st['failed']}")
    print(f"Review -> {REVIEW}")
    print(f"CSV    -> {CSV}")

if __name__ == "__main__":
    main()
