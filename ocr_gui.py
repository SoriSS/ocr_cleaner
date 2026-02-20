#!/usr/bin/env python3
import sys
import shlex
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QPushButton, 
                             QLabel, QTextEdit)
from PyQt6.QtCore import Qt, QProcess, QSize
from PyQt6.QtGui import QIcon, QFont, QColor, QPainter, QPen, QPixmap


def build_app_icon():
    icon_file = Path(__file__).parent / "assets" / "glm_ocr.ico"
    if icon_file.exists():
        return QIcon(str(icon_file))

    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0b3d91"))
    painter.drawRoundedRect(8, 8, 112, 112, 20, 20)

    painter.setPen(QPen(QColor("#ffffff"), 8))
    painter.drawLine(30, 88, 58, 56)
    painter.drawLine(58, 56, 72, 70)
    painter.drawLine(72, 70, 98, 40)

    painter.end()
    return QIcon(pixmap)

class OCRLauncher(QWidget):
    def __init__(self):
        super().__init__()
        self.script_path = self.find_backend_script()
        self.process = None # Keep reference to prevent garbage collection
        self.initUI()

    def find_backend_script(self):
        """Finds ocr_only.py in current dir or ~/Scripts."""
        # 1. Check current directory
        current_dir = Path(__file__).parent / "ocr_only.py"
        if current_dir.exists():
            return str(current_dir)
        
        # 2. Check ~/Scripts/
        home_dir = Path.home() / "Scripts" / "ocr_only.py"
        if home_dir.exists():
            return str(home_dir)
        
        return None

    def initUI(self):
        self.setWindowTitle("GLM-OCR Interface")
        self.setWindowIcon(build_app_icon())
        self.setFixedSize(320, 350) # Slightly taller for the log

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # --- Title ---
        lbl_title = QLabel("AI Text Recognition")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSize(12)
        lbl_title.setFont(font)
        layout.addWidget(lbl_title)

        # --- Buttons ---
        self.btn_text = self.create_button("Text Recognition", "draw-text", "text")
        self.btn_table = self.create_button("Table Recognition", "view-grid", "table")
        self.btn_figure = self.create_button("Figure Recognition", "image-x-generic", "figure")

        layout.addWidget(self.btn_text)
        layout.addWidget(self.btn_table)
        layout.addWidget(self.btn_figure)

        # --- Log Output ---
        lbl_log = QLabel("Status Log:")
        lbl_log.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(lbl_log)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("font-size: 10px; color: #333; background: #f0f0f0; border: 1px solid #ccc;")
        layout.addWidget(self.log_box)

        self.setLayout(layout)

        # Check script
        if not self.script_path:
            self.log("[ERROR] ocr_only.py not found!", error=True)
            self.set_buttons_enabled(False)
        else:
            self.log("[INFO] Ready. Select a mode.")

    def create_button(self, text, icon_name, mode):
        btn = QPushButton(text)
        if QIcon.hasThemeIcon(icon_name):
            btn.setIcon(QIcon.fromTheme(icon_name))
        else:
            btn.setIcon(QIcon.fromTheme("application-x-executable"))
        btn.setIconSize(QSize(24, 24))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(40)
        # Connect click
        btn.clicked.connect(lambda: self.run_ocr(mode))
        return btn

    def log(self, message, error=False):
        """Append text to the log box"""
        color = "red" if error else "#111"
        if message.startswith("[SUCCESS]"):
            color = "#16794d"
        elif message.startswith("[WARNING]"):
            color = "#9a6700"
        elif message.startswith("[ERROR]"):
            color = "red"
        self.log_box.append(f'<span style="color:{color}">{message}</span>')
        # Scroll to bottom
        scrollbar = self.log_box.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_buttons_enabled(self, enabled):
        self.btn_text.setEnabled(enabled)
        self.btn_table.setEnabled(enabled)
        self.btn_figure.setEnabled(enabled)

    def run_ocr(self, mode):
        if not self.script_path:
            return

        # 1. Lock UI
        self.set_buttons_enabled(False)
        self.log(f"[INFO] Starting {mode}...")
        self.log("[INFO] Please select region on screen...")

        # 2. Setup Process
        self.process = QProcess()
        
        # Connect signals to handle output/errors
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.finished.connect(self.on_process_finished)
        
        # 3. Start through login shell so GUI gets the same env as terminal
        command = f"python3 -u {shlex.quote(self.script_path)} {shlex.quote(mode)}"
        self.log(f"[INFO] Launch command: {command}")
        self.process.start("zsh", ["-lc", command])

    def handle_stdout(self):
        data = self.process.readAllStandardOutput()
        stdout = bytes(data).decode("utf8", errors="replace")
        for line in stdout.splitlines():
            line = line.strip()
            if line:
                self.log(line)

    def handle_stderr(self):
        data = self.process.readAllStandardError()
        stderr = bytes(data).decode("utf8", errors="replace")
        for line in stderr.splitlines():
            line = line.strip()
            if line:
                self.log(line if line.startswith("[ERROR]") else f"[ERROR] {line}", error=True)

    def on_process_error(self, process_error):
        self.log(f"[ERROR] Backend process error: {process_error}", error=True)

    def on_process_finished(self, exit_code, exit_status):
        self.set_buttons_enabled(True)
        if exit_code == 0:
            self.log("[INFO] Backend process completed.")
            return

        if exit_status == QProcess.ExitStatus.CrashExit:
            self.log("[ERROR] Backend crashed during processing.", error=True)
        else:
            self.log(f"[ERROR] Backend failed (exit code {exit_code}).", error=True)

def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(build_app_icon())
    app.setDesktopFileName("glm-ocr")
    
    window = OCRLauncher()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
