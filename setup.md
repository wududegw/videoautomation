# Cài đặt videoautomation

## Windows

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

Mở `http://localhost:8501`.

## Dùng uv

```powershell
uv sync --frozen
Copy-Item config.example.toml config.toml
uv run streamlit run webui/Main.py
```

## Lưu ý

- Điền API key trong `config.toml`.
- Không đưa `config.toml` lên Git.
- Dùng Python 3.11 hoặc 3.12.
