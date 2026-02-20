#!/usr/bin/env python3
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEBUG_LOG = Path.home() / "ocr_debug.log"
EDITOR_CMD = "notepad.exe"
OLLAMA_TIMEOUT_SECONDS = 180

try:
    from PIL import Image, ImageGrab
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False


def log_error(message, error_details=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
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
    if not text:
        return
    if not HAS_PYPERCLIP:
        emit_warning("pyperclip is not installed. Clipboard step skipped.")
        return
    try:
        pyperclip.copy(text)
    except Exception as e:
        log_error("Clipboard Error", str(e))
        emit_warning(f"Clipboard step failed: {e}")


def open_editor(file_path):
    try:
        subprocess.Popen([EDITOR_CMD, str(file_path)])
    except Exception as e:
        log_error("Editor Error", str(e))
        emit_warning(f"Could not open editor: {e}")


def sanitize_image(image_path):
    if not HAS_PILLOW:
        return image_path

    try:
        img = Image.open(image_path).convert("RGB")
        max_dim = 1120
        w, h = img.size
        scale = min(max_dim / w, max_dim / h, 1.0)
        new_w = max(28, int(w * scale))
        new_h = max(28, int(h * scale))
        new_w = new_w - (new_w % 28)
        new_h = new_h - (new_h % 28)
        new_w = max(28, new_w)
        new_h = max(28, new_h)

        if (new_w, new_h) != (w, h):
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        safe_path = image_path.with_suffix(".temp.jpg")
        img.save(safe_path, "JPEG", quality=100)
        return safe_path
    except Exception as e:
        log_error("Sanitization Failed", str(e))
        emit_warning("Image sanitization failed. Using original screenshot.")
        return image_path


def select_region():
    try:
        import tkinter as tk
    except Exception as e:
        emit_error(f"tkinter is required for region selection: {e}")
        return None

    result = {"bbox": None}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.25)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.title("Select OCR Region")

    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    state = {"x0": 0, "y0": 0, "rect": None}

    def on_press(event):
        state["x0"], state["y0"] = event.x, event.y
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            state["x0"],
            state["y0"],
            state["x0"],
            state["y0"],
            outline="red",
            width=2,
        )

    def on_drag(event):
        if state["rect"]:
            canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

    def on_release(event):
        x1, y1 = event.x, event.y
        left, right = sorted((state["x0"], x1))
        top, bottom = sorted((state["y0"], y1))
        if right - left > 2 and bottom - top > 2:
            result["bbox"] = (left, top, right, bottom)
        root.quit()

    def on_escape(_event):
        root.quit()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)

    root.mainloop()
    root.destroy()
    return result["bbox"]


def take_screenshot():
    if not HAS_PILLOW:
        emit_error("Pillow is required on Windows for screenshot capture.")
        return None

    emit_info("Please select region on screen...")
    bbox = select_region()
    if not bbox:
        emit_warning("Screenshot canceled.")
        return None

    try:
        img = ImageGrab.grab()
        cropped = img.crop(bbox)
    except Exception as e:
        log_error("Screenshot Error", str(e))
        emit_error(f"Failed to capture screenshot: {e}")
        return None

    pictures_dir = Path.home() / "Pictures" / "ocr"
    pictures_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = pictures_dir / f"Screenshot_{timestamp}.png"
    cropped.save(filename)
    return filename


def get_mode():
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if "table" in arg:
            return "Table Recognition"
        if "figure" in arg:
            return "Figure Recognition"
    return "Text Recognition"


def ensure_ollama_daemon():
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
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:
        emit_error(f"Failed to start ollama daemon: {e}")
        return False

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
    try:
        result = subprocess.run(
            ["ollama", "show", "glm-ocr"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            emit_error("Model 'glm-ocr' is not ready in Ollama.")
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
    process = subprocess.Popen(
        ["ollama", "run", "glm-ocr"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
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


def run():
    mode = get_mode()
    emit_info(f"Mode: {mode}")

    original_path = take_screenshot()
    if not original_path:
        emit_warning("No screenshot captured. OCR aborted.")
        return 1

    emit_info(f"Screenshot saved: {original_path}")
    emit_info("Running glm-ocr...")

    processing_path = sanitize_image(original_path)
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

        raw_output = stdout
        clean_output = re.sub(r"Added image '.*?'", "", raw_output).strip()
        if not clean_output:
            emit_warning("Model returned no text.")
            return 3

        output_file = original_path.with_suffix(".txt")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(clean_output)
        emit_info(f"Saved output file: {output_file}")

        copy_to_clipboard(clean_output)
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
