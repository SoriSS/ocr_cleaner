#!/usr/bin/env python3
import subprocess
import sys
import re
import shutil
import os
import signal
from pathlib import Path
from datetime import datetime
import tempfile
import time

# --- CONFIGURATION ---
CLIPBOARD_CMD = 'wl-copy' 
DEBUG_LOG = Path.home() / "ocr_debug.log"
OUTPUT_FILE = Path.home() / "Pictures" / "ocr" / "ocr_result.txt"
EDITOR_CMD = 'kwrite'  # The text editor to open
OLLAMA_TIMEOUT_SECONDS = 180
SCREENSHOT_TIMEOUT_SECONDS = 90
PRIMARY_MODEL_NAME = "glm-ocr"
FALLBACK_MODEL_NAMES = [m.strip() for m in os.environ.get("OCR_FALLBACK_MODELS", "").split(",") if m.strip()]
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
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
            if error_details:
                f.write(f"DETAILS:\n{error_details}\n")
            f.write("-" * 40 + "\n")
    except Exception:
        # Logging must never interrupt OCR execution.
        pass

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

def ensure_directory(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        log_error(f"Directory create failed: {path}", str(e))
        emit_warning(f"Could not create directory {path}: {e}")
        return False

def parse_cli_args():
    args = sys.argv[1:]
    mode = "Text Recognition"
    pdf_path = None
    output_path = None

    idx = 0
    while idx < len(args):
        arg = args[idx]
        lowered = arg.lower()

        if lowered in {"text", "table", "figure"}:
            if lowered == "table":
                mode = "Table Recognition"
            elif lowered == "figure":
                mode = "Figure Recognition"
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

def sanitize_image(image_path):
    """
    Sanitizes image to prevent GGML_ASSERT crashes while preserving OCR readability.
    Keeps dimensions compatible with the model (multiples of 28), avoids over-downscaling
    for screen text, and writes lossless PNG to reduce text artifacts.
    """
    if not HAS_PILLOW:
        return image_path

    try:
        img = Image.open(image_path)
        img = img.convert("RGB")

        # Keep higher detail for small UI fonts while still bounding giant captures.
        max_dim = 2240
        min_dim_for_text = 900
        w, h = img.size

        longest = max(w, h)
        shortest = min(w, h)
        if longest > max_dim:
            scale = max_dim / float(longest)
        elif shortest < min_dim_for_text:
            scale = min_dim_for_text / float(shortest)
        else:
            scale = 1.0

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

        safe_path = image_path.with_suffix('.temp.png')
        img.save(safe_path, "PNG")
        
        return safe_path
        
    except Exception as e:
        log_error("Sanitization Failed", str(e))
        emit_warning("Image sanitization failed. Using original screenshot.")
        return image_path

def _run_capture_command(cmd, filename, backend_name):
    emit_info(f"Starting screenshot backend: {backend_name}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCREENSHOT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        msg = f"{backend_name} timed out after {SCREENSHOT_TIMEOUT_SECONDS}s"
        log_error("Screenshot timeout", msg)
        emit_warning(msg)
        return False
    except Exception as e:
        log_error(f"{backend_name} screenshot exception", str(e))
        emit_warning(f"{backend_name} screenshot failed: {e}")
        return False

    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()

    if filename.exists() and filename.stat().st_size > 0:
        if result.returncode != 0:
            emit_warning(
                f"{backend_name} returned {result.returncode}, but screenshot file was created."
            )
        return True

    # Some backends/portals write the output file slightly after process exit.
    for _ in range(12):
        time.sleep(0.25)
        if filename.exists() and filename.stat().st_size > 0:
            emit_info(f"{backend_name} completed with delayed file write.")
            return True

    if result.returncode != 0:
        details = stderr or stdout or "no stderr/stdout"
        log_error(
            f"{backend_name} screenshot failed (exit {result.returncode})",
            details,
        )
        emit_warning(f"{backend_name} failed (exit {result.returncode}).")
        if stderr:
            emit_warning(stderr.splitlines()[-1])
        return False

    log_error(f"{backend_name} screenshot produced no file", "empty output")
    emit_warning(f"{backend_name} finished but no screenshot file was created.")
    return False

def _save_wayland_clipboard_image(filename):
    if not shutil.which("wl-paste"):
        return False
    try:
        result = subprocess.run(
            ["wl-paste", "--type", "image/png"],
            capture_output=True,
            timeout=5,
        )
    except Exception as e:
        log_error("wl-paste clipboard read failed", str(e))
        emit_warning(f"wl-paste failed: {e}")
        return False

    if result.returncode != 0 or not result.stdout:
        return False
    # PNG signature
    if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        return False

    try:
        filename.write_bytes(result.stdout)
        return filename.exists() and filename.stat().st_size > 0
    except Exception as e:
        log_error("Failed to write clipboard image", str(e))
        return False

def _run_grim_slurp(filename):
    try:
        emit_info("Starting screenshot backend: grim+slurp")
        region = subprocess.run(
            ["slurp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCREENSHOT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        emit_warning(f"slurp timed out after {SCREENSHOT_TIMEOUT_SECONDS}s")
        return False
    except Exception as e:
        emit_warning(f"slurp failed: {e}")
        return False

    geom = (region.stdout or "").strip()
    if region.returncode != 0 or not geom:
        return False

    try:
        grab = subprocess.run(
            ["grim", "-g", geom, str(filename)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCREENSHOT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        emit_warning(f"grim timed out after {SCREENSHOT_TIMEOUT_SECONDS}s")
        return False
    except Exception as e:
        emit_warning(f"grim failed: {e}")
        return False

    if grab.returncode != 0:
        stderr = (grab.stderr or "").strip()
        if stderr:
            emit_warning(stderr.splitlines()[-1])
        return False
    return filename.exists() and filename.stat().st_size > 0

def take_screenshot():
    tmp = tempfile.NamedTemporaryFile(prefix="ocr_capture_", suffix=".png", delete=False)
    filename = Path(tmp.name)
    tmp.close()

    try:
        backends = []
        is_wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        has_grim_slurp = is_wayland and shutil.which("grim") and shutil.which("slurp")

        if shutil.which("spectacle"):
            backends.append(
                ("spectacle", ["spectacle", "-r", "-b", "-n", "-o", str(filename)], "file")
            )
            backends.append(
                ("spectacle (legacy flags)", ["spectacle", "-r", "-b", "-o", str(filename)], "file")
            )
            backends.append(
                ("spectacle (--region --output)", ["spectacle", "--region", "--output", str(filename)], "file")
            )
            backends.append(
                ("spectacle (-r -o)", ["spectacle", "-r", "-o", str(filename)], "file")
            )
            backends.append(
                ("spectacle (clipboard fallback)", ["spectacle", "-r", "-b", "-n", "-c"], "clipboard")
            )

        if shutil.which("gnome-screenshot"):
            backends.append(
                ("gnome-screenshot", ["gnome-screenshot", "-a", "-f", str(filename)], "file")
            )
        if shutil.which("import"):
            # ImageMagick import can work as X11/XWayland fallback.
            backends.append(
                ("import", ["import", str(filename)], "file")
            )

        if not backends:
            emit_error("Missing screenshot dependency: install spectacle or gnome-screenshot")
            if filename.exists():
                os.remove(filename)
            return None

        if has_grim_slurp:
            if filename.exists():
                filename.unlink(missing_ok=True)
            if _run_grim_slurp(filename):
                return filename

        for backend_name, cmd, target in backends:
            if filename.exists():
                filename.unlink(missing_ok=True)
            if target == "file" and _run_capture_command(cmd, filename, backend_name):
                return filename
            if target == "clipboard":
                emit_info(f"Starting screenshot backend: {backend_name}")
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=SCREENSHOT_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired:
                    emit_warning(f"{backend_name} timed out after {SCREENSHOT_TIMEOUT_SECONDS}s")
                    continue
                except Exception as e:
                    emit_warning(f"{backend_name} failed: {e}")
                    continue

                if result.returncode == 0 and _save_wayland_clipboard_image(filename):
                    emit_info(f"{backend_name} captured image from clipboard.")
                    return filename
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    if stderr:
                        emit_warning(stderr.splitlines()[-1])
                emit_warning(f"{backend_name} did not produce a clipboard image.")

        emit_warning("Screenshot canceled, timed out, or failed.")
        if filename.exists():
            os.remove(filename)
        return None
    except Exception as e:
        log_error("Screenshot Error", str(e))
        emit_warning(f"Screenshot failed: {e}")
        if filename.exists():
            os.remove(filename)
        return None

def build_prompt(mode, image_path):
    image_path = str(image_path)
    if mode == "Table Recognition":
        return f"Extract table content from this image as HTML table: {image_path}"
    if mode == "Figure Recognition":
        return f"Extract all visible text from this figure image: {image_path}"
    return f"Extract all visible text from this image: {image_path}"

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

def check_ollama_model(model_name, noisy=True):
    """Fast preflight so we fail clearly before long OCR execution."""
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
            if noisy:
                emit_error(f"Model '{model_name}' is not ready in Ollama.")
                if err:
                    emit_error(err)
            return False
        return True
    except subprocess.TimeoutExpired:
        if noisy:
            emit_error("Timeout while checking model availability in Ollama.")
        return False
    except Exception as e:
        if noisy:
            emit_error(f"Failed to check model availability: {e}")
        return False

def run_ollama(model_name, prompt):
    """
    Runs Ollama with a hard timeout so GUI flow cannot hang forever.
    Returns (returncode, stdout, stderr, timed_out).
    """
    process = subprocess.Popen(
        ["ollama", "run", model_name],
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

def detect_model_processor(model_name):
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
        if model_name not in stripped:
            continue
        upper = stripped.upper()
        if "GPU" in upper:
            return "GPU"
        if "CPU" in upper:
            return "CPU"
        return "UNKNOWN"
    return None

def emit_processor_diagnostics(model_name):
    processor = detect_model_processor(model_name)
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

def normalize_model_output(raw_output):
    """
    Clean common Ollama/glm-ocr wrappers.
    If output is only a fenced markdown block with no content, returns an empty string.
    """
    cleaned = re.sub(r"Added image '.*?'", "", raw_output).strip()

    # Remove a single full fenced block wrapper, optionally tagged as markdown/text/html.
    fenced_match = re.match(
        r"^\s*```(?:markdown|md|text|txt|html)?\s*\n?(.*?)\n?```\s*$",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match:
        cleaned = fenced_match.group(1).strip()

    # Also remove stray fence markers if the model emitted broken wrappers.
    cleaned = re.sub(r"^\s*```(?:markdown|md|text|txt|html)?\s*$", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    cleaned = re.sub(r"^\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()

def resolve_ready_models(mode):
    if not shutil.which("ollama"):
        emit_error("Missing dependency: ollama")
        return None

    if not ensure_ollama_daemon():
        return None

    model_candidates = [PRIMARY_MODEL_NAME]
    for model_name in FALLBACK_MODEL_NAMES:
        if model_name not in model_candidates:
            model_candidates.append(model_name)

    ready_models = []
    for idx, model_name in enumerate(model_candidates):
        if check_ollama_model(model_name, noisy=(idx == 0)):
            ready_models.append(model_name)

    if not ready_models:
        return None
    return ready_models

def extract_text_from_image(mode, image_path, ready_models):
    emit_info(f"Model prompt image: {image_path}")
    emit_info(f"Waiting for OCR result (timeout: {OLLAMA_TIMEOUT_SECONDS}s)...")
    prompt = build_prompt(mode, image_path)

    clean_output = ""
    last_stdout = ""
    last_stderr = ""
    last_return_code = 0
    last_timed_out = False

    for model_idx, model_name in enumerate(ready_models, start=1):
        if model_idx > 1:
            emit_warning(f"Switching OCR model to fallback: {model_name}")
        emit_processor_diagnostics(model_name)

        return_code, stdout, stderr, timed_out = run_ollama(model_name, prompt)
        last_stdout = stdout
        last_stderr = stderr
        last_return_code = return_code
        last_timed_out = timed_out

        if return_code != 0:
            continue

        candidate = normalize_model_output(stdout)
        candidate = apply_table_styling(mode, candidate)
        if not candidate:
            continue
        clean_output = candidate
        break

    if clean_output:
        return 0, clean_output

    if last_return_code != 0:
        err_msg = (last_stderr or "").strip()
        log_error(f"Ollama Failed ({last_return_code})", err_msg)
        if last_timed_out:
            emit_error("Model execution timed out.")
        else:
            emit_error("Model failed. Check ~/ocr_debug.log")
        if err_msg:
            emit_error(err_msg)
        return last_return_code, None

    log_error("Model returned empty OCR payload", last_stdout[:2000])
    emit_warning("Model returned no text.")
    emit_warning("Raw model output looked empty (or only markdown fences).")
    if last_stdout.strip():
        emit_warning(f"Raw stdout: {last_stdout[:500].strip()}")
    if last_stderr.strip():
        emit_warning(f"Raw stderr: {last_stderr[:500].strip()}")
    return 3, None

def save_output_text(output_text, output_path):
    if not ensure_directory(output_path.parent):
        return 4
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output_text)
    emit_info(f"Saved output file: {output_path}")
    return 0

def render_pdf_to_images(pdf_path):
    if not shutil.which("pdftoppm"):
        emit_error("Missing dependency: pdftoppm")
        emit_error("Install poppler-utils to enable PDF OCR.")
        return None, None

    temp_dir = Path(tempfile.mkdtemp(prefix="ocr_pdf_"))
    output_prefix = temp_dir / "page"
    try:
        result = subprocess.run(
            ["pdftoppm", "-png", str(pdf_path), str(output_prefix)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=max(OLLAMA_TIMEOUT_SECONDS, 300),
        )
    except subprocess.TimeoutExpired:
        emit_error("PDF rendering timed out.")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None
    except Exception as e:
        log_error("PDF rendering failed", str(e))
        emit_error(f"Could not render PDF pages: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
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
    ready_models = resolve_ready_models(mode)
    if not ready_models:
        shutil.rmtree(render_dir, ignore_errors=True)
        return 2

    page_outputs = []
    try:
        for page_index, original_path in enumerate(rendered_pages, start=1):
            emit_info(f"OCR page {page_index}/{len(rendered_pages)}")
            processing_path = sanitize_image(original_path)
            try:
                return_code, clean_output = extract_text_from_image(mode, processing_path, ready_models)
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
    parsed_args = parse_cli_args()
    if parsed_args is None:
        return 2

    mode, pdf_path, output_path = parsed_args
    emit_info(f"Mode: {mode}")

    if pdf_path is not None:
        return ocr_pdf_to_text(pdf_path, mode, output_path)
    
    # 1. Screenshot
    emit_info("Starting screenshot capture. Select a region and confirm the shot.")
    original_path = take_screenshot()
    if not original_path:
        emit_warning("No screenshot captured. OCR aborted.")
        return 0

    emit_info(f"Screenshot captured: {original_path}")
    emit_info(f"Running {PRIMARY_MODEL_NAME}...")

    # 2. Sanitize
    processing_path = sanitize_image(original_path)

    # 3. Prompt Ollama
    try:
        ready_models = resolve_ready_models(mode)
        if not ready_models:
            return 2

        return_code, clean_output = extract_text_from_image(mode, processing_path, ready_models)
        if return_code != 0:
            return return_code

        # 5. Save to File
        output_file = output_path or OUTPUT_FILE
        save_code = save_output_text(clean_output, output_file)
        if save_code != 0:
            return save_code

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
    finally:
        try:
            if processing_path != original_path and processing_path.exists():
                os.remove(processing_path)
        except Exception:
            pass
        try:
            if original_path.exists():
                os.remove(original_path)
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(run())
