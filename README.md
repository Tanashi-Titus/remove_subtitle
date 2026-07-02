# Tool tách & xoá phụ đề video qua vmake.ai

Tool PySide6 thực hiện đúng quy trình:

1. Đầu vào là **1 video** hoặc **1 thư mục** chứa nhiều video.
2. **Tách** mỗi video thành các đoạn 4 giây (đoạn cuối lấy phần dư).
   Ví dụ video 11.8s → `0–4.0s`, `4.0–8.0s`, `8.0–11.8s`.
3. Lần lượt đưa từng đoạn lên **vmake.ai** để xoá phụ đề rồi tải về.
4. **Ghép** các đoạn đã xử lý lại theo đúng video gốc → video hoàn chỉnh không phụ đề.

> Vì sao cắt 4s? Bản miễn phí của vmake chỉ cho tải **5 giây đầu**. Mỗi đoạn ≤ 4s
> nằm gọn trong 5s preview nên tải được trọn vẹn.

---

## 1. Cài đặt

Cần **Python 3.9+** và **ffmpeg** (có cả `ffprobe`) trong PATH.

```bash
# ffmpeg:
#   Windows: tải tại https://www.gyan.dev/ffmpeg/builds/ rồi thêm vào PATH
#   macOS:   brew install ffmpeg
#   Linux:   sudo apt install ffmpeg

pip install -r requirements.txt
playwright install chromium     # tải trình duyệt cho bước vmake
```

## 2. Chạy

```bash
python app.py
```

Trong giao diện:
- Chọn **1 video** hoặc **1 thư mục**.
- Chọn **thư mục lưu kết quả**.
- Để mặc định **độ dài đoạn = 4s**.
- 3 bước (Tách / Xoá phụ đề / Ghép) bật sẵn — bạn có thể bật/tắt từng bước.
- Bấm **Bắt đầu**.

## 3. Hai chế độ vmake

- **Bán tự động (khuyên dùng):** tool tự upload từng đoạn và tự lưu file bạn tải về
  vào đúng chỗ. Bạn chỉ cần bấm trên cửa sổ trình duyệt: chọn **Text/Caption** →
  **Apply** → **Download**. Ổn định nhất vì không phụ thuộc tên nút của site.
- **Tự động (thử nghiệm):** tool thử tự bấm các nút. Nếu site đổi giao diện có thể
  không khớp — khi đó chỉnh các hằng `*_BUTTON_TEXTS` trong `vmake_client.py`.

## 3b. Nghỉ ngẫu nhiên & lưu cấu hình

- **Nghỉ ngẫu nhiên giữa đoạn (giây):** đặt `min`–`max` (mặc định 1–50s). Sau mỗi
  đoạn, tool nghỉ một khoảng ngẫu nhiên trong khoảng này trước khi xử lý đoạn kế
  tiếp (giúp giống thao tác người, tránh bị giới hạn). Có thể bấm **Dừng** giữa lúc
  đang nghỉ.
- **Lưu cấu hình:** bấm nút **Lưu cấu hình** (hoặc tự lưu mỗi khi bấm **Bắt đầu**).
  Cấu hình ghi tại `~/.tnt_video_tool/config.json` và **tự nạp lại** khi mở app.

## 4. Thư mục kết quả

```
output/
  segments/<ten_video>/<ten_video>_000.mp4   # đoạn đã cắt
  processed/<ten_video>/<ten_video>_000.mp4  # đoạn đã xoá phụ đề
  final/<ten_video>_nosub.mp4                 # video hoàn chỉnh
  manifest.json                               # ánh xạ đoạn ↔ video gốc
```

Tool **có thể chạy lại (resume):** đoạn nào đã có file thì bỏ qua. Nếu bước tự động
vmake lỗi, bạn có thể tự xử lý thủ công rồi đặt file đã làm sạch vào đúng
`processed/<ten_video>/` với **đúng tên file**, sau đó chỉ bật bước **Ghép**.
Đoạn nào thiếu bản đã xử lý, bước ghép sẽ dùng tạm đoạn gốc.

## 5. Lưu ý

- Bước upload bám theo cấu trúc thật của vmake: tool **dò `input[type=file]`** sau khi
  trang dựng xong, nếu không có thì **bấm nút "Upload" và bắt hộp chọn file**. Nếu site
  đổi giao diện, chỉnh các hằng `*_TEXTS` / selector trong `vmake_client.py`.
- Bước vmake là phần dễ gãy nhất: phụ thuộc giao diện web của bên thứ ba (có thể
  đổi bất cứ lúc nào) và việc tự động hoá có thể trái Điều khoản sử dụng của vmake.
  Hãy dùng có trách nhiệm, ưu tiên nội dung bạn có quyền chỉnh sửa.
- Các đoạn được re-encode (H.264/AAC) để cắt chính xác theo thời gian và ghép mượt.
