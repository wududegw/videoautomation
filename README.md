# videoautomation — Tự động hóa tạo video tiếng Việt

`videoautomation` là bộ công cụ tạo video tự động đã được cá nhân hóa cho nhu cầu làm nội dung tiếng Việt.

## Tính năng chính

- Giao diện WebUI tiếng Việt.
- Tạo kịch bản video bằng AI/LLM.
- Tạo giọng đọc, phụ đề và nhạc nền.
- Ghép video tự động bằng MoviePy/ffmpeg.
- Hỗ trợ nguồn video Pexels, Pixabay, Coverr, Videvo, Mixkit và file local.
- Hỗ trợ video dài bằng pipeline chia nhỏ kịch bản/TTS.
- Cấu hình API key qua file local `config.toml`, không đưa key lên Git.

## Yêu cầu

- Python `>=3.11,<3.13`.
- Git.
- Khuyến nghị Windows PowerShell nếu chạy trên Windows.

## Clone về chạy ngay trên Windows

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

Mở trình duyệt:

```text
http://localhost:8501
```

## Cài bằng uv

```powershell
git clone https://github.com/wududegw/videoautomation.git
Set-Location videoautomation
uv sync --frozen
Copy-Item config.example.toml config.toml
uv run streamlit run webui/Main.py
```

## Cấu hình API key

Sau khi copy `config.example.toml` thành `config.toml`, điền các key bạn cần dùng:

- `gemini_api_key`, `openai_api_key` hoặc provider LLM khác.
- `pexels_api_keys`, `pixabay_api_keys`, `coverr_api_keys`, `videvo_api_keys` nếu dùng nguồn video online.
- Nếu dùng video riêng, chọn **Local file** trong WebUI hoặc cấu hình `material_directory`.

> [!IMPORTANT]
> Không commit hoặc chia sẻ `config.toml` nếu đã điền API key thật.

## Gợi ý sử dụng tiếng Việt

Trong `config.toml`:

```toml
[ui]
language = "vi"
```

Trong WebUI:

- Chọn ngôn ngữ kịch bản `vi-VN` nếu có.
- Chọn giọng đọc tiếng Việt trong phần TTS.
- Dùng video local có bản quyền để ổn định và tránh trùng lặp.

## Lỗi thường gặp

### Streamlit lỗi frontend hoặc dynamic module

Cài lại đúng dependency:

```powershell
pip install --upgrade --force-reinstall -r requirements.txt
```

Sau đó dừng app bằng `Ctrl + C` và chạy lại:

```powershell
.\webui.bat
```

### MoviePy import lỗi

Repo đã có lớp tương thích MoviePy 1.x/2.x, nhưng vẫn nên cài theo dependency cố định:

```powershell
pip install -r requirements.txt
```

## Tài liệu

- [README-vi.md](README-vi.md): hướng dẫn tiếng Việt rút gọn.
- [LONG_FORM.md](LONG_FORM.md): chế độ tạo video dài.
- [setup.md](setup.md): ghi chú cài đặt.

## Ghi chú nguồn mở

Dự án này được cá nhân hóa thành `videoautomation` từ một nền tảng mã nguồn mở tạo video tự động. License gốc được giữ trong [LICENSE](LICENSE).
