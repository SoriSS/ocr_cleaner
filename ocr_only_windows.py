#!/usr/bin/env python3
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
import tempfile

DEBUG_LOG = Path.home() / "ocr_debug.log"
OUTPUT_FILE = Path.home() / "Pictures" / "ocr" / "ocr_result.txt"
EDITOR_CMD = "notepad.exe"
OLLAMA_TIMEOUT_SECONDS = 420
PRIMARY_MODEL_NAME = "glm-ocr"
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

try:
    from PIL import Image, ImageGrab
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

SNIPPING_TIMEOUT_SECONDS = 30

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


def ensure_output_directory():
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        log_error("Output Directory Error", str(e))
        emit_error(f"Could not create output folder: {OUTPUT_FILE.parent}")
        emit_error(str(e))
        return False


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


def launch_snipping_tool():
    launch_attempts = [
        ["snippingtool", "/clip"],
        ["explorer.exe", "ms-screenclip:"],
    ]

    for command in launch_attempts:
        try:
            subprocess.Popen(command)
            return True
        except FileNotFoundError:
            continue
        except Exception as e:
            log_error("Snipping Tool Launch Error", f"{command}: {e}")

    return False


def get_clipboard_image_signature(clipboard_data):
    if not hasattr(clipboard_data, "resize"):
        return None

    try:
        preview = clipboard_data.convert("RGB").resize((16, 16))
        return (preview.size, preview.tobytes())
    except Exception:
        return None


def wait_for_clipboard_image(previous_signature=None, timeout_seconds=SNIPPING_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            clipboard_data = ImageGrab.grabclipboard()
        except Exception as e:
            log_error("Clipboard Capture Error", str(e))
            emit_error(f"Failed to read clipboard image: {e}")
            return None

        if hasattr(clipboard_data, "save"):
            current_signature = get_clipboard_image_signature(clipboard_data)
            if previous_signature is not None and current_signature == previous_signature:
                time.sleep(0.2)
                continue

            tmp = tempfile.NamedTemporaryFile(prefix="ocr_capture_", suffix=".png", delete=False)
            filename = Path(tmp.name)
            tmp.close()
            clipboard_data.save(filename)
            return filename

        time.sleep(0.2)

    return None


def take_screenshot():
    if not HAS_PILLOW:
        emit_error("Pillow is required on Windows for screenshot capture.")
        return None

    emit_info("Opening Snipping Tool. Select a region to continue...")
    if not launch_snipping_tool():
        emit_error("Could not start the Windows Snipping Tool.")
        return None

    previous_signature = None
    try:
        previous_signature = get_clipboard_image_signature(ImageGrab.grabclipboard())
    except Exception:
        previous_signature = None

    filename = wait_for_clipboard_image(previous_signature=previous_signature)
    if not filename:
        emit_warning("No snip was captured from the clipboard.")
        return None

    return filename

def parse_cli_args():
    args = sys.argv[1:]
    mode = "Text Recognition"
    pdf_path = None
    output_path = None

    idx = 0
    while idx < len(args):
        arg = args[idx]
        lowered = arg.lower()

        if lowered in {"text", "handwritten", "table", "figure"}:
            if lowered == "table":
                mode = "Table Recognition"
            elif lowered == "figure":
                mode = "Figure Recognition"
            elif lowered == "handwritten":
                mode = "Handwritten Recognition"
            idx += 1
            continue

        if lowered == "pdf":
            if idx + 1 >= len(args):
                emit_error("Missing PDF path after 'pdf'.")
                return None
            pdf_path = Path(args[idx + 1]).expanduser()
            idx += 2
            continue

        if lowered == "--output":
            if idx + 1 >= len(args):
                emit_error("Missing output path after '--output'.")
                return None
            output_path = Path(args[idx + 1]).expanduser()
            idx += 2
            continue

        if lowered.endswith(".pdf") and pdf_path is None:
            pdf_path = Path(arg).expanduser()
            idx += 1
            continue

        emit_error(f"Unrecognized argument: {arg}")
        return None

    return mode, pdf_path, output_path


def build_prompt(mode, image_path):
    image_path = str(image_path)
    if mode == "Table Recognition":
        return f"Extract table content from this image as HTML table: {image_path}"
    if mode == "Figure Recognition":
        return f"Extract all visible text from this figure image: {image_path}"
    return f"Extract all visible text from this image: {image_path}"


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


def check_ollama_model(model_name):
    try:
        result = subprocess.run(
            ["ollama", "show", model_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            emit_error(f"Model '{model_name}' is not ready in Ollama.")
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


def run_ollama(model_name, prompt, timeout_seconds):
    process = subprocess.Popen(
        ["ollama", "run", model_name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    stop_event = threading.Event()

    def progress_heartbeat():
        elapsed_seconds = 0
        while not stop_event.wait(5):
            elapsed_seconds += 5
            emit_info(f"OCR still running... {elapsed_seconds}s elapsed.")

    heartbeat_thread = threading.Thread(target=progress_heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        stdout, stderr = process.communicate(
            input=prompt + "\n",
            timeout=timeout_seconds,
        )
        stop_event.set()
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        stop_event.set()
        emit_error(f"Ollama timed out after {timeout_seconds}s. Terminating process.")
        try:
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            process.kill()
            stdout, stderr = process.communicate()
        return 124, stdout or "", stderr or "", True


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


def find_pdftoppm():
    for command_name in ("pdftoppm.exe", "pdftoppm"):
        resolved = shutil.which(command_name)
        if resolved:
            return resolved

    candidate_paths = [
        Path(r"C:\Program Files\poppler\Library\bin\pdftoppm.exe"),
        Path(r"C:\Program Files\poppler\bin\pdftoppm.exe"),
        Path(r"C:\Program Files (x86)\poppler\Library\bin\pdftoppm.exe"),
        Path(r"C:\Program Files (x86)\poppler\bin\pdftoppm.exe"),
        Path.home() / "scoop" / "apps" / "poppler" / "current" / "Library" / "bin" / "pdftoppm.exe",
        Path.home() / "scoop" / "apps" / "poppler" / "current" / "bin" / "pdftoppm.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    ]

    for path in candidate_paths[:-1]:
        if path.exists():
            return str(path)

    winget_root = candidate_paths[-1]
    if winget_root.exists():
        for package_dir in sorted(winget_root.glob("*poppler*")):
            matches = sorted(package_dir.rglob("pdftoppm.exe"))
            if matches:
                return str(matches[0])

    return None


def save_output_text(output_text, output_path):
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log_error("Output Directory Error", str(e))
        emit_error(f"Could not create output folder: {output_path.parent}")
        emit_error(str(e))
        return 4

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_text)
    except Exception as e:
        log_error("Output File Error", str(e))
        emit_error(f"Could not write output file: {output_path}")
        emit_error(str(e))
        return 4

    emit_info(f"Saved output file: {output_path}")
    return 0


def extract_text_from_image(mode, image_path, timeout_seconds):
    prompt = build_prompt(mode, image_path)
    emit_info(f"Model prompt image: {image_path}")
    emit_info(f"Waiting for OCR result (timeout: {timeout_seconds}s)...")
    emit_info("The first OCR run can take longer while glm-ocr starts up.")
    return_code, stdout, stderr, timed_out = run_ollama(prompt, timeout_seconds)

    if timed_out:
        retry_timeout = max(timeout_seconds, 600)
        emit_warning("OCR timed out during startup. Retrying once...")
        emit_info(f"Retrying OCR (timeout: {retry_timeout}s)...")
        return_code, stdout, stderr, timed_out = run_ollama(prompt, retry_timeout)

    if return_code != 0:
        err_msg = (stderr or "").strip()
        log_error(f"Ollama Failed ({return_code})", err_msg)
        if timed_out:
            emit_error("Model execution timed out.")
        else:
            emit_error("Model failed. Check ~/ocr_debug.log")
        if err_msg:
            emit_error(err_msg)
        return return_code, None

    raw_output = stdout
    clean_output = re.sub(r"Added image '.*?'", "", raw_output).strip()
    clean_output = apply_table_styling(mode, clean_output)
    if not clean_output:
        emit_warning("Model returned no text.")
        return 3, None

    return 0, clean_output


def render_pdf_to_images(pdf_path):
    pdftoppm_cmd = find_pdftoppm()
    if not pdftoppm_cmd:
        emit_error("Missing dependency: pdftoppm")
        emit_error("Install Poppler for Windows. Example: winget install oschwartz10612.Poppler or scoop install poppler")
        emit_error("If Poppler is already installed, ensure pdftoppm.exe is on PATH.")
        return None, None
    emit_info(f"Using pdftoppm: {pdftoppm_cmd}")
    emit_info("Rendering PDF pages to images...")

    temp_dir = Path(tempfile.mkdtemp(prefix="ocr_pdf_"))
    output_prefix = temp_dir / "page"
    stderr_lines = []
    stop_event = threading.Event()

    def read_progress(stream):
        while not stop_event.is_set():
            line = stream.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            stderr_lines.append(line)
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                emit_info(f"Rendered PDF page {parts[0]}/{parts[1]}")
            else:
                emit_info(f"pdftoppm: {line}")

    def emit_heartbeat():
        elapsed_seconds = 0
        while not stop_event.wait(5):
            elapsed_seconds += 5
            emit_info(f"PDF rendering still running... {elapsed_seconds}s elapsed.")

    try:
        process = subprocess.Popen(
            [pdftoppm_cmd, "-progress", "-png", str(pdf_path), str(output_prefix)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        progress_thread = threading.Thread(target=read_progress, args=(process.stderr,), daemon=True)
        heartbeat_thread = threading.Thread(target=emit_heartbeat, daemon=True)
        progress_thread.start()
        heartbeat_thread.start()
        try:
            stdout, _ = process.communicate(timeout=max(OLLAMA_TIMEOUT_SECONDS, 900))
        finally:
            stop_event.set()
        progress_thread.join(timeout=1)
        heartbeat_thread.join(timeout=1)
    except subprocess.TimeoutExpired:
        stop_event.set()
        try:
            process.kill()
            process.communicate(timeout=5)
        except Exception:
            pass
        emit_error("PDF rendering timed out.")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None
    except Exception as e:
        stop_event.set()
        log_error("PDF rendering failed", str(e))
        emit_error(f"Could not render PDF pages: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None

    if process.returncode != 0:
        details = ("\n".join(stderr_lines) or (stdout or "")).strip()
        log_error("pdftoppm failed", details)
        emit_error("Could not render PDF pages.")
        if details:
            emit_error(details)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None

    image_paths = sorted(temp_dir.glob("page-*.png"))
    if not image_paths:
        emit_error("No pages were rendered from the PDF.")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None

    return temp_dir, image_paths


def build_pdf_output_path(pdf_path, output_path):
    if output_path is not None:
        return output_path
    return OUTPUT_FILE.with_name(f"{pdf_path.stem}_ocr.txt")


def ocr_pdf_to_text(pdf_path, mode, output_path=None):
    pdf_path = Path(pdf_path).expanduser()
    if not pdf_path.exists():
        emit_error(f"PDF file not found: {pdf_path}")
        return 2
    if not pdf_path.is_file():
        emit_error(f"PDF path is not a file: {pdf_path}")
        return 2

    emit_info(f"PDF source: {pdf_path}")
    render_dir, rendered_pages = render_pdf_to_images(pdf_path)
    if not rendered_pages:
        return 2

    emit_info(f"Rendered {len(rendered_pages)} page(s) from PDF.")
    page_outputs = []
    try:
        for page_index, original_path in enumerate(rendered_pages, start=1):
            emit_info(f"OCR page {page_index}/{len(rendered_pages)}")
            processing_path = sanitize_image(original_path)
            try:
                return_code, clean_output = extract_text_from_image(
                    mode, processing_path, OLLAMA_TIMEOUT_SECONDS
                )
            finally:
                try:
                    if processing_path != original_path and processing_path.exists():
                        os.remove(processing_path)
                except Exception:
                    pass

            if return_code != 0:
                return return_code

            page_outputs.append(f"===== Page {page_index} =====\n{clean_output}")

        final_output = "\n\n".join(page_outputs)
        final_output_path = build_pdf_output_path(pdf_path, output_path)
        save_code = save_output_text(final_output, final_output_path)
        if save_code != 0:
            return save_code

        copy_to_clipboard(final_output)
        open_editor(final_output_path)
        emit_success(f"{mode} PDF OCR finished successfully.")
        return 0
    finally:
        shutil.rmtree(render_dir, ignore_errors=True)


def run():
    mode = get_mode()
    emit_info(f"Mode: {mode}")

    original_path = take_screenshot()
    if not original_path:
        emit_warning("No screenshot captured. OCR aborted.")
        return 1

    emit_info(f"Screenshot captured: {original_path}")
    emit_info("Running glm-ocr...")

    processing_path = sanitize_image(original_path)
    prompt = build_prompt(mode, processing_path)
    emit_info(f"Model prompt image: {processing_path}")

    try:
        if not shutil.which("ollama"):
            emit_error("Missing dependency: ollama")
            return 2

        if not ensure_ollama_daemon():
            return 2

        if not check_ollama_model(model_name):
            return 2

        emit_info(f"Waiting for OCR result (timeout: {OLLAMA_TIMEOUT_SECONDS}s)...")
        emit_info("The first OCR run can take longer while glm-ocr starts up.")
        return_code, stdout, stderr, timed_out = run_ollama(prompt, OLLAMA_TIMEOUT_SECONDS)

        # First-time model startup can be slow on Windows; retry once automatically.
        if timed_out:
            retry_timeout = max(OLLAMA_TIMEOUT_SECONDS, 600)
            emit_warning("OCR timed out during startup. Retrying once...")
            emit_info(f"Retrying OCR (timeout: {retry_timeout}s)...")
            return_code, stdout, stderr, timed_out = run_ollama(prompt, retry_timeout)


        if return_code != 0:
            return return_code

        raw_output = stdout
        clean_output = re.sub(r"Added image '.*?'", "", raw_output).strip()
        clean_output = apply_table_styling(mode, clean_output)
        if not clean_output:
            emit_warning("Model returned no text.")
            return 3

        if not ensure_output_directory():
            return 4

        output_file = OUTPUT_FILE
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
    finally:
        try:
            if "processing_path" in locals() and "original_path" in locals() and processing_path != original_path and processing_path.exists():
                os.remove(processing_path)
        except Exception:
            pass
        try:
            if "original_path" in locals() and original_path.exists():
                os.remove(original_path)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(run())
