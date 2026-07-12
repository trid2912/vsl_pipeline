#!/usr/bin/env python3
"""Thay đại từ "tôi"/"bạn" bằng tên nhân vật (nhân vật lịch sử safe_edu +
tên thường từ entity bank) để đa dạng chủ ngữ/tân ngữ — yêu cầu doc 03 và
phản hồi người dùng 05/07.

Quy tắc:
- Giữ 1/3 segment nguyên bản (VSL vẫn cần dữ liệu đại từ IX:), tất định theo dialogue_id.
- Hội thoại: MỘT tên duy nhất cho cả "tôi" lẫn "bạn" xuyên suốt segment —
  đọc như phỏng vấn/trò chuyện về một người. BÀI HỌC 05/07 (d00747): đảo vai
  theo lượt chẵn/lẻ giả định luân phiên tuyệt đối → người trả lời nói 2 câu
  liền là danh tính bị lật ("Tân... rồi thành Ngô Quyền"). Một tên = luôn coherent.
- Độc thoại (storytelling/instruction): "tôi" → A cố định, "bạn" → B cố định.
- Nhân vật A ~60% là nhân vật lịch sử (safety_level=safe_edu, ≤3 âm tiết),
  còn lại tên thường; B luôn tên thường.
- Segment chứa nội dung YELLOW (trộm cắp, bạo lực…) → CHỈ tên thường.
- Segment dài ≥135 từ → chỉ tên 1 âm tiết (tránh vượt trần độ dài).
- Guard: "chúng tôi", "các/những/người bạn", "bạn bè/thân/cùng/học/gái/trai".

Cách dùng: python3 tools/diversify_pronouns.py <csv hoặc dir>...  (sửa tại chỗ)
"""
import csv, glob, hashlib, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qc_segments import YELLOW_PAT

ROOT = os.path.abspath(__file__)
while ROOT != os.path.dirname(ROOT) and not os.path.isdir(os.path.join(ROOT, 'data', 'processed')):
    ROOT = os.path.dirname(ROOT)
NAME_BANK = os.path.join(ROOT, 'data/processed/entity_bank_person_names.csv')
HIST_BANK = os.path.join(ROOT, 'data/processed/entity_bank_historical_figures_tierA.csv')
KEEP_EVERY = 3          # 1/3 segment giữ nguyên tôi/bạn
HIST_RATIO = 0.6        # tỷ lệ dùng nhân vật lịch sử làm tên nhân vật (user chốt 05/07)

TOI = re.compile(r'(?<!chúng )(?<!Chúng )\b[Tt]ôi\b')
BAN = re.compile(r'(?<!các )(?<!Các )(?<!những )(?<!Những )(?<!người )(?<!Người )(?<!một )(?<!Một )'
                 r'(?<!hai )(?<!Hai )(?<!ba )(?<!bốn )(?<!mấy )(?<!nhiều )(?<!đám )(?<!nhóm )'
                 r'\b[Bb]ạn\b(?! ấy| bè| thân| cùng| học| gái| trai| đời| nữ| nam| mới| nào| của)')
QUESTION = re.compile(r'\?')


def load_pools():
    given = []
    for r in csv.DictReader(open(NAME_BANK, encoding='utf-8-sig')):
        given.append(r['given_name'])
    hist = []
    for r in csv.DictReader(open(HIST_BANK, encoding='utf-8-sig')):
        if r['safety_level'] == 'safe_edu' and len(r['name_vi'].split()) <= 3:
            hist.append(r['name_vi'])
    return given, hist


def h(did, salt=''):
    return int(hashlib.md5(f'{did}:{salt}'.encode()).hexdigest(), 16)


def pick_names(did, text, so_tu, given, hist):
    """-> (A, B)"""
    yellow = bool(YELLOW_PAT.search(text.lower()))
    short_only = so_tu >= 135
    pool_a = given if (yellow or short_only or h(did, 'ha') % 100 >= HIST_RATIO * 100) else hist
    a = pool_a[h(did, 'a') % len(pool_a)]
    if short_only:
        g1 = [g for g in given if len(g.split()) == 1] or given
        a = g1[h(did, 'a') % len(g1)]
    b = given[h(did, 'b') % len(given)]
    while b == a or b in text:
        b = given[(given.index(b) + 1) % len(given)]
    return a, b


def diversify(text, did, genre, so_tu, given, hist):
    if h(did) % KEEP_EVERY == 0:
        return text, None
    if not (TOI.search(text) or BAN.search(text)):
        return text, None
    a, b = pick_names(did, text, so_tu, given, hist)
    dialog = genre in ('conversation', 'interview', 'classroom_dialogue') or \
             (not genre and len(QUESTION.findall(text)) >= 2)
    if dialog:
        # 1 tên duy nhất cho cả tôi/bạn — không bao giờ lật danh tính giữa chừng
        out = BAN.sub(a, TOI.sub(a, text))
        return out, (a, a)
    out = BAN.sub(b, TOI.sub(a, text))
    return out, (a, b)


def process_csv(path, given, hist, text_col='sentences/paragraph'):
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    if not rows or text_col not in rows[0] or 'dialogue_id' not in rows[0]:
        return 0
    changed = 0
    for r in rows:
        # chỉ đụng dòng sinh máy — Unique_sentence (đã quay) và FLEURS giữ nguyên
        if not r.get('source', 'generated').startswith('gen'):
            continue
        did = r['dialogue_id'] or f"stt_{r.get('stt', '')}"
        so_tu = len(r[text_col].replace('|', ' ').split())
        new, mapping = diversify(r[text_col], did, r.get('genre', ''), so_tu, given, hist)
        if mapping:
            r[text_col] = new
            changed += 1
    if changed:
        with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
    return changed


def main():
    given, hist = load_pools()
    print(f'pool: {len(given)} tên thường + {len(hist)} nhân vật lịch sử safe_edu')
    total = 0
    for arg in sys.argv[1:]:
        files = sorted(glob.glob(os.path.join(arg, '*.csv'))) if os.path.isdir(arg) else [arg]
        for f in files:
            if 'qc_report' in f or '00_INDEX' in f:
                continue
            n = process_csv(f, given, hist)
            total += n
            if n:
                print(f'  {os.path.basename(f)}: {n} segment')
    print(f'Tổng: thay đại từ trong {total} segment.')


if __name__ == '__main__':
    main()
