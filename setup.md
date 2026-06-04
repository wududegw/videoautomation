# MoneyPrinterTurbo (Long Edition) — Setup Guide for a New Windows Laptop

> **Purpose of this file:** This is a step-by-step runbook for setting up and running
> **moneyPrinterTurbo_Long** on a **fresh Windows laptop**. It is written so it can be handed
> to an AI coding agent (or a human) and followed top-to-bottom. Execute the steps **in order**
> and run the **verification** command after each major step before moving on.

---

## 0. Instructions for the AI agent (read first)

- Target OS: **Windows 11**, PowerShell.
- Target Python: **Python 3.11** (install it if missing — see Step 1).
- This repo folder was **copied from another Windows laptop**. The application code,
  `config.toml` (with API keys already filled in), and the `storage/` folder travel with it.
  **The installed Python libraries do NOT travel with it** — they must be reinstalled into a
  fresh environment on this machine (Steps 1–4).
- Do all work **inside the repo folder**. Use `python -m <tool>` forms (not `uv`) — `uv` has
  failed on Windows here with `uv trampoline failed to canonicalize script path`.
- After each step, run the **Verify** command and confirm the expected output before continuing.
- If a step fails, consult **Section 9 — Common Issues** before improvising.

---

## 1. Install Python 3.11 + FFmpeg + (optional) Git

### Python 3.11
Install from https://www.python.org/downloads/ (check **"Add python.exe to PATH"** during
install), or via winget:

```powershell
winget install Python.Python.3.11
```

**Verify** (open a NEW terminal first so PATH refreshes):

```powershell
py -3.11 --version
```

Expected: `Python 3.11.x`

### FFmpeg (required for all video/audio processing)

```powershell
winget install Gyan.FFmpeg
```

**Verify** (new terminal):

```powershell
ffmpeg -version
```

Expected: prints an `ffmpeg version ...` banner. If "not found", add its `bin` folder to PATH,
or set `ffmpeg_path` in `config.toml` under `[app]`.

### Git (optional — only if cloning instead of copying)

```powershell
winget install Git.Git
```

---

## 2. Place the Repo and Open It

Copy the entire `moneyPrinterTurbo_Long` folder onto this laptop (any location is fine — paths
are computed relative to the code, nothing is hard-coded to a drive letter).

```powershell
cd <path-where-you-put-it>\moneyPrinterTurbo_Long
```

Example:

```powershell
cd D:\Youtube_Automation\MoneyPrinter_Turbo\moneyPrinterTurbo_Long
```

**Verify** you are in the right place (these files must exist):

```powershell
Test-Path .\webui\Main.py, .\requirements.txt, .\config.example.toml
```

Expected: three `True` values.

> **Optional cleanup before/after copying:** the `storage\cache_videos` and `storage\tasks`
> folders can be large (downloaded stock clips + past renders). They are safe to delete to save
> space; they will be recreated on demand.

---

## 3. Create an Isolated Virtual Environment (venv)

Using a venv keeps this project's libraries self-contained and avoids polluting the system
Python.

```powershell
py -3.11 -m venv .venv
```

**Verify** the venv Python works:

```powershell
.\.venv\Scripts\python --version
```

Expected: `Python 3.11.x`

> From here on, **always use `.\.venv\Scripts\python`** to run things, so you use the venv and
> not the system Python.

---

## 4. Install Python Dependencies

Upgrade pip, then install the project requirements into the venv:

```powershell
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

- `requirements.txt` = the curated, pinned dependency list (recommended for a fresh 3.11 setup).
- `requirements-lock.txt` (also in this folder) = an exact `pip freeze` snapshot from the source
  machine (Python 3.10). Use it **only** if you need to reproduce the source environment exactly;
  for a clean 3.11 install, prefer `requirements.txt`.

**Verify** the key packages import cleanly:

```powershell
.\.venv\Scripts\python -c "import streamlit, moviepy, fastapi, uvicorn, openai, faster_whisper, edge_tts; print('all imports OK')"
```

Expected: `all imports OK`

> **If you see `ModuleNotFoundError`** for any package, re-run the `pip install -r requirements.txt`
> line above and confirm you are using `.\.venv\Scripts\python`.

---

## 5. Configure the App (`config.toml`)

If the repo was copied with its `config.toml`, it already contains the API keys — you can reuse
it. **If `config.toml` is missing**, create it from the example:

```powershell
copy config.example.toml config.toml
```

Open `config.toml` and confirm/set these under `[app]`:

```toml
[app]
video_source = "pixabay"                  # "pexels" or "pixabay"
pexels_api_keys = ["YOUR_PEXELS_KEY"]     # https://www.pexels.com/api/
pixabay_api_keys = ["YOUR_PIXABAY_KEY"]   # https://pixabay.com/api/docs/
llm_provider = "openai"                   # provider used to auto-write scripts
```

> **SECURITY — rotate keys:** if this `config.toml` was copied from another machine (or any key
> was ever shared in chat/email), **revoke and regenerate** those API keys in their dashboards,
> then paste the new values here. Treat the copied keys as compromised.

> **Voice/TTS note:** Free **Edge TTS** voices (e.g. `en-US-AriaNeural-Female`) work with no
> billing. **Gemini TTS** requires a Google Cloud project with **active billing**; otherwise it
> fails with `403 ... dunning decision is deny for project`.

---

## 6. Run the WebUI (port 508)

```powershell
.\.venv\Scripts\python -m streamlit run .\webui\Main.py --browser.gatherUsageStats=False --server.enableCORS=True --server.port=508
```

Expected console output includes:

```
Local URL: http://localhost:508
```

**Verify** in another terminal that it responds:

```powershell
try { (Invoke-WebRequest -Uri "http://127.0.0.1:508" -UseBasicParsing -TimeoutSec 15).StatusCode } catch { "DOWN" }
```

Expected: `200`

Then open **http://localhost:508** in Chrome or Edge.

> To use a different port, change `--server.port=508`. If the port is busy, either stop the old
> process (`taskkill /PID <pid> /F`) or pick another port.

---

## 7. (Optional) Run the API Server Instead

```powershell
.\.venv\Scripts\python main.py
```

API docs: `http://127.0.0.1:<listen_port>/docs` (`listen_port` defaults to `8080`, set in
`config.toml`).

---

## 8. Generate a Test Video

1. Open the WebUI at `http://localhost:508`.
2. Paste a short **script** (or enter a subject).
3. Enter **keywords** as plain, comma-separated words: `tiger, wildlife, big cats`.
4. Choose aspect ratio (`9:16` for Shorts/Reels, `16:9` for landscape), a voice, and options.
5. Click **Generate Video**.

Output appears in:

```
storage\tasks\<task_id>\   ->   combined-1.mp4  and  final-1.mp4
```

**Keyword rules (important):**
- Use plain keywords (`tiger, nature`), **not** hashtag blobs (`#TigerFacts #Wildlife`).
- Generic terms return far more stock footage than compound tags.

---

## 9. Common Issues

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'openai'` (or other) | Deps not installed into the venv | `.\.venv\Scripts\python -m pip install -r requirements.txt` |
| `py -3.11` not recognized | Python 3.11 not installed / not on PATH | Install Python 3.11, open a new terminal |
| `ffmpeg` not found | FFmpeg not on PATH | `winget install Gyan.FFmpeg`, or set `ffmpeg_path` in `config.toml` |
| `uv trampoline failed to canonicalize script path` | `uv` path bug on Windows | Use the `python -m streamlit ...` form |
| `found 0 videos for '#...'` | Keywords entered as hashtags | Use plain comma-separated keywords |
| `failed to download videos...` | Missing/invalid stock API key, or no network | Set a valid `pexels`/`pixabay` key in `config.toml` |
| `403 ... dunning decision is deny for project` (TTS) | Gemini project has no active billing | Fix Google Cloud billing, or switch to an Edge TTS voice |
| Port already in use | Old server still running | `taskkill /PID <pid> /F` or change `--server.port` |

---

## 10. (Optional) Use the RTX 3050 GPU

This laptop has an **NVIDIA RTX 3050 (4 GB)**, which — unlike a basic MX-series GPU — has a
real **NVENC** hardware encoder and usable **CUDA**. The default pipeline still runs on CPU
(decode + resize are CPU-bound), but the GPU can accelerate two stages:

1. **NVENC video encoding** (swap the CPU `libx264` encoder for `h264_nvenc`).
2. **faster-whisper subtitle transcription on CUDA** (much faster than CPU when subtitles are on).

These are **not enabled by default** and require code/config changes plus NVIDIA drivers + CUDA
runtime. Ask the AI agent to wire them up **with automatic CPU fallback** if you want to use the
GPU. Otherwise the app runs fine on CPU.

---

## Quick Start (TL;DR)

```powershell
# 1. Prereqs (new terminal afterwards)
winget install Python.Python.3.11
winget install Gyan.FFmpeg

# 2. Enter repo
cd <path>\moneyPrinterTurbo_Long

# 3. venv + deps
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt

# 4. config (reuse copied config.toml, or create it)
copy config.example.toml config.toml   # only if missing
#   -> ensure pexels/pixabay keys are set; ROTATE any reused keys

# 5. run
.\.venv\Scripts\python -m streamlit run .\webui\Main.py --browser.gatherUsageStats=False --server.enableCORS=True --server.port=508
# open http://localhost:508
```
