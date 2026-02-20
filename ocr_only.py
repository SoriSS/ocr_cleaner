#!/usr/bin/env python3
import subprocess
import sys
import re
import shutil
import os
import signal
from pathlib import Path
from datetime import datetime

# --- CONFIGURATION ---
CLIPBOARD_CMD = 'wl-copy' 
DEBUG_LOG = Path.home() / "ocr_debug.log"
EDITOR_CMD = 'kwrite'  # The text editor to open
OLLAMA_TIMEOUT_SECONDS = 180
MODEL_NAME = "glm-ocr"
TABLE_STYLE_BLOCK = """<style>
table {
  width: auto;
  max-width: 100%;
  display: inline-table;
  border-collapse: collapse;
  font-family: sans-serif;
  font-size: 14px;
}

th, td {
  padding: 8px 10px;
  border: 1px solid #ddd;
  text-align: left;
  vertical-align: top;
  max-width: 48ch;
  white-space: normal;
  overflow-wrap: anywhere;
}

th {
  background: #f5f5f5;
  font-weight: 600;
}
</style>
"""

# Check for Pillow (Image processing library)
try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

def log_error(message, error_details=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG, "a") as f:
        f.write(f"[{timestamp}] {message}\n")
        if error_details:
            f.write(f"DETAILS:\n{error_details}\n")
        f.write("-" * 40 + "\n")

def emit_info(message):
    print(f"[INFO] {message}", flush=True)

def emit_warning(message):
    print(f"[WARNING] {message}", flush=True)

def emit_success(message):
    print(f"[SUCCESS] {message}", flush=True)

def emit_error(message):
    print(f"[ERROR] {message}", file=sys.stderr, flush=True)

def copy_to_clipboard(text):
    if not text: return
    try:
        if not shutil.which('wl-copy'):
            log_error("Clipboard Error", "wl-copy not found")
            emit_warning("wl-copy not found. Clipboard step skipped.")
            return
        process = subprocess.Popen(CLIPBOARD_CMD.split(), stdin=subprocess.PIPE)
        process.communicate(input=text.encode('utf-8'))
    except Exception as e:
        log_error("Clipboard Exception", str(e))
        emit_warning(f"Clipboard step failed: {e}")

def open_editor(file_path):
    """Opens the text file in KWrite without blocking the script."""
    try:
        subprocess.Popen([EDITOR_CMD, str(file_path)])
    except FileNotFoundError:
        emit_warning(f"{EDITOR_CMD} not found. File saved but not opened.")
    except Exception as e:
        log_error("Editor Error", str(e))
        emit_warning(f"Could not open {EDITOR_CMD}: {e}")

def sanitize_image(image_path):
    """
    Sanitizes image to prevent GGML_ASSERT crashes (fix for text-only PDFs).
    Ensures RGB format and aligns dimensions to multiples of 28.
    """
    if not HAS_PILLOW:
        return image_path

    try:
        img = Image.open(image_path)
        img = img.convert("RGB")
        
        # Max dimension to prevent memory errors
        max_dim = 1120 
        w, h = img.size
        
        # Calculate scale
        scale = min(max_dim / w, max_dim / h, 1.0)
        new_w = int(w * scale)
        new_h = int(h * scale)

        # Force dimensions to be multiples of 28 (Patch alignment)
        new_w = new_w - (new_w % 28)
        new_h = new_h - (new_h % 28)

        # Prevent 0px images
        new_w = max(28, new_w)
        new_h = max(28, new_h)

        if new_w != w or new_h != h:
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        safe_path = image_path.with_suffix('.temp.jpg')
        img.save(safe_path, "JPEG", quality=100)
        
        return safe_path
        
    except Exception as e:
        log_error("Sanitization Failed", str(e))
        emit_warning("Image sanitization failed. Using original screenshot.")
        return image_path

def take_screenshot():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pictures_dir = Path.home() / "Pictures" / "ocr"
    pictures_dir.mkdir(parents=True, exist_ok=True)
    filename = pictures_dir / f"Screenshot_{timestamp}.png"
    
    cmd = ["spectacle", "-r", "-b", "-n", "-o", str(filename)]

    try:
        if not shutil.which("spectacle"):
            emit_error("Missing dependency: spectacle")
            return None
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
        if filename.exists():
            return filename
        return None
    except subprocess.CalledProcessError:
        emit_warning("Screenshot canceled or failed.")
        return None

def get_mode():
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if 'table' in arg: return "Table Recognition"
        if 'figure' in arg: return "Figure Recognition"
    return "Text Recognition"

def ensure_ollama_daemon():
    """Ensure Ollama daemon is reachable; try starting it once if needed."""
    def can_connect():
        try:
            result = subprocess.run(
                ["ollama", "ps"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=8,
            )
            return result.returncode == 0, (result.stderr or "").strip()
        except Exception as e:
            return False, str(e)

    ok, err = can_connect()
    if ok:
        return True

    emit_warning("Ollama daemon is not reachable. Trying to start it...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        emit_error(f"Failed to start ollama daemon: {e}")
        return False

    # Quick retry window
    for _ in range(10):
        ok, err = can_connect()
        if ok:
            emit_info("Ollama daemon is now reachable.")
            return True
        try:
            import time
            time.sleep(0.5)
        except Exception:
            break

    emit_error("Ollama daemon is still not reachable.")
    if err:
        emit_error(err)
    return False

def check_ollama_model():
    """Fast preflight so we fail clearly before long OCR execution."""
    try:
        result = subprocess.run(
            ["ollama", "show", MODEL_NAME],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            emit_error(f"Model '{MODEL_NAME}' is not ready in Ollama.")
            if err:
                emit_error(err)
            return False
        return True
    except subprocess.TimeoutExpired:
        emit_error("Timeout while checking model availability in Ollama.")
        return False
    except Exception as e:
        emit_error(f"Failed to check model availability: {e}")
        return False

def run_ollama(prompt):
    """
    Runs Ollama with a hard timeout so GUI flow cannot hang forever.
    Returns (returncode, stdout, stderr, timed_out).
    """
    process = subprocess.Popen(
        ["ollama", "run", MODEL_NAME],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        # Feed prompt explicitly and close stdin to avoid interactive-mode hangs.
        stdout, stderr = process.communicate(
            input=prompt + "\n",
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        emit_error(f"Ollama timed out after {OLLAMA_TIMEOUT_SECONDS}s. Terminating process.")
        try:
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            process.kill()
            stdout, stderr = process.communicate()
        return 124, stdout or "", stderr or "", True

def detect_model_processor():
    """
    Best-effort check for the loaded model processor from `ollama ps`.
    Returns "GPU", "CPU", "UNKNOWN", or None if not available.
    """
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=8,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("name"):
            continue
        if MODEL_NAME not in stripped:
            continue
        upper = stripped.upper()
        if "GPU" in upper:
            return "GPU"
        if "CPU" in upper:
            return "CPU"
        return "UNKNOWN"
    return None

def emit_processor_diagnostics():
    processor = detect_model_processor()
    if processor == "GPU":
        emit_info("Ollama processor: GPU")
        return
    if processor == "CPU":
        emit_warning("Ollama processor: CPU (slow).")
        emit_warning("Fedora fix: restart daemon and re-check with `ollama ps`.")
        emit_warning("Try: `pkill -f \"ollama serve\" && ollama serve`")
        emit_warning("If still CPU, verify GPU drivers/toolkit after the Fedora update.")
        return
    if processor == "UNKNOWN":
        emit_warning("Ollama processor is active but could not be parsed from `ollama ps`.")
        return
    emit_warning("Could not read `ollama ps` processor status.")

def apply_table_styling(mode, output_text):
    if mode != "Table Recognition":
        return output_text

    lower_output = output_text.lower()
    if "<table" not in lower_output:
        return output_text

    styled_output = re.sub(
        r"<table(\s[^>]*)?>",
        r'<div style="overflow-x:auto;"><table\1>',
        output_text,
        flags=re.IGNORECASE,
    )
    styled_output = re.sub(
        r"</table>",
        "</table></div>",
        styled_output,
        flags=re.IGNORECASE,
    )

    if "<style" in lower_output:
        return styled_output
    return f"{TABLE_STYLE_BLOCK}\n{styled_output}"

def run():
    mode = get_mode()
    emit_info(f"Mode: {mode}")
    
    # 1. Screenshot
    original_path = take_screenshot()
    if not original_path:
        emit_warning("No screenshot captured. OCR aborted.")
        return 1

    emit_info(f"Screenshot saved: {original_path}")
    emit_info(f"Running {MODEL_NAME}...")

    # 2. Sanitize
    processing_path = sanitize_image(original_path)

    # 3. Prompt Ollama
    prompt = f"{mode}: {processing_path}"
    emit_info(f"Model prompt image: {processing_path}")
    
    try:
        if not shutil.which("ollama"):
            emit_error("Missing dependency: ollama")
            return 2

        if not ensure_ollama_daemon():
            return 2

        if not check_ollama_model():
            return 2

        emit_info(f"Waiting for OCR result (timeout: {OLLAMA_TIMEOUT_SECONDS}s)...")
        return_code, stdout, stderr, timed_out = run_ollama(prompt)
        emit_processor_diagnostics()

        # Cleanup temp file
        if processing_path != original_path and processing_path.exists():
            os.remove(processing_path)

        if return_code != 0:
            err_msg = (stderr or "").strip()
            log_error(f"Ollama Failed ({return_code})", err_msg)
            if timed_out:
                emit_error("Model execution timed out.")
            else:
                emit_error("Model failed. Check ~/ocr_debug.log")
            if err_msg:
                emit_error(err_msg)
            return return_code

        # 4. Process Output
        raw_output = stdout
        clean_output = re.sub(r"Added image '.*?'", "", raw_output).strip()
        clean_output = apply_table_styling(mode, clean_output)

        if not clean_output:
            emit_warning("Model returned no text.")
            return 3

        # 5. Save to File
        output_file = original_path.with_suffix('.txt')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(clean_output)
        emit_info(f"Saved output file: {output_file}")

        # 6. Copy to Clipboard
        copy_to_clipboard(clean_output)
        
        # 7. Open in KWrite
        open_editor(output_file)

        emit_success(f"{mode} finished successfully.")
        return 0

    except Exception as e:
        log_error("Script Error", str(e))
        emit_error(f"Unexpected error: {e}")
        emit_error("Check ~/ocr_debug.log")
        return 99

if __name__ == "__main__":
    sys.exit(run())
