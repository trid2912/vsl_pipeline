#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Thay TÊN NHÂN VẬT LỊCH SỬ / danh từ riêng bị dùng làm TÊN NGƯỜI trong
final_recording_script.csv bằng tên người bình thường — THAY XÁC ĐỊNH (không LLM),
giữ nguyên casing, nội dung, số lượt. Chỉ đụng segment domain != 'Lịch sử'.

Phân biệt người vs địa danh:
- Nhân vật lịch sử (HIST): trong domain đời thường luôn là tên-người bị lạm dụng -> thay.
- Danh từ riêng kiểu địa danh (PLACE_LIKE: Thống Nhất, Bến Thành, Cửu Long, Bạch Mai,
  Chợ Rẫy, Việt Tiến, Hiệp Hòa): CHỈ thay khi dùng làm người (có ngữ cảnh người),
  giữ nguyên khi là địa danh (đứng sau tàu/metro/ga/chợ/sông/đồng bằng/bệnh viện...).

In toàn bộ quyết định để review. Ghi khi chạy với --apply.
"""
import argparse, hashlib, os, re
import pandas as pd

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(R, "annotation", "data", "recording", "final_recording_script.csv")
REVIEW = os.path.join(R, "annotation", "data", "recording", "name_fixes_review.csv")

HIST = ["an dương vương","lý thường kiệt","lý thái tổ","lý công uẩn","lý nam đế","ngô quyền",
    "nguyễn trãi","nguyễn huệ","nguyễn ánh","nguyễn du","lê thánh tông","lê đại hành","lê hoàn","lê lợi",
    "trần thái tông","trần nhân tông","trần hưng đạo","trần quốc tuấn","gia long","minh mạng","tự đức",
    "hàm nghi","quang trung","thiệu trị","kiến phúc","trưng trắc","trưng nhị","bà triệu","đinh tiên hoàng",
    "đinh bộ lĩnh","hùng vương","vua hùng","phùng hưng","mai an tiêm","thánh gióng","lang liêu",
    "hồ xuân hương","chu văn an","lương thế vinh","phan thanh giản","mạc đăng dung","anrê dũng lạc",
    "mạc đĩnh chi"]
PLACE_LIKE = ["thống nhất","bến thành","cửu long","bạch mai","chợ rẫy","việt tiến","hiệp hòa"]
ALL = sorted(HIST + PLACE_LIKE, key=len, reverse=True)

# đứng NGAY TRƯỚC tên -> địa danh/sự vật (không phải người)
PLACE_PREFIX = ["tàu","metro","ga","chợ","sông","đồng bằng","công viên","bến xe","bến","bệnh viện",
    "viện","trường","phố","đường","quán","hãng","thương hiệu","hiệu","khu","cầu","núi","hồ","đảo",
    "vịnh","thành phố","tỉnh","huyện","xã","quê","đền","chùa","siêu thị","nhà máy","công ty","đại lộ",
    "may","xưởng","nhãn hiệu","thương hiệu","ở","tại","đến","về"]
# Danh từ riêng LUÔN là thương hiệu/địa danh (không bao giờ là người) -> luôn giữ
ALWAYS_KEEP = {"việt tiến"}
# dấu hiệu NGƯỜI: đứng trước tên
PERSON_PREFIX = ["chào","anh","chị","cô","bạn","em","ông","bà","bố","mẹ","gia đình","nhà","con",
    "cháu","bác","chú","dì","cậu","mợ","thím","của","cho","với","tên","bé"]
# dấu hiệu NGƯỜI: đứng NGAY SAU tên (vị ngữ cá nhân)
PERSON_SUFFIX = ["là","làm","lái","bay","thích","sống","ăn","uống","học","có","hiến","quản","nghiên",
    "gọi","đi","đến","cảm","thấy","nhớ","yêu","bị","đã","sẽ","rất","thường","mở","bán","đào","hướng",
    "tuổi","bao","ơi","cần","muốn","vừa","cũng","phải","luôn","chưa","nói","kể","mua","chơi","xem"]

POOL_M = ["Minh","Nam","Phong","Quân","Dũng","Long","Sơn","Hải","Khoa","Đạt","Toàn","Bình","Kiên","Hùng","Tuấn"]
POOL_F = ["Lan","Hoa","Mai","Linh","Thảo","Hương","Hằng","Trang","Yến","My","Nga","Thu","Chi","Vy","Hà"]
FEMALE_HINT = {"hồ xuân hương","trưng trắc","trưng nhị","bà triệu"}

def pick_name(seg_id, hist_name):
    pool = POOL_F if hist_name in FEMALE_HINT else POOL_M
    h = int(hashlib.md5(seg_id.encode()).hexdigest(), 16)
    return pool[h % len(pool)]

def words_before(text, pos, k=2):
    # '|' ngăn lượt KHÔNG có khoảng trắng -> coi như dấu cách để không dính token
    return text[:pos].casefold().replace("|", " ").split()[-k:]

def classify(text, name):
    """Trả về 'person' nếu name dùng làm người ở ít nhất 1 vị trí, 'place' nếu chỉ địa danh."""
    tl = text.casefold()
    person = place = False
    for m in re.finditer(re.escape(name), tl):
        wb = words_before(text, m.start(), 3)
        prev1 = wb[-1] if wb else ""
        prev2 = " ".join(wb[-2:]) if len(wb) >= 2 else ""
        after = tl[m.end():].replace("|", " ").lstrip(" ,.?!:").split()[:1]
        nxt = after[0] if after else ""
        is_place_ctx = (prev1 in PLACE_PREFIX) or (prev2 in PLACE_PREFIX)
        is_person_ctx = (prev1 in PERSON_PREFIX) or (prev2 in PERSON_PREFIX) or (nxt in PERSON_SUFFIX)
        # ƯU TIÊN nơi chốn: có "bệnh viện/chợ/tàu/công viên..." ngay trước -> địa danh,
        # dù theo sau là động từ (bệnh viện Chợ Rẫy LÀM VIỆC, công viên Thống Nhất SẼ...).
        if is_place_ctx:
            place = True
        elif is_person_ctx:
            person = True
        # else: danh từ riêng trơ, không rõ -> bỏ qua (mặc định giữ, an toàn)
    return "person" if person else "place"

INSTITUTION = {"trường", "quốc học", "thpt", "thcs", "tiểu học", "đại học", "học viện",
               "cao đẳng", "trung học", "lớp"}

def hist_verdict(text, name):
    """HIST = người, TRỪ khi MỌI occurrence đứng sau từ chỉ trường/cơ sở/địa danh
    (trường Chu Văn An, Quốc Học...) -> khi đó là tên trường/địa danh, giữ nguyên."""
    tl = text.casefold()
    any_person = False
    for m in re.finditer(re.escape(name), tl):
        wb = words_before(text, m.start(), 3)
        prev1 = wb[-1] if wb else ""
        prev2 = " ".join(wb[-2:]) if len(wb) >= 2 else ""
        if prev1 in INSTITUTION or prev2 in INSTITUTION or prev1 in PLACE_PREFIX or prev2 in PLACE_PREFIX:
            continue
        any_person = True
    return "person" if any_person else "place"

def fix_segment(seg_id, text, domain):
    """Thay các tên đóng vai người. Trả (new_text, [(name, repl, verdict)])."""
    decisions = []
    new = text
    for name in ALL:
        if name not in text.casefold():
            continue
        # HIST trong domain đời thường: là người, TRỪ khi đứng sau từ chỉ trường/cơ sở
        # (trường Chu Văn An, Quốc Học...) -> giữ vì là tên trường/địa danh.
        if name in ALWAYS_KEEP:
            verdict = "place"
        elif name in HIST:
            verdict = hist_verdict(text, name)
        else:
            verdict = classify(text, name)
        if verdict != "person":
            decisions.append((name, None, "giữ (địa danh)")
            )
            continue
        repl = pick_name(seg_id, name)
        # thay giữ casing: các occurrence đều là Title Case -> repl cũng Title Case
        new = re.sub(re.escape(name), repl, new, flags=re.IGNORECASE)
        decisions.append((name, repl, "thay (người)"))
    return new, decisions

def wc(t): return len(str(t).replace("|", " ").split())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ghi vào CSV (mặc định chỉ xem)")
    ap.add_argument("--show", type=int, default=0, help="in text đầy đủ N segment đầu")
    args = ap.parse_args()

    d = pd.read_csv(CSV)
    nonhist = d.domain.astype(str).str.strip().str.casefold() != "lịch sử"
    rows = []
    n_fix = n_keep = 0
    full = []
    for i, r in d[nonhist].iterrows():
        text = str(r.segment_text)
        if not any(n in text.casefold() for n in ALL):
            continue
        new, dec = fix_segment(r.segment_id, text, r.domain)
        changed = new != text
        if changed: n_fix += 1
        for name, repl, verdict in dec:
            rows.append({"segment_id": r.segment_id, "domain": r.domain, "ten": name,
                         "thay_bang": repl or "", "quyet_dinh": verdict})
            if verdict.startswith("giữ"): n_keep += 1
        if changed:
            full.append((r.segment_id, r.domain, text, new))
        if changed:
            d.at[i, "segment_text"] = new
            d.at[i, "segmented_word_count"] = wc(new)

    rev = pd.DataFrame(rows)
    print("=== QUYẾT ĐỊNH TỪNG TÊN ===")
    for _, x in rev.iterrows():
        print(f"  [{x.domain}|{x.segment_id}] {x.ten!r} -> {x.quyet_dinh}" + (f" = {x.thay_bang}" if x.thay_bang else ""))
    print(f"\nSegment được sửa: {n_fix} | lượt 'giữ (địa danh)': {n_keep}")

    for sid, dom, old, new in full[:args.show]:
        print(f"\n### {sid} [{dom}]")
        for o, n in zip(old.split("|"), new.split("|")):
            print(("  = " if o == n else "  CŨ : ") + o)
            if o != n: print("  MỚI:", n)

    if args.apply:
        if not os.path.exists(CSV + ".bak2"):
            import shutil; shutil.copy2(CSV, CSV + ".bak2")
        d.to_csv(CSV, index=False, encoding="utf-8-sig")
        rev.to_csv(REVIEW, index=False, encoding="utf-8-sig")
        print(f"\nĐÃ GHI -> {CSV}")
        print(f"Review  -> {REVIEW}")
    else:
        print("\n(chỉ xem — thêm --apply để ghi)")

if __name__ == "__main__":
    main()
