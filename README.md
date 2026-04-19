# OCR Cleaner

Simple desktop OCR utility for Linux with:
- a PyQt GUI launcher (`ocr_gui.py`)
- a backend OCR pipeline (`ocr_only.py`)

It captures a screen region, sends it to `glm-ocr` via Ollama, saves text output, copies it to clipboard, and optionally opens the text file in an editor.

## Scripts

- `ocr_gui.py`
  - Main GUI app.
  - Shows status/errors in the app log panel.
  - Launches the backend with the current Python interpreter.
- `ocr_only.py`
  - OCR backend pipeline.
  - Handles screenshot capture, image sanitization (Pillow), Ollama call, output save, clipboard copy, and editor open.
- `ocr_gui_windows.py`
  - Windows GUI launcher.
  - Streams backend logs directly into the main log panel.
- `ocr_only_windows.py`
  - Windows backend pipeline.
  - Uses Windows Snipping Tool and reads the captured image from the clipboard.
  - Uses `pyperclip` for clipboard and `notepad.exe` for opening output.

## Requirements

## Linux system dependencies
- `ollama` (daemon + CLI)
- `spectacle` (region screenshot capture)
- `wl-copy` (Wayland clipboard)
- `kwrite` (optional; used to open output `.txt`)
- `pdftoppm` from `poppler-utils` (required for PDF OCR)

Install Linux system dependencies as needed:

```bash
# Fedora
sudo dnf install ollama spectacle wl-clipboard kwrite poppler-utils

# Debian / Ubuntu
sudo apt install ollama spectacle wl-clipboard kwrite poppler-utils
```

## Python dependencies
- Python `3.10+`
- `PyQt6`
- `Pillow` (recommended; script works without it, but sanitization is disabled)
- `pyperclip` (required for clipboard on Windows backend)

Install Python deps:

```bash
python3 -m pip install --user PyQt6 Pillow pyperclip
```

Install/load model:

```bash
ollama pull glm-ocr
```

Start Ollama daemon (if not already running):

```bash
ollama serve
```

## Environment

- Linux desktop (tested flow assumes Wayland tools like `wl-copy`).
- Writes screenshots and OCR output under:
  - `~/Pictures/ocr/`
- Writes debug log to:
  - `~/ocr_debug.log`
- Optional for desktop-launcher setups where `ollama` is not on PATH:
  - set `OLLAMA_BIN=/full/path/to/ollama`

## Windows environment

- Windows 10/11
- Ollama installed and available in PATH
- Poppler installed for PDF OCR (`pdftoppm.exe`)
- Python 3.10+ with:
  - `PyQt6`
  - `Pillow`
  - `pyperclip`
- Output files are saved to:
  - `%USERPROFILE%\Pictures\ocr\`

Install Windows PDF dependency if you want `PDF OCR` in the GUI or `pdf` mode in the backend:

```powershell
winget install oschwartz10612.Poppler
# or
scoop install poppler
```

After install, restart your terminal or app launcher so `pdftoppm.exe` is visible to the process.

## Run

Run GUI:

```bash
python3 ocr_gui.py
```

GUI PDF flow:

1. Start `python3 ocr_gui.py`
2. Click `PDF OCR`
3. Pick a `.pdf` file in the file chooser
4. Wait for each page to be rendered and OCR'd
5. Read the saved output file shown in the log

Run Windows GUI:

```powershell
python ocr_gui_windows.py
```

Run backend directly:

```bash
python3 ocr_only.py
python3 ocr_only.py text
python3 ocr_only.py table
python3 ocr_only.py figure
python3 ocr_only.py pdf /path/to/file.pdf
python3 ocr_only.py table pdf /path/to/file.pdf
python3 ocr_only.py pdf /path/to/file.pdf --output /path/to/result.txt
```

Run Windows backend directly:

```powershell
python ocr_only_windows.py
python ocr_only_windows.py text
python ocr_only_windows.py table
python ocr_only_windows.py figure
python ocr_only_windows.py pdf C:\path\to\file.pdf
python ocr_only_windows.py table pdf C:\path\to\file.pdf
python ocr_only_windows.py pdf C:\path\to\file.pdf --output C:\path\to\result.txt
```

Windows PDF OCR requires `pdftoppm` from Poppler to be installed and available on `PATH`.

## Run GUI from an app launcher / shortcut

Yes, you can launch the GUI from a registered app entry on Fedora and from a shortcut on Windows.

### Fedora (GNOME/KDE app menu)

Create a desktop entry:

```bash
cat > ~/.local/share/applications/glm-ocr.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=GLM OCR
Comment=Launch OCR GUI
Exec=python3 /ABSOLUTE/PATH/TO/ocr_cleaner/ocr_gui.py
Path=/ABSOLUTE/PATH/TO/ocr_cleaner
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
```

Replace `/ABSOLUTE/PATH/TO/ocr_cleaner` with your real project path.

Optional (some desktops require executable bit):

```bash
chmod +x ~/.local/share/applications/glm-ocr.desktop
```

Then open your app menu and search for `GLM OCR` (or run `gtk-launch glm-ocr`).

### Windows (Desktop or Start Menu shortcut)

Use a real `.lnk` shortcut (not a `.bat`) and set icon explicitly.

From PowerShell:

```powershell
$Project = "C:\path\to\ocr_cleaner"
$Pythonw = (Get-Command pythonw.exe).Source
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\GLM OCR.lnk")
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = "`"$Project\ocr_gui_windows.py`""
$Shortcut.WorkingDirectory = $Project
$Shortcut.IconLocation = "$Project\assets\glm_ocr.ico,0"
$Shortcut.Save()
```

Then:
- Right click `GLM OCR.lnk` -> `Pin to taskbar`.
- If you pinned an older shortcut before, unpin it first and pin this new one.
- `assets/glm_ocr.ico` is included in this repo and used by the app + shortcut.

You can rename the shortcut to `GLM OCR` and pin it to Start/Taskbar.

## Typical flow

1. Start GUI.
2. Click recognition mode.
3. Select screen region.
4. Wait for OCR completion.
5. Result is:
   - saved as `.txt` next to screenshot in `~/Pictures/ocr/`
   - copied to clipboard (if `wl-copy` exists)
   - opened in `kwrite` (if installed)

## PDF OCR

The Linux backend can OCR a PDF file page by page and save the combined result into a text file.

- Default output path: `~/Pictures/ocr/<pdf-name>_ocr.txt`
- Optional mode selection still works: `text`, `table`, `figure`
- Each page is separated in the output file with a `===== Page N =====` header

### How to OCR a PDF file

1. Make sure `ollama` is running and `glm-ocr` is installed.
2. Install `poppler-utils` so `pdftoppm` is available.

```bash
# Fedora
sudo dnf install poppler-utils

# Debian / Ubuntu
sudo apt install poppler-utils
```

3. Run:

```bash
python3 ocr_only.py pdf /path/to/file.pdf
```

4. Wait for each page to be rendered and OCR'd in sequence.
5. Read the final text file at:

```text
~/Pictures/ocr/<pdf-name>_ocr.txt
```

Optional commands:

```bash
# Force table extraction mode for all pages
python3 ocr_only.py table pdf /path/to/file.pdf

# Choose a custom output text file
python3 ocr_only.py pdf /path/to/file.pdf --output /path/to/result.txt
```

## Troubleshooting

- Stuck at OCR step:
  - ensure daemon is up: `ollama ps`
  - ensure model exists: `ollama show glm-ocr`
- OCR is unexpectedly slow / CPU-only after Fedora update:
  - check processor column: `ollama ps` (look for `GPU` vs `CPU`)
  - restart daemon: `pkill -f "ollama serve" && ollama serve`
  - if still CPU, reinstall/repair your GPU driver stack after the OS update.
- No screenshot:
  - ensure `spectacle` is installed and region selection is not canceled.
- PDF OCR fails before OCR starts:
  - ensure `pdftoppm` is installed (`poppler-utils` on Fedora/Debian-family distros).
- No clipboard output:
  - ensure `wl-copy` exists.
- Editor not opening:
  - install `kwrite` or change `EDITOR_CMD` in `ocr_only.py`.
- Windows region selector not opening:
  - ensure `snippingtool.exe` is available and clipboard access is not blocked.
