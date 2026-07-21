# Cẩm nang sửa lỗi `final_recording_script.csv`

> Ghi lại các **loại lỗi** trong segment sinh máy + **cách sửa** để rà từng câu nhất quán.
> Cột sửa: `segment_text` (các lượt ngăn bằng `|`, KHÔNG có khoảng trắng quanh `|`).
> Khi đổi số lượt → cập nhật `utterance_count`; luôn cập nhật `segmented_word_count`.
> Backup gốc: `final_recording_script.csv.bak` (pristine). Sau bước sửa tên: `.bak2`.

---

## A. Danh từ riêng bị dùng làm TÊN NGƯỜI trò chuyện
Nhân vật lịch sử / địa danh / thương hiệu bị nhét làm người nói hoặc người được gọi.

**Sửa:** thay bằng tên người bình thường (giữ casing: đầu câu & tên viết hoa).
**GIỮ NGUYÊN (không phải người):**
- Địa danh là nơi chốn đang nhắc tới: `đi du lịch Sa Pa`, `chợ Bến Thành`, `bệnh viện Chợ Rẫy`, `tàu Thống Nhất`, `sông Cửu Long`.
- Tên trường theo danh nhân: `trường Chu Văn An`, `Quốc Học`, `đại học Bách Khoa`.
- Thương hiệu: `công ty may Việt Tiến`.
- Người thật đang được bàn tới: `ca sĩ Sơn Tùng`.
- **Chủ đề bàn luận trong domain Lịch sử**: `Trần Hưng Đạo là ai?` (giữ — đây là nội dung học).

**Phân biệt người vs chủ đề/nơi chốn:** đứng sau `trường/chợ/bệnh viện/tàu/sông...` → nơi chốn (giữ).
Được gọi trực tiếp (`X ơi`, `Chào X`) hoặc làm chủ ngữ hành động cá nhân (`X là/làm/thích...`) → người (thay).

- VD: `Lý Thường Kiệt là phi công` → `Quân là phi công` (thay).
- VD: `Hiệp Hòa làm ở viện nghiên cứu` → `Long làm ở viện nghiên cứu` (địa danh làm người → thay).
- VD (Lịch sử): `Lê Lợi ơi, Trần Hưng Đạo là ai?` → `Nam ơi, Trần Hưng Đạo là ai?` (đổi người ĐƯỢC GỌI, giữ chủ đề).

## B. Viết hoa tên riêng lịch sử
Bản gốc domain Lịch sử hay để chữ thường: `ngô quyền`, `sơn tinh`, `lê lợi`.
**Sửa:** viết hoa đúng danh từ riêng → `Ngô Quyền`, `Sơn Tinh`, `Lê Lợi`.

## C. Xưng hô / lặp tên (văn nói)
Văn nói chỉ nêu tên **1 lần ở đầu**, sau đó dùng đại từ. Lỗi: lặp nguyên tên mỗi lượt,
hoặc người trả lời tự xưng bằng tên.
**Sửa:**
- Người trả lời nói về mình → `mình` (hoặc `tôi`/`em` tùy register).
- Người hỏi gọi người kia → `bạn` (hoặc `anh/chị/cô` cho khớp lời chào).
- Nhân vật thứ ba (bạn bè) → giữ tên lần đầu, sau đó `bạn ấy`/`cô ấy`/`anh ấy`.
- **KHÔNG dùng code parity máy móc** — hội thoại lệch nhịp sẽ gán sai vai. Phải ĐỌC.
- VD: `Long làm ở viện... Long là chuyên gia. Long nghiên cứu...` → `Long làm ở viện... Mình là chuyên gia. Mình nghiên cứu...`

## D. Chia lượt nói sai
Hai người bị gộp trong MỘT lượt (thiếu `|`), hoặc tên người được gọi/xưng không nhất quán.
**Sửa:** tách đúng lượt bằng `|`; cập nhật `utterance_count`.
- VD: `Mình được điểm tốt... rõ ràng. Mình thì bị điểm kém...` → tách 2 lượt.
- Tên không nhất quán: hỏi `Chương thấy sao?` nhưng đáp `Hằng sợ hãi...` → thống nhất (đáp = `Mình...`).

## E. Nội dung phi lý / cảm xúc lệch category → SINH LẠI
Không sửa được bằng biên tập, cần sinh lại bằng `generation/gen_segment_llm.py`.
- Trộn chủ đề vô lý: soạn thảo văn bản + may vá + siêu âm thai trong 1 đoạn (gf2_hocduong_003).
- Nhân vật lịch sử làm người hội thoại trong domain Lịch sử (`Lê Lợi biết truyền thuyết con rồng cháu tiên không?`).
- Cảm xúc (`emotion`) không hợp category → sinh câu với cảm xúc khác cho phù hợp.
- Ghi các segment nhóm này vào danh sách `regenerate_list.txt` để xử lý theo mẻ.

---

## Nhật ký sửa
Mỗi segment sửa tay ghi 1 dòng ở `FIX_LOG.md`: `segment_id | loại lỗi (A–E) | tóm tắt`.
Sau khi sửa, ĐỌC LẠI cả đoạn kiểm tra tự nhiên + không phát sinh lỗi mới.
