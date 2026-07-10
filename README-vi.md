# moneyphanhoang — bản Việt hóa nhanh

Đây là bản fork từ MoneyPrinterTurbo đã clone tại máy local và được tinh chỉnh để dễ dùng hơn cho người Việt.

## Chạy nhanh trên Windows

1. Cài Python 3.11 hoặc 3.12.
2. Clone repo và mở terminal tại thư mục dự án:

```powershell
git clone https://github.com/wududegw/videoautomation.git
Set-Location videoautomation
```

3. Khuyến nghị tạo môi trường ảo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
```

4. Cài dependencies bằng một trong hai cách:

```powershell
uv sync --frozen
```

Hoặc:

```powershell
pip install -r requirements.txt
```

4. Copy cấu hình mẫu:

```powershell
Copy-Item config.example.toml config.toml
```

5. Mở `config.toml` và điền API key cần dùng:

- `gemini_api_key`, `openai_api_key`, hoặc provider LLM khác.
- `pexels_api_keys`, `pixabay_api_keys`, `coverr_api_keys` nếu dùng nguồn video online.
- Nếu dùng clip của bạn, chọn **Local file** và tải file lên hoặc cấu hình `material_directory`.

6. Chạy WebUI:

```powershell
.\webui.bat
```

Hoặc:

```powershell
streamlit run webui/Main.py
```

## Gợi ý cấu hình tiếng Việt

Trong `config.toml`:

```toml
[ui]
language = "vi"
```

Trong WebUI:

- **Ngôn ngữ tạo kịch bản**: chọn `vi-VN` hoặc để tự động.
- **Giọng đọc**: chọn voice tiếng Việt nếu provider TTS có hỗ trợ.
- **Nguồn video**:
  - Pexels/Pixabay: dễ dùng, cần API key.
  - Coverr: miễn phí/cần API key.
  - Videvo: cần quyền API đối tác.
  - Mixkit: không có API chính thức, chỉ nên dùng thử.
  - Local file: ổn định nhất nếu bạn có sẵn clip bản quyền.

## Video dài

Bật **Chế độ video dài** nếu muốn tạo video 30 phút trở lên. Nên chuẩn bị nhiều clip local trong `material_directory` để video không bị lặp hình quá nhiều.

## Lưu ý API key

Không commit hoặc chia sẻ `config.toml` nếu đã điền API key thật.
