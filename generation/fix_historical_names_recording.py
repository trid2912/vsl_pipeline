#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sửa các câu bị XUYÊN TẠC LỊCH SỬ trong final_recording_script.csv:
những segment thuộc domain KHÔNG phải 'Lịch sử' nhưng bị nhét tên nhân vật lịch sử
(vd "Lý Thường Kiệt là phi công") -> viết lại thành hội thoại đời thường tự nhiên,
gán tên người bình thường, giữ nguyên số lượt nói (dấu '|').

Dùng Ollama API (OpenAI-compatible, không cần key).
Chỉ sửa cột `segment_text`; các cột khác giữ nguyên (trừ segmented_word_count cập nhật lại).
Backup file gốc -> .bak trước khi ghi.

Chạy:
  python3 fix_historical_names_recording.py --limit 3   # test
  python3 fix_historical_names_recording.py             # full 57
  python3 fix_historical_names_recording.py --model gemma4:31b
"""
import argparse, json, os, re, shutil, time
from concurrent.futures import ThreadPoolExecutor, as_completed

# repo root = tổ tiên gần nhất có annotation/
_R = os.path.abspath(__file__)
while _R != os.path.dirname(_R) and not os.path.isdir(os.path.join(_R, "annotation")):
    _R = os.path.dirname(_R)
CSV = os.path.join(_R, "annotation", "data", "recording", "final_recording_script.csv")
REVIEW = os.path.join(_R, "annotation", "data", "recording", "historical_name_fixes.csv")
JSONL = os.path.join(_R, "annotation", "data", "recording", "_fix_hist_checkpoint.jsonl")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# Tên nhân vật / sự kiện lịch sử để DÒ (dài trước để thay cụm dài trước)
NAMES = sorted([
    "an dương vương","lý thường kiệt","lý thái tổ","lý công uẩn","lý nam đế","lý bí",
    "ngô quyền","nguyễn trãi","nguyễn huệ","nguyễn ánh","lê thánh tông","lê đại hành","lê hoàn","lê lợi",
    "trần thái tông","trần nhân tông","trần hưng đạo","trần quốc tuấn","trần quốc toản",
    "gia long","minh mạng","tự đức","hàm nghi","quang trung",
    "trưng trắc","trưng nhị","hai bà trưng","bà triệu","triệu thị trinh",
    "đinh tiên hoàng","đinh bộ lĩnh","hùng vương","vua hùng","phùng hưng","mai thúc loan",
    "mai an tiêm","thánh gióng","lang liêu","sơn tinh","thủy tinh","mị châu","trọng thủy",
    "hồ quý ly","mạc đăng dung","nguyễn hoàng","hoàng hoa thám","phan bội châu","phan đình phùng",
], key=len, reverse=True)

def hist_names_in(text):
    t = str(text).casefold()
    return [n for n in NAMES if n in t]

NAME_POOL = ["nam","lan","minh","hoa","an","mai","tú","linh","thảo","hà","phong","quân",
             "my","bình","dũng","hương","long","hằng","sơn","trang","hải","yến","khoa","chi"]

SYSTEM = (
    "Bạn là biên tập ngữ liệu tiếng Việt cho ngôn ngữ ký hiệu. Bạn viết lại hội thoại đời "
    "thường sao cho TỰ NHIÊN như văn nói hằng ngày, CÓ CHỦ NGỮ, không cụt lủn nhưng vẫn "
    "ngắn gọn dễ hiểu. Chữ thường, không dùng ngoặc. Chỉ trả JSON hợp lệ."
)

USER_TMPL = """CHỦ ĐỀ: {domain}

Đoạn hội thoại gốc dưới đây bị nhét TÊN NHÂN VẬT LỊCH SỬ vào một cách vô lý, xuyên tạc lịch sử
(mỗi dòng là một LƯỢT NÓI, ngăn bằng số thứ tự):
{numbered}

Hãy VIẾT LẠI thành hội thoại đời thường TỰ NHIÊN, đúng chủ đề "{domain}", như văn nói hằng ngày.
YÊU CẦU:
1. BỎ HẲN mọi tên nhân vật/sự kiện lịch sử. Nhân vật chính đặt tên bình thường là "{name}"
   (người kia gọi "{name}", còn "{name}" tự xưng "mình/tôi"). Đoạn mới TUYỆT ĐỐI không còn
   tên nhân vật lịch sử nào.
2. Câu phải hợp lý, CÓ CHỦ NGỮ, tự nhiên như đời thường (một phi công/giám đốc/phiên dịch...
   bình thường), KHÔNG vô lý kiểu người xưa lái máy bay.
3. GIỮ đúng chủ đề và ý chính của đoạn gốc. KHÔNG bịa thêm chủ đề mới.
4. GIỮ NGUYÊN số lượt nói = {n} (trả về đúng {n} phần tử). Giữ nhịp hỏi–đáp luân phiên.
   Chữ thường, không ngoặc.

Trả JSON: {{"luot": ["lượt 1", "lượt 2", ...]}}  (đúng {n} phần tử). Chỉ trả JSON."""

def parse_json(s):
    try: return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None

def strip_names_det(turns):
    out = []
    for s in turns:
        for nm in NAMES:
            if nm in s.casefold():
                s = re.compile(re.escape(nm), re.IGNORECASE).sub("bạn ấy", s)
        s = re.sub(r"\bchào bạn ấy\b", "xin chào", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).replace(" ,", ",").replace(" .", ".").strip()
        out.append(s)
    return out

def rewrite(client, model, domain, turns, name, retries=4):
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(turns))
    best = None
    for a in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role":"system","content":SYSTEM},
                          {"role":"user","content":USER_TMPL.format(
                              domain=domain, numbered=numbered, n=len(turns), name=name)}],
                response_format={"type":"json_object"}, temperature=0.4+0.15*a)
            data = parse_json(r.choices[0].message.content)
            cand = [str(x).strip() for x in data["luot"]] if isinstance(data, dict) and isinstance(data.get("luot"), list) else None
        except Exception:
            time.sleep(1.5*(a+1)); continue
        if not cand: continue
        clean = not any(hist_names_in(c) for c in cand)
        if len(cand)==len(turns) and clean:
            return cand, "ok"
        if best is None or (len(cand)==len(turns) and len(best)!=len(turns)):
            best = cand
    if best is None:
        return None, "failed"
    if len(best)!=len(turns):
        best = best[:len(turns)] + turns[len(best):] if len(best)<len(turns) else best[:len(turns)]
    if any(hist_names_in(c) for c in best):
        best = strip_names_det(best)
    return best, "salvaged"

def wc(text):  # đếm từ thô (khoảng trắng), bỏ dấu '|'
    return len(str(text).replace("|", " ").split())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b-instruct-q4_K_M")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    import pandas as pd

    d = pd.read_csv(CSV)
    d["_hist"] = d.segment_text.map(hist_names_in)
    mask = (d.domain.astype(str).str.strip().str.casefold()!="lịch sử") & (d._hist.map(len)>0)
    targets = d[mask].copy()
    if args.limit:
        targets = targets.head(args.limit)
    print(f"Segment cần sửa (non-'Lịch sử' + có tên nhân vật): {len(targets)} | model: {args.model}")

    done = {}
    if os.path.exists(JSONL):
        for line in open(JSONL, encoding="utf-8"):
            try:
                r=json.loads(line); done[r["segment_id"]]=r
            except Exception: pass
    todo = targets[~targets.segment_id.isin(done)]
    print(f"Đã sửa trước: {len(done)} | còn lại: {len(todo)}")

    from openai import OpenAI
    client = OpenAI(base_url=OLLAMA_URL, api_key="ollama")

    def work(row):
        turns = [t.strip() for t in str(row.segment_text).split("|")]
        name = NAME_POOL[hash(row.segment_id) % len(NAME_POOL)]
        new, status = rewrite(client, args.model, row.domain, turns, name)
        new = new or turns
        return {"segment_id": row.segment_id, "domain": row.domain, "status": status,
                "old": row.segment_text, "new": "|".join(new)}

    t0=time.time()
    if len(todo):
        with open(JSONL, "a", encoding="utf-8") as f:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs={ex.submit(work, r):r for r in todo.itertuples(index=False)}
                for i,fut in enumerate(as_completed(futs),1):
                    rec=fut.result(); f.write(json.dumps(rec,ensure_ascii=False)+"\n"); f.flush()
                    done[rec["segment_id"]]=rec
                    if i%5==0 or i==len(todo):
                        el=time.time()-t0; rate=i/el if el else 0
                        print(f"  [{i}/{len(todo)}] {rate:.3f}/s ETA {(len(todo)-i)/rate/60:.1f}p" if rate else f"  [{i}/{len(todo)}]")

    # Backup + áp vào CSV
    if not os.path.exists(CSV+".bak"):
        shutil.copy2(CSV, CSV+".bak"); print(f"Backup -> {CSV}.bak")
    fixes={sid:r["new"] for sid,r in done.items()}
    m = d.segment_id.isin(fixes)
    d.loc[m,"segment_text"] = d.loc[m,"segment_id"].map(fixes)
    d.loc[m,"segmented_word_count"] = d.loc[m,"segment_text"].map(wc)
    d.drop(columns=["_hist"]).to_csv(CSV, index=False, encoding="utf-8-sig")

    rows=[{"segment_id":sid,"domain":r["domain"],"status":r["status"],
           "old_text":r["old"],"new_text":r["new"]} for sid,r in done.items()]
    pd.DataFrame(rows).to_csv(REVIEW, index=False, encoding="utf-8-sig")

    # verify sạch
    d2=pd.read_csv(CSV)
    resid=d2[(d2.domain.astype(str).str.strip().str.casefold()!="lịch sử") &
             (d2.segment_text.map(lambda t: len(hist_names_in(t))>0))]
    n_salv=sum(1 for r in done.values() if r["status"]=="salvaged")
    n_fail=sum(1 for r in done.values() if r["status"]=="failed")
    print(f"\n===== XONG =====")
    print(f"Đã sửa: {len(fixes)} | salvaged: {n_salv} | failed: {n_fail} | còn sót tên: {len(resid)}")
    print(f"Review -> {REVIEW}")
    print(f"CSV    -> {CSV}")

if __name__=="__main__":
    main()
