# videoautomation

This repository is a Vietnamese-first, personalized video automation tool.

Primary documentation is in Vietnamese:

- [README.md](README.md)
- [README-vi.md](README-vi.md)

## Quick start

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

Open `http://localhost:8501`.

Do not commit `config.toml` after adding API keys.
