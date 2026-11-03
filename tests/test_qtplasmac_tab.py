"""The QtPlasmaC tab glue, with a stand-in for QtPlasmaC's handler."""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "qtplasmac"))

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QLabel, QTabWidget

import plasmanest_tab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakePin(QObject):
    value_changed = pyqtSignal(bool)


class FakeHandler:
    """What the glue uses of QtPlasmaC 2.9.4's handler."""

    def __init__(self):
        class W:
            pass
        self.w = W()
        self.w.main_tab_widget = QTabWidget()
        self.pages = {name: QLabel(name) for name in ("MAIN", "CONVERSATIONAL", "PARAMETERS", "SETTINGS",
                                                      "STATISTICS")}
        for name, page in self.pages.items():
            self.w.main_tab_widget.addTab(page, name)
        self.convTabDisable, self.paramTabDisable, self.settingsTabDisable = FakePin(), FakePin(), FakePin()
        # QtPlasmaC connects the pins to its bound method before the user command file runs.
        for pin in (self.convTabDisable, self.paramTabDisable, self.settingsTabDisable):
            pin.value_changed.connect(self.disable_tabs)

    def disable_tabs(self, *args):
        tabs = self.w.main_tab_widget
        while tabs.count() > 1:
            tabs.removeTab(1)
        for index, name in enumerate(("CONVERSATIONAL", "PARAMETERS", "SETTINGS", "STATISTICS"), 1):
            tabs.insertTab(index, self.pages[name], name)


def titles(handler):
    tabs = handler.w.main_tab_widget
    return [tabs.tabText(i) for i in range(tabs.count())]


def wait_for(condition, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_user_command_file_installs_the_tab_last(app):
    handler = FakeHandler()
    path = os.path.join(ROOT, "qtplasmac", "user_command.py")
    exec(compile(open(path, "rb").read(), path, "exec"), {"self": handler, "rcfile": path})
    assert titles(handler)[-1] == "PLASMANEST"
    assert titles(handler)[:5] == ["MAIN", "CONVERSATIONAL", "PARAMETERS", "SETTINGS", "STATISTICS"]


def test_tab_comes_back_after_qtplasmac_rebuilds_its_tabs(app):
    handler = FakeHandler()
    plasmanest_tab.install(handler)
    handler.convTabDisable.value_changed.emit(True)  # via QtPlasmaC's own connection
    assert titles(handler)[-1] == "PLASMANEST"
    handler.disable_tabs()  # a direct call
    assert titles(handler).count("PLASMANEST") == 1 and titles(handler)[-1] == "PLASMANEST"


def test_program_starts_only_when_the_tab_is_chosen(app):
    handler = FakeHandler()
    tab = plasmanest_tab.install(handler)
    tab.command = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert tab.process is None
    handler.w.main_tab_widget.setCurrentIndex(2)
    assert tab.process is None
    handler.w.main_tab_widget.setCurrentWidget(tab)
    assert tab.running
    tab.stop()
    assert tab.process is None


def test_program_that_exits_early_shows_a_message(app):
    tab = plasmanest_tab.PlasmaNestTab([sys.executable, "-c", "pass"])
    tab.start()
    assert wait_for(lambda: "avslutades" in tab.message.text())
    assert tab.process is None and not tab.restart.isHidden()


def test_program_that_never_answers_is_stopped(app):
    tab = plasmanest_tab.PlasmaNestTab([sys.executable, "-c", "import time; time.sleep(30)"], timeout_ms=300)
    tab.start()
    process = tab.process
    assert wait_for(lambda: "svarade inte" in tab.message.text())
    assert process.poll() is not None and tab.process is None


def test_install_never_raises(app):
    class Broken:
        pass
    assert plasmanest_tab.install(Broken()) is None


def test_plasmanest_fills_the_whole_tab(app):
    from PyQt5.QtWidgets import QWidget
    script = "import time; print(12345, flush=True); time.sleep(30)"
    tab = plasmanest_tab.PlasmaNestTab([sys.executable, "-c", script])
    tab.make_container = lambda window_id: QWidget()  # offscreen Qt cannot embed a real window
    tab.resize(1200, 700)
    tab.show()
    tab.start()
    assert wait_for(lambda: tab.container is not None)
    QApplication.processEvents()
    assert tab.container.isVisible()
    assert tab.container.geometry() == tab.rect()
    tab.stop()
    assert not tab.message_page.isHidden()
    tab.close()
