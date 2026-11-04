"""Embed PlasmaNest in a tab the way QtVCP does, to try embedding without QtPlasmaC.

Mirrors embed_program in qtvcp/widgets/xembed.py (LinuxCNC 2.9.4): start the program with
stdout as a pipe, read its window id from the first line, never read the pipe again, and put
the window in a container with QWindow.fromWinId and QWidget.createWindowContainer.
Closing the host ends the program with SIGTERM, like QtVCP's _hal_cleanup.

    python3 tools/embed_host.py
"""

import os
import subprocess
import sys

from PyQt5.QtGui import QWindow
from PyQt5.QtWidgets import QApplication, QLabel, QTabWidget, QVBoxLayout, QWidget

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMAND = [sys.executable, os.path.join(ROOT, "plasmanest.py"), "--embed"]


class Host(QTabWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Embedding test (like QtVCP)")
        self.addTab(QLabel("MAIN would be here."), "MAIN")
        self.process = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        xid = int(self.process.stdout.readline())
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QWidget.createWindowContainer(QWindow.fromWinId(xid), page))
        self.addTab(page, "PLASMANEST")
        self.setCurrentIndex(1)

    def closeEvent(self, event):
        self.process.terminate()
        self.process.wait(10)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    host = Host()
    host.resize(1400, 850)
    host.show()
    sys.exit(app.exec_())
