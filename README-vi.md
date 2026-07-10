# videoautomation — bản cá nhân hóa tiếng Việt

Đây là bản tạo video tự động đã được cá nhân hóa cho người Việt, dùng giao diện và tài liệu chính bằng tiếng Việt.

## Chạy nhanh trên Windows

```powershell
git clone https://github.com/wududegw/videoautomation.git
Set-Location videoautomation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
Copy-Item config.example.toml config.toml
.\webui.bat
```

Mở:

```text
http://localhost:8501
```

## Cấu hình

Mở `config.toml` và điền API key cần dùng:

- `gemini_api_key`, `openai_api_key`, hoặc provider LLM khác.
- `pexels_api_keys`, `pixabay_api_keys`, `coverr_api_keys`, `videvo_api_keys` nếu dùng nguồn video online.
- Dùng **Local file** hoặc `material_directory` nếu bạn có sẵn clip riêng.

## Video dài

Bật **Chế độ video dài** nếu muốn tạo video 30 phút trở lên. Nên chuẩn bị nhiều clip local trong `material_directory` để hình ảnh không bị lặp.

## Bảo mật

Không commit hoặc chia sẻ `config.toml` nếu đã điền API key thật.
