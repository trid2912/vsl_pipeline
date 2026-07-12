#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sinh segment corpus VSL bằng API LLM — mỗi lần gọi API sinh MỘT segment.

CHỈ SINH SEGMENT DÀI: phần câu đơn/ngắn (Unique_sentence.csv, 11.913 dòng)
ĐÃ QUAY XONG — các vòng sinh mới chỉ bổ sung segment liên tục ~1–2 phút.

Mục tiêu mỗi segment: ~1–2 phút quay ký (100–155 từ ≈ 75–115s với tốc độ ~0.75 s/từ,
hiệu chuẩn theo doc 01: 70–110 từ ≈ 45–90s; hiệu chỉnh lại sau pilot).

Trục kiểm soát mỗi segment:
  - category (topic)      : 18 chủ đề hiện có
  - genre                 : interview / conversation / storytelling /
                            instruction_procedure / classroom_dialogue (doc 01)
  - emotion (sắc thái)    : 9 nhãn, ưu tiên lặp nhóm thiếu
  - ngữ pháp              : mỗi segment được giao 2 cấu trúc bắt buộc (xoay vòng)
  - thực thể              : >=2 tên riêng thật (truyền vào từ ENTITY_HINTS,
                            thay bằng entity bank doc 03 khi đã crawl)

Cách dùng:
  export OPENAI_API_KEY=...            # hoặc ANTHROPIC_API_KEY với --provider anthropic
  python3 tools/gen_segment_llm.py --provider openai --model gpt-4o \
      --out final_corpus/gen_by_category/round3 --per-genre 2,2,2,3,3
  # endpoint nội bộ / vLLM:
  python3 tools/gen_segment_llm.py --provider openai --base-url http://localhost:8000/v1 --model <model>

Resume: đã có dialogue_id nào trong file output thì bỏ qua — chạy lại vô tư.
"""
import argparse, csv, glob, json, os, re, sys, time, unicodedata, urllib.request
from collections import Counter, defaultdict

# Run relative to the repo root (the dir containing final_corpus), wherever this script lives.
_R = os.path.abspath(__file__)
while _R != os.path.dirname(_R) and not os.path.isdir(os.path.join(_R, 'final_corpus')):
    _R = os.path.dirname(_R)
os.chdir(_R)

# ---------------------------------------------------------------- cấu hình trục
CATEGORIES = {  # slug -> (tên file, giá trị cột category)
    'anuong': ('Ăn_uống.csv', 'Ăn uống'),
    'congviec': ('Công_việc.csv', 'Công việc'),
    'doisong': ('Đời_sống_hàng_ngày.csv', 'Đời sống hàng ngày'),
    'dongvat': ('Động_vật.csv', 'Động vật'),
    'dulich': ('Du_lịch.csv', 'Du lịch'),
    'giadinh': ('Gia_đình.csv', 'Gia đình'),
    'giaitri': ('Giải_trí.csv', 'Giải trí'),
    'giaotiep': ('Giao_tiếp_Xã_hội.csv', 'Giao tiếp Xã hội'),
    'hocduong': ('Học_đường.csv', 'Học đường'),
    'lichsu': ('Lịch_sử.csv', 'Lịch sử'),
    'mausac': ('Màu_sắc.csv', 'Màu sắc'),
    'muasam': ('Mua_sắm.csv', 'Mua sắm'),
    'phuongtien': ('Phương_tiện_di_chuyển.csv', 'Phương tiện di chuyển'),
    'sothich': ('Sở_thích.csv', 'Sở thích'),
    'suckhoe': ('Sức_khỏe.csv', 'Sức khỏe'),
    'thiennhien': ('Thiên_nhiên.csv', 'Thiên nhiên'),
    'thoitiet': ('Thời_tiết_và_Mùa.csv', 'Thời tiết và Mùa'),
    'xahoi': ('Xã_hội.csv', 'Xã hội'),
}

GENRES = ['interview', 'conversation', 'storytelling', 'instruction_procedure', 'classroom_dialogue']

GENRE_SPEC = {
    'interview': 'Phỏng vấn: phóng viên hỏi, chuyên gia/nhân chứng đáp. 10–14 lượt, luân phiên, người hỏi mở đầu. Mỗi lượt bắt đầu bằng nội dung câu, KHÔNG ghi tên người nói.',
    'conversation': 'Hội thoại đời thường giữa hai người bạn. 12–16 lượt, tự nhiên, có hỏi lại và phản hồi.',
    'storytelling': 'Một người kể lại (độc thoại). 14–20 câu, mạch mở đầu → diễn biến → kết thúc (mạch ngầm, không ghi tiêu đề).',
    'instruction_procedure': 'Hướng dẫn từng bước làm một việc thuộc chủ đề. 10–14 câu, chủ yếu câu cầu khiến (hãy/đừng/nhớ/… đi/nhé), theo trình tự trước–sau.',
    'classroom_dialogue': 'Đối thoại thầy–trò trong lớp về chủ đề. 12–16 lượt, thầy hỏi trò đáp, có khen và nhắc nhở.',
}

# --turn-mode multi: hội thoại phức tạp — MỖI LƯỢT gồm 2–4 câu ngắn (dấu '|' = ranh giới LƯỢT nói),
# ít lượt hơn nhưng mỗi lượt giàu ý (nêu ý → lý do/ví dụ → hỏi lại/cảm xúc). Câu vẫn ngắn, ký được.
GENRE_SPEC_MULTI = {
    'interview': ('Phỏng vấn phức tạp: phóng viên hỏi, chuyên gia/nhân chứng đáp. 6–9 LƯỢT luân phiên, người hỏi mở đầu. '
                  'MỖI LƯỢT gồm 2–4 câu ngắn: lượt hỏi có dẫn dắt rồi mới hỏi; lượt đáp nêu ý rồi giải thích/nêu ví dụ hoặc cảm xúc. '
                  'KHÔNG ghi tên người nói ở đầu lượt.'),
    'conversation': ('Hội thoại đời thường phức tạp giữa hai người ĐANG NÓI CHUYỆN TRỰC TIẾP với nhau. 6–9 LƯỢT luân phiên. '
                     'MỖI LƯỢT gồm 2–4 câu ngắn: một người nêu ý và giải thích/kể thêm, người kia phản hồi rồi hỏi lại hoặc bày tỏ cảm xúc. '
                     'Hai người XƯNG HÔ trực tiếp bằng "mình/tớ/cậu" hoặc "anh/chị/em" (TRÁNH "tôi"/"bạn"), có thể gọi tên người kia; '
                     'KHÔNG kể ở ngôi thứ ba (không thuật "Tuấn sợ…, Ngọc lo…"). Có đồng tình hoặc phản đối nhẹ, chuyển ý tự nhiên; KHÔNG ghi tên người nói ở đầu lượt.'),
}

# sắc thái: 9 nhãn; danh sách xoay vòng ưu tiên nhóm thiếu trong corpus hiện tại
EMOTIONS = ['gian_buc', 'so_lo', 'ngac_nhien', 'ghet_chan', 'buon_tiec',
            'vui', 'yeu_thich', 'biet_on_xin_loi', 'trung_tinh']
EMOTION_VI = {
    'vui': 'vui mừng, phấn khởi', 'yeu_thich': 'yêu thích, say mê',
    'buon_tiec': 'buồn bã, tiếc nuối', 'gian_buc': 'giận dữ, bực bội',
    'so_lo': 'sợ hãi, lo lắng', 'ngac_nhien': 'ngạc nhiên, bất ngờ',
    'ghet_chan': 'chán ghét, ngán ngẩm', 'biet_on_xin_loi': 'biết ơn hoặc áy náy xin lỗi',
    'trung_tinh': 'trung tính, thuật sự việc khách quan',
}

# cấu trúc ngữ pháp xoay vòng — mỗi segment nhận 2 mục
GRAMMAR_POOL = [
    'ít nhất 2 câu điều kiện dạng "nếu … thì …"',
    'ít nhất 2 câu tương phản dùng "nhưng" hoặc "tuy … nhưng …"',
    'ít nhất 2 câu có "đang" (hành động đang diễn ra)',
    'ít nhất 2 câu so sánh (hơn / nhất / bằng / như)',
    'ít nhất 2 câu có mốc thời gian (hôm qua / ngày mai / năm ngoái / trước khi / sau khi)',
    'ít nhất 1 câu bị động dùng "được" và 1 câu dùng "bị"',
    'ít nhất 2 câu nhân quả dùng "vì … nên …"',
    'ít nhất 2 câu mục đích dùng "để"',
]

# tên riêng gợi ý — THAY bằng entity bank (doc 03) khi đã crawl
ENTITY_HINTS = {
    'anuong': 'phở Hà Nội, bún bò Huế, bánh mì Sài Gòn, chợ Bến Thành, cà phê Buôn Ma Thuột, bánh xèo miền Tây, chè Huế',
    'congviec': 'khu công nghiệp Bình Dương, công ty may Việt Tiến, cảng Hải Phòng, chợ Đồng Xuân, nhà máy sữa Vinamilk',
    'doisong': 'hồ Gươm, công viên Thống Nhất, chung cư Linh Đàm, siêu thị Co.opmart, cầu Long Biên, hồ Tây',
    'dongvat': 'Thảo Cầm Viên Sài Gòn, vườn quốc gia Cúc Phương, Cát Tiên, sếu đầu đỏ Tràm Chim, voi Tây Nguyên, bán đảo Sơn Trà',
    'dulich': 'vịnh Hạ Long, Đà Lạt, Hội An, Sa Pa, Phú Quốc, chùa Một Cột, động Sơn Đoòng, Nha Trang',
    'giadinh': 'Tết Nguyên Đán, làng gốm Bát Tràng, quê Nam Định, đám giỗ, chợ quê Thái Bình',
    'giaitri': 'sân Mỹ Đình, đội tuyển Việt Nam, rạp CGV, công viên Đầm Sen, Nhà hát Lớn Hà Nội',
    'giaotiep': 'hội Chữ thập đỏ, nhà văn hoá phường, câu lạc bộ người Điếc Hà Nội, bưu điện Sài Gòn, ngày Quốc tế Người Điếc',
    'hocduong': 'trường Chu Văn An, đại học Bách Khoa, thư viện Quốc gia, Văn Miếu, kỳ thi tốt nghiệp',
    'lichsu': 'Hai Bà Trưng, Ngô Quyền, sông Bạch Đằng, Điện Biên Phủ, vua Hùng, Trần Hưng Đạo, Quang Trung, thành Cổ Loa',
    'mausac': 'cờ Việt Nam, áo dài Huế, đèn lồng Hội An, hoa sen Đồng Tháp, lúa chín Mù Cang Chải, hoa đào Hà Nội',
    'muasam': 'chợ Bến Thành, chợ Đồng Xuân, siêu thị Co.opmart, Vincom, chợ nổi Cái Răng, phố Hàng Đào',
    'phuongtien': 'tàu Thống Nhất, sân bay Nội Bài, Tân Sơn Nhất, xe buýt Hà Nội, cầu Long Biên, metro Bến Thành, phà Cát Lái',
    'sothich': 'hồ Tây, sân Mỹ Đình, làng tranh Đông Hồ, cờ tướng, đàn bầu, nhà văn hoá Thanh niên',
    'suckhoe': 'bệnh viện Bạch Mai, bệnh viện Chợ Rẫy, trạm y tế phường, thuốc nam, bệnh viện Nhi Đồng 1',
    'thiennhien': 'núi Phan Xi Păng, sông Mê Kông, rừng U Minh, thác Bản Giốc, động Phong Nha, đồng bằng sông Cửu Long',
    'thoitiet': 'mùa mưa Sài Gòn, mùa đông Hà Nội, bão miền Trung, tuyết Sa Pa, nắng Tây Nguyên, đỉnh Mẫu Sơn',
    'xahoi': 'hội người Điếc Việt Nam, ngày Nhà giáo Việt Nam, Tết Trung Thu, ủy ban phường, hội Chữ thập đỏ',
}

WORD_MIN, WORD_MAX = 100, 155  # ≈ 75–115s khi ký (0.75 s/từ) — CHỈ segment dài; chỉnh lại sau pilot

# ---------------------------------------------------------------- gloss coverage
def norm_vi(s):
    s = unicodedata.normalize('NFC', s).lower()
    s = re.sub(r'[^\w\sàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def _variants(label, display):
    out = set()
    for fld in (label, display):
        if not fld:
            continue
        # "10 - mười", "anh hai, anh cả", "ước mơ/mơ ước" (giữ nguyên dạng ngày 30/4)
        for p in re.split(r'\s+-\s+|,|(?<!\d)/(?!\d)', fld) + [fld]:
            out.add(p)
            out.add(re.sub(r'\([^)]*\)', ' ', p))        # ngoài ngoặc
            out.update(re.findall(r'\(([^)]*)\)', p))    # trong ngoặc
    return {tuple(norm_vi(v).split()) for v in out if norm_vi(v)}

def load_vocab(path):
    id2label, id2forms, form2ids = {}, {}, defaultdict(set)
    for row in csv.DictReader(open(path, encoding='utf-8-sig')):
        uid = row['unique_id']
        id2label[uid] = row['label']
        vs = _variants(row['label'], row.get('display_word', ''))
        id2forms[uid] = vs
        for v in vs:
            form2ids[v].add(uid)
    return id2label, id2forms, form2ids, max(len(k) for k in form2ids)

def match_glosses(text, form2ids, max_len):
    """Greedy longest-match -> Counter(uid)."""
    toks = norm_vi(text.replace('|', ' ')).split()
    hit = Counter()
    j = 0
    while j < len(toks):
        m = 0
        for L in range(min(max_len, len(toks) - j), 0, -1):
            ids = form2ids.get(tuple(toks[j:j + L]))
            if ids:
                for u in ids:
                    hit[u] += 1
                m = L
                break
        j += m or 1
    return hit

def count_coverage(srcs, form2ids, max_len):
    counts = Counter()
    for pat in srcs:
        for f in sorted(glob.glob(pat)):
            try:
                for r in csv.DictReader(open(f, encoding='utf-8-sig')):
                    counts += match_glosses(r.get('sentences/paragraph', ''), form2ids, max_len)
            except Exception as e:
                print(f'  bỏ qua {f}: {e}', file=sys.stderr)
    return counts

def seed_word(uid, id2forms):
    """Chọn dạng từ tự nhiên nhất để yêu cầu model dùng: ưu tiên biến thể có chữ
    (không thuần số), ngắn nhất nhưng >=1 âm tiết."""
    forms = sorted(id2forms[uid], key=lambda f: (all(w.isdigit() for w in f), len(f)))
    return ' '.join(forms[0]) if forms else ''

SYSTEM_PROMPT = """Bạn là chuyên gia Ngôn ngữ Ký hiệu Việt Nam (VSL), xây dựng ngữ liệu cho người Điếc và hệ thống AI dịch ký hiệu.
Quy tắc bắt buộc:
- Câu NGẮN (tối đa 15 từ), trực quan: hành động, vị trí, hình dạng, màu sắc, chuyển động. Hạn chế từ trừu tượng, ẩn dụ, văn chương.
- Khuyến khích một số câu cấu trúc chủ đề→bình luận, ví dụ: "Thác Bản Giốc, nước đổ trắng xoá."
- KHÔNG bịa dữ kiện, số liệu, sự kiện; chỉ dùng kiến thức phổ thông chắc chắn.
- KHÔNG dùng ký tự [ hoặc ], không emoji, không ghi tên/nhãn người nói, không đánh số câu.
- Mỗi câu / mỗi lượt nói nằm trên MỘT dòng riêng. Chỉ trả về các dòng nội dung, không giải thích, không tiêu đề."""

SYSTEM_PROMPT_MULTI = """Bạn là chuyên gia Ngôn ngữ Ký hiệu Việt Nam (VSL), xây dựng ngữ liệu HỘI THOẠI cho người Điếc và hệ thống AI dịch ký hiệu.
Quy tắc bắt buộc:
- Mỗi DÒNG là MỘT LƯỢT NÓI của một người; hai người luân phiên nhau (hoặc phóng viên–người đáp).
- MỖI LƯỢT gồm 2–4 CÂU NGẮN, mỗi câu tối đa 15 từ, trực quan (hành động, vị trí, hình dạng, màu sắc, chuyển động). Các câu trong một lượt nối tiếp tự nhiên: nêu ý → giải thích/lý do/ví dụ → hỏi lại hoặc bày tỏ cảm xúc.
- KHÔNG viết câu dài, không gộp nhiều ý vào một câu; tách thành nhiều câu ngắn.
- Hạn chế từ trừu tượng, ẩn dụ, văn chương. KHÔNG bịa dữ kiện, số liệu, sự kiện.
- KHÔNG dùng ký tự [ hoặc ], không emoji, KHÔNG ghi tên/nhãn người nói ở đầu lượt, không đánh số.
- Chỉ trả về các dòng lượt nói (mỗi lượt một dòng), không giải thích, không tiêu đề."""

USER_PROMPT = """Sinh MỘT segment văn bản tiếng Việt để một người Điếc ký lại trên video (~1–2 phút quay).

- Chủ đề: {category}
- Thể loại: {genre} — {genre_spec}
- Sắc thái cảm xúc chủ đạo: {emotion_vi}. Cảm xúc phải thể hiện rõ bằng TỪ VỰNG trong câu (ví dụ: giận, bực, sợ, lo, ngạc nhiên, bất ngờ, chán, tiếc, mừng, biết ơn…), không chỉ ngụ ý.
- Tổng độ dài: {wmin}–{wmax} từ (đếm mọi dòng cộng lại). Đây là ràng buộc cứng.
- Ngữ pháp bắt buộc có: {grammar1}; và {grammar2}.
- Dùng ít nhất 2 tên riêng thật trong danh sách gợi ý (hoặc tên tương đương phổ biến): {entities}.
  {subject_rule}
- {char_rule}
- TỪ VỰNG BẮT BUỘC: dùng tự nhiên, mỗi từ ít nhất 1 lần, các từ sau: {seed_words}.
  Nếu một từ không hợp chủ đề thì vẫn đưa vào bằng một câu chuyển ý ngắn hợp lý.
- HẠN CHẾ lặp các từ đã quá phổ biến trong corpus: {avoid_words} — chỉ dùng khi thật cần.

Trả về đúng các dòng nội dung của segment, mỗi câu/lượt một dòng."""

# ---------------------------------------------------------------- gọi API
def call_openai(base_url, model, api_key, system, user, temperature):
    req = urllib.request.Request(
        base_url.rstrip('/') + '/chat/completions',
        data=json.dumps({
            'model': model, 'temperature': temperature,
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': user}],
        }).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)['choices'][0]['message']['content']

OLLAMA_NUM_CTX = 4096  # giới hạn KV cache — tránh OOM khi H100 đang chạy job khác (0 = để model tự quyết)

def call_ollama(base_url, model, api_key, system, user, temperature):
    """API native của Ollama (think:false để tắt reasoning của model như gemma4)."""
    options = {'temperature': temperature}
    if OLLAMA_NUM_CTX:
        options['num_ctx'] = OLLAMA_NUM_CTX
    req = urllib.request.Request(
        base_url.rstrip('/') + '/api/chat',
        data=json.dumps({
            'model': model, 'stream': False, 'think': False,
            'options': options,
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': user}],
        }).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)['message']['content']

def call_anthropic(base_url, model, api_key, system, user, temperature):
    req = urllib.request.Request(
        base_url.rstrip('/') + '/v1/messages',
        data=json.dumps({
            'model': model, 'max_tokens': 2048, 'temperature': temperature,
            'system': system, 'messages': [{'role': 'user', 'content': user}],
        }).encode(),
        headers={'Content-Type': 'application/json', 'x-api-key': api_key,
                 'anthropic-version': '2023-06-01'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return ''.join(b.get('text', '') for b in json.load(r)['content'])

# ---------------------------------------------------------------- kiểm tra
# Tiêu chuẩn cộng đồng — nội dung RED bị loại thẳng ở khâu sinh (không được public)
RED_WORDS = ['tình dục', 'làm tình', 'khiêu dâm', 'dương vật', 'âm đạo', 'âm hộ', 'tinh trùng',
             'mại dâm', 'gái điếm', 'bán dâm', 'mua dâm', 'thủ dâm', 'giao hợp', 'dâm ô',
             'hiếp dâm', 'cưỡng hiếp', 'ấu dâm', 'loạn luân',
             'ma túy', 'ma tuý', 'heroin', 'thuốc lắc', 'cần sa', 'chích hút',
             'bán độ', 'cá độ', 'số đề', 'đánh đề',
             'tự tử', 'tự sát', 'treo cổ', 'cắt cổ tay', 'khủng bố', 'chặt đầu', 'thảm sát']
RED_PAT = re.compile(r'\b(' + '|'.join(re.escape(w) for w in RED_WORDS) + r')\b', re.I)

def split_sentences(s):
    return [p.strip() for p in re.split(r'(?<=[.!?…])\s+', s.strip()) if p.strip()]

def validate(text, turn_mode='single'):
    """-> (segment_str, lỗi hoặc None). turn_mode='multi': mỗi dòng là 1 LƯỢT gồm nhiều câu ngắn."""
    lines = [re.sub(r'^\s*[-•*\d.]+\s*', '', l).strip() for l in text.splitlines()]
    # bóc prefix tên người nói ("Thầy:", "Nam:", "Cô Lan:") — kịch bản quay không ghi người nói
    lines = [re.sub(r'^[A-ZĐÀ-Ỹ][\wà-ỹơưêôâăđ]*(\s[A-ZĐÀ-Ỹ][\wà-ỹơưêôâăđ]*)?:\s+', '', l) for l in lines]
    lines = [l for l in lines if l and not l.startswith(('#', '['))]
    if not lines:
        return None, 'rỗng'
    seg = '|'.join(lines)
    if '[' in seg or ']' in seg:
        return None, 'chứa ngoặc vuông'
    m = RED_PAT.search(norm_vi(seg))
    if m:
        return None, f'vi phạm tiêu chuẩn cộng đồng (từ "{m.group(0)}") — tuyệt đối không dùng nội dung này'
    n_words = len(seg.replace('|', ' ').split())
    if not (WORD_MIN - 10 <= n_words <= WORD_MAX + 15):
        return None, f'độ dài {n_words} từ ngoài khoảng {WORD_MIN}-{WORD_MAX}'
    if turn_mode == 'multi':
        # '|' = ranh giới LƯỢT; mỗi lượt phải gồm nhiều câu NGẮN
        sents = [s for l in lines for s in split_sentences(l)]
        too_long = [s for s in sents if len(s.split()) > 16]
        if len(too_long) > 2:
            return None, f'{len(too_long)} câu dài quá 16 từ — mỗi CÂU phải ngắn, ký được'
        if len(sents) < round(len(lines) * 1.6):
            return None, (f'lượt nói chưa đủ phức tạp: {len(sents)} câu / {len(lines)} lượt — '
                          f'mỗi lượt cần 2–4 câu ngắn')
        over = [l for l in lines if len(split_sentences(l)) > 5]
        if over:
            return None, f'{len(over)} lượt có quá 5 câu — rút gọn mỗi lượt còn 2–4 câu'
        if not (6 <= len(lines) <= 11):
            return None, f'số lượt {len(lines)} ngoài khoảng 6–9 (cho phép tới 11)'
    else:
        too_long = [l for l in lines if len(l.split()) > 18]
        if len(too_long) > 2:
            return None, f'{len(too_long)} câu dài quá 18 từ'
    return seg, None

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--provider', choices=['openai', 'anthropic', 'ollama'], default='openai')
    ap.add_argument('--model', default=None, help='bắt buộc trừ khi --report-only')
    ap.add_argument('--base-url', default=None,
                    help='mặc định: https://api.openai.com/v1 hoặc https://api.anthropic.com')
    ap.add_argument('--out', default='final_corpus/gen_by_category/round3')
    ap.add_argument('--per-genre', default='2,2,2,3,3',
                    help='số segment cho interview,conversation,storytelling,instruction,classroom (mỗi category)')
    ap.add_argument('--categories', default='all', help='slug cách nhau bằng dấu phẩy, hoặc all')
    ap.add_argument('--temperature', type=float, default=0.9)
    ap.add_argument('--retries', type=int, default=3)
    ap.add_argument('--sleep', type=float, default=1.0, help='giãn cách giữa các call (s)')
    ap.add_argument('--vocab', default='corpus_files/vsl_unique_glosses_word.csv')
    ap.add_argument('--coverage-src', default='final_corpus/balanced_corpus.csv,final_corpus/gen_by_category/round2/*.csv',
                    help='các nguồn ĐÃ CÓ (đã quay/đã sinh) để đếm độ phủ; glob, phẩy ngăn cách. Output --out tự được cộng thêm.')
    ap.add_argument('--seeds', type=int, default=5, help='số gloss thiếu nhét vào mỗi segment')
    ap.add_argument('--target-count', type=int, default=5, help='gloss có số lần xuất hiện < mức này thì cần seed')
    ap.add_argument('--seed-min-hit', type=int, default=3, help='tối thiểu bao nhiêu seed phải xuất hiện thật trong segment')
    ap.add_argument('--prefix', default='r3', help='tiền tố dialogue_id + giá trị cột source (generated_<prefix>_llm)')
    ap.add_argument('--turn-mode', choices=['single', 'multi'], default='single',
                    help="multi = hội thoại phức tạp, mỗi lượt ('|') gồm 2–4 câu ngắn (chỉ hợp interview/conversation)")
    ap.add_argument('--num-ctx', type=int, default=4096,
                    help='Ollama num_ctx (giới hạn KV cache tránh OOM; 0 = để model tự quyết)')
    ap.add_argument('--seed-max-syl', type=int, default=4, help='độ dài tối đa (âm tiết) của 1 seed')
    ap.add_argument('--name-bank', default='data/processed/entity_bank_person_names.csv',
                    help='CSV tên nhân vật (given_name,gender) — xoay vòng 1 tên nam + 1 tên nữ mỗi segment')
    ap.add_argument('--entity-bank-dir', default='data/processed',
                    help='thư mục entity bank doc 03 — cộng gợi ý địa danh/nước/nhân vật lịch sử xoay vòng vào prompt')
    ap.add_argument('--pairs', default='',
                    help='chế độ sinh tổ hợp chỉ định: "slug:genre:emotion,slug:genre:emotion,..." — bỏ qua --per-genre')
    ap.add_argument('--report-only', action='store_true', help='chỉ in báo cáo độ phủ (kể cả phần đã sinh) rồi thoát')
    ap.add_argument('--exclude', default='feedback/uncovered_glosses_composable.csv,feedback/seed_redlist.csv',
                    help='CSV có cột unique_id — các gloss KHÔNG cần seed (mục ghép/concat, red-list…); phẩy ngăn cách nhiều file')
    args = ap.parse_args()
    global OLLAMA_NUM_CTX
    OLLAMA_NUM_CTX = args.num_ctx

    # ---- độ phủ gloss hiện có (đã quay + đã sinh + output cũ của chính round này)
    id2label, id2forms, form2ids, max_len = load_vocab(args.vocab)
    srcs = [s.strip() for s in args.coverage_src.split(',') if s.strip()]
    srcs.append(os.path.join(args.out, '*.csv'))
    print('Đếm độ phủ gloss từ:', ', '.join(srcs))
    counts = count_coverage(srcs, form2ids, max_len)
    excluded = set()
    for f in [x.strip() for x in (args.exclude or '').split(',') if x.strip()]:
        if os.path.exists(f):
            excluded |= {r['unique_id'] for r in csv.DictReader(open(f, encoding='utf-8-sig'))}
    if excluded:
        print(f'Loại {len(excluded)} gloss khỏi danh sách seed (mục ghép/red-list).')
    need = sorted((u for u in id2label if counts[u] < args.target_count and u not in excluded),
                  key=lambda u: (counts[u], u))
    over = [seed_word(u, id2forms) for u, _ in counts.most_common(15)]
    n0 = sum(1 for u in id2label if counts[u] == 0)
    print(f'Vocab {len(id2label)} | chưa xuất hiện: {n0} | dưới {args.target_count} lần: {len(need)} | lặp nhiều nhất: {", ".join(over[:8])}')

    if args.report_only:
        outp = 'feedback/gloss_need_list.csv'
        os.makedirs('feedback', exist_ok=True)
        with open(outp, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['unique_id', 'label', 'occurrences', 'seed_word'])
            for u in need:
                w.writerow([u, id2label[u], counts[u], seed_word(u, id2forms)])
        print(f'Đã ghi danh sách cần seed: {outp} ({len(need)} gloss)')
        return

    if not args.model:
        sys.exit('Thiếu --model.')
    if args.provider == 'openai':
        key = os.environ.get('OPENAI_API_KEY', '')
        base = args.base_url or 'https://api.openai.com/v1'
        call = call_openai
    elif args.provider == 'ollama':
        key = 'local'
        base = args.base_url or 'http://localhost:11434'
        call = call_ollama
    else:
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        base = args.base_url or 'https://api.anthropic.com'
        call = call_anthropic
    if not key and 'localhost' not in (base or ''):
        sys.exit(f'Thiếu API key cho {args.provider} (đặt biến môi trường).')

    per_genre = dict(zip(GENRES, map(int, args.per_genre.split(','))))
    pairs_parsed = []
    if args.pairs:
        for p in args.pairs.split(','):
            s, g, e = p.strip().split(':')
            assert s in CATEGORIES and g in GENRES and e in EMOTIONS, f'pair sai: {p}'
            pairs_parsed.append((s, g, e))
        slugs = list(dict.fromkeys(s for s, _, _ in pairs_parsed))
    else:
        slugs = list(CATEGORIES) if args.categories == 'all' else args.categories.split(',')

    # bank tên nhân vật (doc 03) — xoay vòng để không segment nào trùng cặp tên
    name_m, name_f = ['Sơn', 'Khánh', 'Phong'], ['Hà', 'Thảo', 'Uyên']
    if os.path.exists(args.name_bank):
        _nm, _nf = [], []
        for r in csv.DictReader(open(args.name_bank, encoding='utf-8-sig')):
            (_nm if r['gender'] == 'nam' else _nf).append(r['given_name'])
        if _nm and _nf:
            name_m, name_f = _nm, _nf
    name_i = [0]

    # entity bank doc 03: địa danh VN (ưu tiên 1), nước ngoài Tier A, nhân vật lịch sử safe_edu
    bank_places, bank_foreign, bank_figures = [], [], []
    _b = args.entity_bank_dir
    if _b and os.path.isdir(_b):
        _p = os.path.join(_b, 'entity_bank_vietnam_places.csv')
        if os.path.exists(_p):
            bank_places = [r['name_vi'] for r in csv.DictReader(open(_p, encoding='utf-8-sig'))
                           if r['priority'] == '1']
        _p = os.path.join(_b, 'entity_bank_foreign_places_tierA.csv')
        if os.path.exists(_p):
            bank_foreign = [r['name_vi'] for r in csv.DictReader(open(_p, encoding='utf-8-sig'))
                            if r['priority'] == '1']
        _p = os.path.join(_b, 'entity_bank_historical_figures_tierA.csv')
        if os.path.exists(_p):
            bank_figures = [r['name_vi'] for r in csv.DictReader(open(_p, encoding='utf-8-sig'))
                            if r['safety_level'] == 'safe_edu']
        _p = os.path.join(_b, 'entity_bank_culture_heritage_tierA.csv')
        if os.path.exists(_p):
            bank_culture = defaultdict(list)
            for r in csv.DictReader(open(_p, encoding='utf-8-sig')):
                if r['safety_level'] == 'safe_edu' and r['priority'] in ('1', '2'):
                    bank_culture[r['entity_type']].append(r['name_vi'])
        else:
            bank_culture = {}
        print(f'Entity bank: {len(bank_places)} địa danh, {len(bank_foreign)} nước, '
              f'{len(bank_figures)} nhân vật lịch sử, {sum(map(len, bank_culture.values()))} văn hoá/di sản')
    os.makedirs(args.out, exist_ok=True)

    FIELDS = ['stt', 'sentences/paragraph', 'category', 'source', 'dialogue_id', 'genre', 'emotion']
    total_new = total_skip = total_fail = 0
    seed_ptr = [0]   # con trỏ xoay vòng qua danh sách gloss thiếu

    for slug in slugs:
        fname, cat = CATEGORIES[slug]
        path = os.path.join(args.out, fname)
        done = {}
        if os.path.exists(path):
            done = {r['dialogue_id']: r for r in csv.DictReader(open(path, encoding='utf-8-sig'))}
        rows = list(done.values())

        # kế hoạch: genre x số lượng, emotion xoay vòng LỆCH PHA theo category
        # (nếu mọi category cùng bắt đầu ở EMOTIONS[0] thì lưới genre x emotion bị
        # trống hệ thống — bài học round2/3)
        if args.pairs:
            plan = [(g, e, GRAMMAR_POOL[j % len(GRAMMAR_POOL)],
                     GRAMMAR_POOL[(j + 3) % len(GRAMMAR_POOL)])
                    for j, (s, g, e) in enumerate(pairs_parsed) if s == slug]
        else:
            plan = []
            ei = sum(map(ord, slug)) % len(EMOTIONS)
            gi = sum(map(ord, slug)) % len(GRAMMAR_POOL)
            for g in GENRES:
                for _ in range(per_genre[g]):
                    plan.append((g, EMOTIONS[ei % len(EMOTIONS)],
                                 GRAMMAR_POOL[gi % len(GRAMMAR_POOL)],
                                 GRAMMAR_POOL[(gi + 3) % len(GRAMMAR_POOL)]))
                    ei += 1; gi += 1

        for i, (genre, emo, g1, g2) in enumerate(plan, 1):
            did = f'{args.prefix}_{slug}_{i:03d}'
            if did in done:
                total_skip += 1
                continue

            # chọn seed: xoay vòng qua danh sách thiếu (không lặp bộ seed giữa các
            # segment liên tiếp), bỏ dạng thuần số (đã có quy ước NUM:), tối đa 4 âm tiết
            seeds, used_forms, scanned = [], set(), 0
            while len(seeds) < args.seeds and scanned < len(need):
                u = need[seed_ptr[0] % len(need)]
                seed_ptr[0] += 1
                scanned += 1
                if counts[u] >= args.target_count:
                    continue
                w = seed_word(u, id2forms)
                if not w or w in used_forms or len(w.split()) > args.seed_max_syl or all(t.isdigit() for t in w.split()):
                    continue
                seeds.append((u, w))
                used_forms.add(w)
            seed_words = ', '.join(f'"{w}"' for _, w in seeds) or '(không có)'

            char_names = (f'{name_m[name_i[0] % len(name_m)]} (nam), '
                          f'{name_f[(name_i[0] * 3 + 1) % len(name_f)]} (nữ)')
            k = name_i[0]
            name_i[0] += 1
            extra_ents = []
            if bank_places:
                extra_ents += [bank_places[k % len(bank_places)], bank_places[(k * 7 + 3) % len(bank_places)]]
            if bank_foreign:
                extra_ents.append(bank_foreign[k % len(bank_foreign)])
            if bank_figures and slug in ('lichsu', 'xahoi', 'hocduong'):
                extra_ents.append(bank_figures[k % len(bank_figures)])
            CULTURE_SLUG = {'anuong': ['traditional_food'], 'dulich': ['heritage_site', 'festival'],
                            'giaitri': ['traditional_music', 'festival'], 'muasam': ['traditional_craft'],
                            'lichsu': ['historical_event', 'folk_story'],
                            'xahoi': ['intangible_cultural_heritage'],
                            'sothich': ['traditional_music', 'traditional_craft'],
                            'giadinh': ['traditional_food', 'folk_story']}
            for typ in CULTURE_SLUG.get(slug, []):
                pool = bank_culture.get(typ, [])
                if pool:
                    extra_ents.append(pool[k % len(pool)])
            entities = ENTITY_HINTS[slug] + (('; gợi ý thêm: ' + ', '.join(extra_ents)) if extra_ents else '')
            sys_prompt = SYSTEM_PROMPT_MULTI if args.turn_mode == 'multi' else SYSTEM_PROMPT
            gspec = (GENRE_SPEC_MULTI.get(genre, GENRE_SPEC[genre])
                     if args.turn_mode == 'multi' else GENRE_SPEC[genre])
            # dialogue mode (multi + interview/conversation): người nói xưng hô trực tiếp, KHÔNG kể ngôi thứ ba
            dialogue = args.turn_mode == 'multi' and genre in ('interview', 'conversation')
            if dialogue:
                subject_rule = ('Người nói XƯNG HÔ trực tiếp bằng "mình/tớ/cậu" hoặc "anh/chị/em" (TRÁNH "tôi"/"bạn"); '
                                'chỉ dùng tên riêng cho ĐỊA DANH và người/vật ĐƯỢC NHẮC TỚI. '
                                'TUYỆT ĐỐI không tự gọi mình bằng tên và không kể về người đối thoại ở ngôi thứ ba.')
                char_rule = (f'Nếu nhắc tới nhân vật thứ ba, có thể đặt tên: {char_names}. '
                             f'TUYỆT ĐỐI không dùng tên "Nam" hay "Lan"; hai người đang trò chuyện thì xưng hô trực tiếp, không gọi nhau bằng tên ở ngôi thứ ba.')
            else:
                subject_rule = 'Dùng tên người, tên địa danh làm CHỦ NGỮ/TÂN NGỮ ở nhiều câu thay cho "tôi"/"bạn" — đa dạng chủ ngữ, vị ngữ.'
                char_rule = f'Nhân vật trong segment (nếu có) đặt tên: {char_names}. TUYỆT ĐỐI không dùng tên "Nam" hay "Lan" cho nhân vật.'
            user = USER_PROMPT.format(
                category=cat, genre=genre, genre_spec=gspec,
                emotion_vi=EMOTION_VI[emo], wmin=WORD_MIN, wmax=WORD_MAX,
                grammar1=g1, grammar2=g2, entities=entities,
                subject_rule=subject_rule, char_rule=char_rule,
                seed_words=seed_words, avoid_words=', '.join(over[:10]))
            seg = err = None
            for attempt in range(args.retries):
                try:
                    raw = call(base, args.model, key, sys_prompt,
                               user if not err else user + f'\n\nLần trước bị lỗi: {err}. Hãy sửa và sinh lại.',
                               args.temperature)
                    seg, err = validate(unicodedata.normalize('NFC', raw), args.turn_mode)
                    if seg and seeds:  # kiểm tra seed có mặt thật
                        hit = match_glosses(seg, form2ids, max_len)
                        got = [w for u, w in seeds if hit.get(u)]
                        if len(got) < min(args.seed_min_hit, len(seeds)):
                            missing = [w for u, w in seeds if not hit.get(u)]
                            seg, err = None, f'thiếu từ bắt buộc: {", ".join(missing)}'
                    if seg:
                        break
                except Exception as e:
                    err = str(e)[:200]
                    time.sleep(3 * (attempt + 1))
            if not seg:
                print(f'  FAIL {did} ({genre}/{emo}): {err}', file=sys.stderr)
                total_fail += 1
                continue
            rows.append({'stt': 0, 'sentences/paragraph': seg, 'category': cat,
                         'source': f'generated_{args.prefix}_llm', 'dialogue_id': did,
                         'genre': genre, 'emotion': emo})
            total_new += 1
            counts += match_glosses(seg, form2ids, max_len)   # cập nhật vòng kín
            # ghi ngay sau mỗi segment (an toàn khi ngắt giữa chừng)
            rows.sort(key=lambda r: r['dialogue_id'])
            for n, r in enumerate(rows, 1):
                r['stt'] = n
            with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                w.writeheader(); w.writerows(rows)
            n_words = len(seg.replace('|', ' ').split())
            print(f'  OK {did} {genre:<22} {emo:<16} {n_words} từ ≈ {n_words*0.75:.0f}s')
            time.sleep(args.sleep)

    print(f'\nXong: {total_new} mới, {total_skip} bỏ qua (đã có), {total_fail} lỗi. Output: {args.out}/')

if __name__ == '__main__':
    main()
