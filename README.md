# OCR Cleaner

Simple desktop OCR utility for Linux with:
- a PyQt GUI launcher (`ocr_gui.py`)
- a backend OCR pipeline (`ocr_only.py`)

It captures a screen region, sends it to `glm-ocr` via Ollama, saves text output, copies it to clipboard, and optionally opens the text file in an editor.

## Scripts

- `ocr_gui.py`
  - Main GUI app.
  - Shows status/errors in the app log panel.
  - Launches backend through `zsh -lc` so GUI run uses terminal-like environment.
- `ocr_only.py`
  - OCR backend pipeline.
  - Handles screenshot capture, image sanitization (Pillow), Ollama call, output save, clipboard copy, and editor open.
- `ocr_gui_windows.py`
  - Windows GUI launcher.
  - Streams backend logs directly into the main log panel.
- `ocr_only_windows.py`
  - Windows backend pipeline.
  - Uses a built-in region selector (Tk overlay) + Pillow screen grab.
  - Uses `pyperclip` for clipboard and `notepad.exe` for opening output.

## Requirements

## Linux system dependencies
- `ollama` (daemon + CLI)
- `spectacle` (region screenshot capture)
- `wl-copy` (Wayland clipboard)
- `kwrite` (optional; used to open output `.txt`)

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
- `zsh` should be available (GUI uses `zsh -lc` when launching backend).
- Writes screenshots and OCR output under:
  - `~/Pictures/ocr/`
- Writes debug log to:
  - `~/ocr_debug.log`

## Windows environment

- Windows 10/11
- Ollama installed and available in PATH
- Python 3.10+ with:
  - `PyQt6`
  - `Pillow`
  - `pyperclip`
- `tkinter` available (bundled with most standard Python installs)
- Output files are saved to:
  - `%USERPROFILE%\Pictures\ocr\`

## Run

Run GUI:

```bash
python3 ocr_gui.py
```

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
```

Run Windows backend directly:

```powershell
python ocr_only_windows.py
python ocr_only_windows.py text
python ocr_only_windows.py table
python ocr_only_windows.py figure
```

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

## Troubleshooting

- Stuck at OCR step:
  - ensure daemon is up: `ollama ps`
  - ensure model exists: `ollama show glm-ocr`
- No screenshot:
  - ensure `spectacle` is installed and region selection is not canceled.
- No clipboard output:
  - ensure `wl-copy` exists.
- Editor not opening:
  - install `kwrite` or change `EDITOR_CMD` in `ocr_only.py`.
- Windows region selector not opening:
  - verify `tkinter` is present in your Python installation.
