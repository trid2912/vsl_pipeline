#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cổng QC tự động cho segment đã sinh (hiện thực bước lọc doc 08) — chạy TRƯỚC khi
đưa sang duyệt người (doc 09) và trước khi merge/quay.

Kiểm tra mỗi segment:
  1. format   : đủ 7 cột, không [ ], không xuống dòng trong ô
  2. length   : tổng từ trong [--wmin, --wmax]; mọi câu <= 18 từ
  3. genre    : số lượt/câu đúng khoảng theo genre
  4. emotion  : nhãn sắc thái có từ vựng tương ứng trong nội dung
  5. dup      : câu trùng nguyên văn nội bộ / với corpus đã có (kể cả 347 câu FLEURS -> leakage)
  6. near-dup : segment giống segment khác >= --jaccard (3-gram từ)
  7. oov      : tỉ lệ âm tiết không khớp từ điển gloss <= --oov (proxy độ "ký được")

Dùng:
  python3 tools/qc_segments.py final_corpus/gen_by_category/round2 \
      [--against final_corpus/balanced_corpus.csv] [--wmin 59] [--wmax 170]
Kết quả: PASS/FAIL từng file + chi tiết flag ghi ra <dir>/qc_report.csv
"""
import argparse, csv, glob, os, re, sys, unicodedata
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_segment_llm import norm_vi, load_vocab, match_glosses  # tái dùng, tránh lệch logic

# Run relative to the repo root (the dir containing final_corpus), wherever this script lives.
_R = os.path.abspath(__file__)
while _R != os.path.dirname(_R) and not os.path.isdir(os.path.join(_R, 'final_corpus')):
    _R = os.path.dirname(_R)
os.chdir(_R)

GENRE_TURNS = {  # khoảng số lượt/câu hợp lệ
    'interview': (6, 16), 'conversation': (8, 18), 'storytelling': (10, 22),
    'instruction_procedure': (6, 16), 'classroom_dialogue': (8, 18),
}
# Tiêu chuẩn cộng đồng (public dataset): ĐỎ = loại thẳng; VÀNG = cần người duyệt ngữ cảnh
from gen_segment_llm import RED_PAT  # dùng chung 1 danh sách với khâu sinh
YELLOW_WORDS = ['rượu', 'bia', 'thuốc lá', 'nghiện', 'cờ bạc', 'đánh bạc', 'cá độ',
                'súng', 'bom', 'đạn', 'dao găm', 'giết', 'máu me', 'đánh nhau', 'bạo lực',
                'ăn cắp', 'ăn trộm', 'cướp', 'lừa đảo', 'đánh đập', 'ly hôn', 'ngoại tình',
                'phản động', 'biểu tình', 'lật đổ', 'chống phá', 'thế lực thù địch']
YELLOW_PAT = re.compile(r'\b(' + '|'.join(re.escape(w) for w in YELLOW_WORDS) + r')\b', re.I)

EMO_LEX = {
    'vui': r'vui|mừng|hạnh phúc|sung sướng|phấn khởi|hào hứng|cười',
    'yeu_thich': r'yêu|thích|mê|quý|thương|hâm mộ',
    'buon_tiec': r'buồn|khóc|tiếc|nhớ|thất vọng|tủi',
    'gian_buc': r'giận|tức|bực|cáu|nổi nóng|khó chịu',
    'so_lo': r'sợ|lo|hoảng|hồi hộp|run',
    'ngac_nhien': r'ngạc nhiên|bất ngờ|kinh ngạc|sửng sốt|không ngờ|ồ|ủa',
    'ghet_chan': r'ghét|chán|ngán|khinh',
    'biet_on_xin_loi': r'cảm ơn|cám ơn|biết ơn|xin lỗi|áy náy',
    'trung_tinh': r'.',  # không yêu cầu
}

def shingles(text, n=3):
    t = norm_vi(text.replace('|', ' ')).split()
    return {' '.join(t[i:i + n]) for i in range(len(t) - n + 1)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir', help='thư mục chứa các CSV segment cần QC')
    ap.add_argument('--against', default='final_corpus/balanced_corpus.csv',
                    help='corpus đã có để soát trùng/leakage (glob, phẩy ngăn cách)')
    ap.add_argument('--vocab', default='corpus_files/vsl_unique_glosses_word.csv')
    ap.add_argument('--wmin', type=int, default=59)
    ap.add_argument('--wmax', type=int, default=170)
    ap.add_argument('--maxsent', type=int, default=18, help='số từ tối đa mỗi câu')
    ap.add_argument('--jaccard', type=float, default=0.5, help='ngưỡng near-dup segment')
    ap.add_argument('--oov', type=float, default=0.45, help='tỉ lệ âm tiết ngoài từ điển tối đa')
    args = ap.parse_args()

    _, _, form2ids, max_len = load_vocab(args.vocab)

    # corpus đối chứng
    old_utts, old_shingles, fleurs_utts = set(), [], set()
    for pat in args.against.split(','):
        for f in sorted(glob.glob(pat.strip())):
            for r in csv.DictReader(open(f, encoding='utf-8-sig')):
                txt = r.get('sentences/paragraph', '')
                for u in txt.split('|'):
                    u = norm_vi(u)
                    if u:
                        old_utts.add(u)
                        if r.get('source') == 'transcriptions.csv':
                            fleurs_utts.add(u)
                old_shingles.append(shingles(txt))

    files = sorted(glob.glob(os.path.join(args.dir, '*.csv')))
    files = [f for f in files if not f.endswith('qc_report.csv')]
    report, seen_utts, seen_sh = [], {}, []
    FIELDS = ['stt', 'sentences/paragraph', 'category', 'source', 'dialogue_id', 'genre', 'emotion']

    for f in files:
        rows = list(csv.DictReader(open(f, encoding='utf-8-sig')))
        fails = 0
        for r in rows:
            flags = []
            txt = r.get('sentences/paragraph', '')
            did = r.get('dialogue_id', '?')
            # 1. format
            if list(r.keys()) != FIELDS:
                flags.append('schema')
            if '[' in txt or ']' in txt or '\n' in txt:
                flags.append('ký tự cấm')
            # 2. length
            utts = [u.strip() for u in txt.split('|') if u.strip()]
            n_words = len(txt.replace('|', ' ').split())
            if not (args.wmin <= n_words <= args.wmax):
                flags.append(f'độ dài {n_words} từ')
            long_s = [u for u in utts if len(u.split()) > args.maxsent]
            if long_s:
                flags.append(f'{len(long_s)} câu >{args.maxsent} từ')
            # 3. genre
            lo, hi = GENRE_TURNS.get(r.get('genre', ''), (1, 99))
            if not (lo <= len(utts) <= hi):
                flags.append(f'genre {r.get("genre")}: {len(utts)} lượt ngoài [{lo},{hi}]')
            # 4. emotion
            emo = r.get('emotion', '')
            if emo in EMO_LEX and not re.search(EMO_LEX[emo], norm_vi(txt)):
                flags.append(f'không thấy từ vựng cảm xúc "{emo}"')
            # 5. dup + leakage
            for u in utts:
                nu = norm_vi(u)
                if nu in fleurs_utts:
                    flags.append(f'LEAKAGE FLEURS: "{u[:40]}"')
                elif nu in old_utts and len(nu.split()) >= 5:
                    flags.append(f'trùng corpus cũ: "{u[:40]}"')
                elif nu in seen_utts and seen_utts[nu] != did and len(nu.split()) >= 5:
                    flags.append(f'trùng nội bộ với {seen_utts[nu]}: "{u[:40]}"')
                seen_utts.setdefault(nu, did)
            # 6. near-dup segment
            sh = shingles(txt)
            if sh:
                for other_did, osh in seen_sh:
                    j = len(sh & osh) / len(sh | osh)
                    if j >= args.jaccard:
                        flags.append(f'near-dup {j:.2f} với {other_did}')
                        break
            seen_sh.append((did, sh))
            # 7. oov / signability proxy
            toks = norm_vi(txt.replace('|', ' ')).split()
            n_matched = 0
            j = 0
            while j < len(toks):
                m = 0
                for L in range(min(max_len, len(toks) - j), 0, -1):
                    if tuple(toks[j:j + L]) in form2ids:
                        m = L
                        break
                n_matched += m
                j += m or 1
            oov = 1 - n_matched / max(1, len(toks))
            if oov > args.oov:
                flags.append(f'OOV {oov:.0%} > {args.oov:.0%}')
            # 8. tiêu chuẩn cộng đồng
            m = RED_PAT.search(norm_vi(txt))
            if m:
                flags.append(f'RED-CONTENT "{m.group(0)}" — loại, không public được')
            else:
                m = YELLOW_PAT.search(norm_vi(txt))
                if m:
                    flags.append(f'yellow-content "{m.group(0)}" — cần người duyệt ngữ cảnh')
            if flags:
                fails += 1
                report.append({'file': os.path.basename(f), 'dialogue_id': did,
                               'genre': r.get('genre'), 'emotion': emo,
                               'flags': ' ; '.join(flags)})
        print(f'{"PASS" if fails == 0 else "FAIL":<5} {os.path.basename(f):<30} {len(rows)} segment, {fails} bị flag')

    outp = os.path.join(args.dir, 'qc_report.csv')
    with open(outp, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=['file', 'dialogue_id', 'genre', 'emotion', 'flags'])
        w.writeheader()
        w.writerows(report)
    print(f'\nTổng flag: {len(report)} → {outp}')
    print('QC tự động chỉ là cổng 1 — vẫn cần duyệt người Việt + chuyên gia VSL (doc 09) trước khi quay.')

if __name__ == '__main__':
    main()
