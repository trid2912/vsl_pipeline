#!/usr/bin/env python3
"""Đa dạng hoá tên nhân vật trong các segment sinh máy: thay "Nam"/"Lan"
(model lạm dụng) bằng tên gọi phổ biến crawl từ UIT-ViNames
(data/processed/entity_bank_person_names.csv), khớp giới tính, nhất quán
trong từng segment, tất định theo dialogue_id (chạy lại cho cùng kết quả).

Guard không đụng: Việt Nam, miền/Quảng/Hà/Tây/Đông/phía/hướng + Nam,
Nam + Định/Bộ/Cao/Kỳ/Á, Hà Lan, Lan Anh. Giữ nguyên 1/13 segment
(Nam/Lan vẫn là tên thật, không nên biến mất hoàn toàn).

Cách dùng: python3 tools/diversify_names.py <dir1> <dir2> ...  (sửa tại chỗ)
"""
import csv, glob, hashlib, os, re, sys

# repo root = nearest ancestor containing data/ (works wherever this script lives)
_R = os.path.abspath(__file__)
while _R != os.path.dirname(_R) and not os.path.isdir(os.path.join(_R, 'data', 'processed')):
    _R = os.path.dirname(_R)
BANK = os.path.join(_R, 'data/processed/entity_bank_person_names.csv')
KEEP_EVERY = 13   # 1/13 segment giữ nguyên Nam/Lan

NAM_PAT = re.compile(
    r'(?<!Việt )(?<!việt )(?<!miền )(?<!Miền )(?<!Quảng )(?<!Hà )(?<!Tây )(?<!Đông )'
    r'(?<!phía )(?<!Phía )(?<!hướng )\bNam\b(?! Định| Bộ| Cao| Kỳ| Á)')
LAN_PAT = re.compile(r'(?<!Hà )(?<!hà )\bLan\b(?! Anh)')

def load_pools():
    male, female = [], []
    for r in csv.DictReader(open(BANK, encoding='utf-8-sig')):
        (male if r['gender'] == 'nam' else female).append(r['given_name'])
    return male, female

def pick(pool, did, salt, taken):
    h = int(hashlib.md5(f'{did}:{salt}'.encode()).hexdigest(), 16)
    for k in range(len(pool)):
        cand = pool[(h + k) % len(pool)]
        if cand not in taken:
            return cand
    return pool[h % len(pool)]

def diversify(text, did, male, female):
    if int(hashlib.md5(did.encode()).hexdigest(), 16) % KEEP_EVERY == 0:
        return text, {}
    taken = set(re.findall(r'\b[A-ZĐÀ-Ỹ][a-zà-ỹơưêôâăđ]+\b', text))
    mapping = {}
    if NAM_PAT.search(text):
        mapping['Nam'] = pick(male, did, 'nam', taken); taken.add(mapping['Nam'])
    if LAN_PAT.search(text):
        mapping['Lan'] = pick(female, did, 'lan', taken)
    out = NAM_PAT.sub(mapping.get('Nam', 'Nam'), text)
    out = LAN_PAT.sub(mapping.get('Lan', 'Lan'), out)
    return out, mapping

def main():
    male, female = load_pools()
    n_seg = n_rep = 0
    for d in sys.argv[1:]:
        for f in sorted(glob.glob(os.path.join(d, '*.csv'))):
            if 'qc_report' in f or '00_INDEX' in f:
                continue
            rows = list(csv.DictReader(open(f, encoding='utf-8-sig')))
            if not rows or 'dialogue_id' not in rows[0]:
                continue
            changed = False
            for r in rows:
                new, mapping = diversify(r['sentences/paragraph'], r['dialogue_id'], male, female)
                if mapping:
                    r['sentences/paragraph'] = new
                    changed = True; n_rep += 1
                n_seg += 1
            if changed:
                with open(f, 'w', newline='', encoding='utf-8-sig') as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                    w.writeheader(); w.writerows(rows)
    print(f'Đã xử lý {n_seg} segment, thay tên trong {n_rep} segment.')

if __name__ == '__main__':
    main()
