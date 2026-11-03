"""A PLASMANEST tab in QtPlasmaC (2.9.4) with PlasmaNest embedded as its own program.

Loaded through user_command.py, which QtVCP runs from USER_COMMAND_FILE after the screen is built.
This runs inside QtPlasmaC's process, so it is kept small and must never raise: a failure here may
only leave the tab empty, never stop the machine's screen.

PlasmaNest is started the first time the tab is chosen. Its window id is read from the first line
of its stdout without blocking QtPlasmaC, and its window is shown in the tab the same way QtVCP's
embed_program does it. Closing QtPlasmaC ends PlasmaNest with SIGTERM.
"""

import os
import subprocess
import sys
import traceback

from PyQt5.QtCore import QSocketNotifier, QTimer
from PyQt5.QtGui import QWindow
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget

TAB_TITLE = "PLASMANEST"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from plasmanest import live  # noqa: E402 - no Qt, safe to import into QtPlasmaC
COMMAND = [sys.executable, os.path.join(ROOT, "plasmanest.py"), "--embed"]
START_TIMEOUT_MS = 15000
WATCH_MS = 1000


def log(text):
    print(f"PlasmaNest-fliken: {text}", file=sys.stderr, flush=True)


class PlasmaNestTab(QWidget):
    def __init__(self, command=COMMAND, timeout_ms=START_TIMEOUT_MS):
        super().__init__()
        self.setObjectName("plasmanest_tab")
        self.command = command
        self.timeout_ms = timeout_ms
        self.process = None
        self.container = None
        self.notifier = None
        self.buffer = b""
        # Two pages: a message while starting or after a failure, and PlasmaNest filling the tab.
        self.pages = QStackedLayout(self)
        self.message_page = QWidget()
        message_layout = QVBoxLayout(self.message_page)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.restart = QPushButton("Starta PlasmaNest")
        self.restart.clicked.connect(lambda checked=False: self.start())
        message_layout.addStretch(1)
        message_layout.addWidget(self.message)
        message_layout.addWidget(self.restart)
        message_layout.addStretch(1)
        self.restart.hide()
        self.pages.addWidget(self.message_page)
        self.start_timer = QTimer(self)
        self.start_timer.setSingleShot(True)
        self.start_timer.timeout.connect(lambda: self.fail("PlasmaNest svarade inte."))
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.watch)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def show_message(self, text, restart=False):
        self.message.setText(text)
        self.restart.setVisible(restart)
        self.pages.setCurrentWidget(self.message_page)

    def make_container(self, window_id):
        return QWidget.createWindowContainer(QWindow.fromWinId(window_id), self)

    def start(self):
        if self.process is not None:
            return
        try:
            self.show_message("Startar PlasmaNest…")
            self.buffer = b""
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self.notifier = QSocketNotifier(self.process.stdout.fileno(), QSocketNotifier.Read, self)
            self.notifier.activated.connect(lambda fd: self.read_window_id())
            self.start_timer.start(self.timeout_ms)
            self.watch_timer.start(WATCH_MS)
        except Exception as error:
            self.fail(f"Kunde inte starta PlasmaNest: {error}")

    def read_window_id(self):
        try:
            data = os.read(self.process.stdout.fileno(), 64)
        except OSError:
            data = b""
        if not data:
            self.fail("PlasmaNest avslutades innan det startat.")
            return
        self.buffer += data
        if b"\n" not in self.buffer:
            return
        # Never read the pipe again: PlasmaNest has silenced its stdout after this line.
        self.notifier.setEnabled(False)
        self.start_timer.stop()
        try:
            window_id = int(self.buffer.split(b"\n", 1)[0])
            self.container = self.make_container(window_id)
            self.pages.addWidget(self.container)
            self.pages.setCurrentWidget(self.container)
        except Exception as error:
            self.fail(f"Kunde inte visa PlasmaNest: {error}")

    def watch(self):
        if self.process is not None and self.process.poll() is not None:
            self.cleanup()
            self.show_message("PlasmaNest har avslutats.", restart=True)

    def fail(self, text):
        log(text)
        self.stop()
        self.show_message(text, restart=True)

    def cleanup(self):
        self.start_timer.stop()
        self.watch_timer.stop()
        if self.notifier is not None:
            self.notifier.setEnabled(False)
            self.notifier = None
        if self.container is not None:
            self.pages.removeWidget(self.container)
            self.container.deleteLater()
            self.container = None
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    stream.close()
                except Exception:
                    pass
        self.process = None

    def stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(5)
            except Exception:
                process.kill()
        self.cleanup()


class NestLoader:
    """Loads the live export into QtPlasmaC's preview when leaving the tab for MAIN."""

    def __init__(self, handler, directory=None, machine_idle=None, open_program=None, report_error=None):
        self.handler = handler
        self.directory = directory or live.live_dir()
        self.machine_idle = machine_idle or self.qtvcp_machine_idle
        self.open_program = open_program or self.qtvcp_open_program
        self.report_error = report_error or self.qtvcp_report_error
        self.last_loaded = None
        self.last_error = None

    def leave_tab(self):
        if not self.machine_idle():
            log("maskinen står inte still, nesten läses inte in")
            return "nothing"
        status = live.read(self.directory)
        action, value = live.decide(status, self.last_loaded)
        if action == "load":
            self.open_program(status["gcode"])
            self.last_loaded = value
            self.last_error = None
        elif action == "error":
            if status.get("checksum") != self.last_error:  # once per nest, not at every tab change
                self.report_error(value)
                self.last_error = status.get("checksum")
        return action

    @staticmethod
    def qtvcp_machine_idle():
        from qtvcp.core import Status
        status = Status()
        return status.is_interp_idle() and not status.is_auto_paused()

    def qtvcp_open_program(self, path):
        # As QtPlasmaC's own latest-file button does.
        from qtvcp.core import Action
        try:
            self.handler.overlayProgress.setValue(0)
        except Exception:
            pass
        try:
            self.handler.remove_temp_materials()
        except Exception:
            log(traceback.format_exc())
        Action().OPEN_PROGRAM(path)

    @staticmethod
    def qtvcp_report_error(text):
        import linuxcnc
        from qtvcp.core import Status
        Status().emit("error", linuxcnc.OPERATOR_ERROR, text)


def install(handler):
    """Add the tab to QtPlasmaC's main tab widget, last, and keep it there."""
    try:
        tabs = handler.w.main_tab_widget
        tab = PlasmaNestTab()

        def ensure_tab():
            # QtPlasmaC's disable_tabs removes every tab but MAIN and only puts its own back.
            try:
                if tabs.indexOf(tab) < 0:
                    tabs.addTab(tab, TAB_TITLE)
            except Exception:
                log(traceback.format_exc())

        ensure_tab()
        original = handler.disable_tabs

        def disable_tabs(*args, **kwargs):
            original(*args, **kwargs)
            ensure_tab()
        handler.disable_tabs = disable_tabs
        # The HAL pins were connected to the original method before this file ran.
        for name in ("convTabDisable", "paramTabDisable", "settingsTabDisable"):
            pin = getattr(handler, name, None)
            if pin is not None:
                pin.value_changed.connect(lambda value: ensure_tab())

        loader = NestLoader(handler)
        previous = [tabs.currentWidget()]

        def tab_changed(index):
            try:
                widget = tabs.widget(index)
                if widget is tab:
                    tab.start()
                elif previous[0] is tab and index == getattr(handler, "MAIN", 0):
                    loader.leave_tab()
            except Exception:
                log(traceback.format_exc())
            finally:
                previous[0] = tabs.widget(index)
        tabs.currentChanged.connect(tab_changed)
        handler.plasmanest_loader = loader
        QApplication.instance().aboutToQuit.connect(tab.stop)
        handler.plasmanest_tab = tab
        return tab
    except Exception:
        log(traceback.format_exc())
        return None
