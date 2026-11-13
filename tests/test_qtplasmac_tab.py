# SPDX-License-Identifier: GPL-2.0-or-later
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


@pytest.fixture(autouse=True)
def tab_log(tmp_path, monkeypatch):
    path = tmp_path / "qtplasmac-tab.log"
    monkeypatch.setattr(plasmanest_tab, "LOG_FILE", str(path))
    return path


class FakePin(QObject):
    value_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.value = False

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.value_changed.emit(value)


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
        self.widgetConversational = self.pages["CONVERSATIONAL"]
        self.widgetParameters = self.pages["PARAMETERS"]
        self.widgetSettings = self.pages["SETTINGS"]
        self.widgetStatistics = self.pages["STATISTICS"]
        self.MAIN, self.CONVERSATIONAL, self.PARAMETERS, self.SETTINGS, self.STATISTICS = 0, 1, 2, 3, 4
        self.convTabDisable, self.paramTabDisable, self.settingsTabDisable = FakePin(), FakePin(), FakePin()

    def startup_timeout(self):
        # As in QtPlasmaC 2.9.4: the pins are connected late, after the user command file has run.
        for pin in (self.convTabDisable, self.paramTabDisable, self.settingsTabDisable):
            pin.value_changed.connect(self.disable_tabs)

    def disable_tabs(self):  # as in QtPlasmaC 2.9.4: no arguments
        tabs = self.w.main_tab_widget
        while tabs.count() > 1:
            tabs.removeTab(1)
        names = ["STATISTICS"]
        for pin, name in ((self.settingsTabDisable, "SETTINGS"), (self.paramTabDisable, "PARAMETERS"),
                          (self.convTabDisable, "CONVERSATIONAL")):
            if not pin.get():
                names.insert(0, name)
        for index, name in enumerate(names, 1):
            tabs.insertTab(index, self.pages[name], name)
        self.CONVERSATIONAL = tabs.indexOf(self.widgetConversational)
        self.PARAMETERS = tabs.indexOf(self.widgetParameters)
        self.SETTINGS = tabs.indexOf(self.widgetSettings)
        self.STATISTICS = tabs.indexOf(self.widgetStatistics)

    def set_tab_jog_states_paused(self, state):
        # QtPlasmaC 2.9.4's set_tab_jog_states while paused.
        tabs = self.w.main_tab_widget
        for n in range(tabs.count()):
            if n > 1:
                tabs.setTabEnabled(n, state)


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
    handler.startup_timeout()
    handler.convTabDisable.set(True)  # via QtPlasmaC's own connection
    assert titles(handler)[1] == "PLASMANEST"
    handler.disable_tabs()  # a direct call
    assert titles(handler).count("PLASMANEST") == 1 and titles(handler)[1] == "PLASMANEST"


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
    assert wait_for(lambda: "ended" in tab.message.text())
    assert tab.process is None and not tab.restart.isHidden()


def test_program_that_never_answers_is_stopped(app):
    tab = plasmanest_tab.PlasmaNestTab([sys.executable, "-c", "import time; time.sleep(30)"], timeout_ms=300)
    tab.start()
    process = tab.process
    assert wait_for(lambda: "did not answer" in tab.message.text())
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
    # The whole tab, less what QtPlasmaC's style sheet lets the pages overhang the pane.
    m = plasmanest_tab.PANE_OVERHANG
    inside = tab.rect().adjusted(m, m, -m, -m)
    assert tab.container.mapTo(tab, tab.container.rect().topLeft()) == inside.topLeft()
    assert tab.container.size() == inside.size()
    tab.stop()
    assert not tab.message_page.isHidden()
    tab.close()


def test_pin_connected_to_the_wrapped_method_hides_conversational(app):
    # In the real QtPlasmaC the pin ends up calling our wrapper with its value as an argument.
    handler = FakeHandler()
    plasmanest_tab.install(handler)
    handler.convTabDisable.value_changed.connect(handler.disable_tabs)
    handler.convTabDisable.set(True)
    QApplication.processEvents()
    assert titles(handler) == ["MAIN", "PLASMANEST", "PARAMETERS", "SETTINGS", "STATISTICS"]
    handler.convTabDisable.set(False)
    QApplication.processEvents()
    assert titles(handler) == ["MAIN", "CONVERSATIONAL", "PARAMETERS", "SETTINGS", "STATISTICS", "PLASMANEST"]


def test_errors_in_slots_never_leave_the_glue(app, capsys):
    tab = plasmanest_tab.PlasmaNestTab([sys.executable, "-c", "pass"])
    tab.process = object()  # nonsense state: every slot must still return quietly
    tab.watch()
    tab.read_window_id()
    tab.stop()
    assert "Traceback" in capsys.readouterr().err


def test_pin_changes_are_logged(app, tab_log):
    handler = FakeHandler()
    plasmanest_tab.install(handler)
    handler.convTabDisable.set(True)
    text = tab_log.read_text()
    assert "installed" in text and "convTabDisable = True" in text and "PLASMANEST" in text


def test_value_set_before_qtplasmac_listens_is_still_applied(app):
    # QtPlasmaC 2.9.4 misses a value set in the postgui HAL file; the glue builds the tabs anyway.
    handler = FakeHandler()
    plasmanest_tab.install(handler)
    handler.convTabDisable.set(True)  # nobody from QtPlasmaC is listening yet
    QApplication.processEvents()
    assert titles(handler) == ["MAIN", "PLASMANEST", "PARAMETERS", "SETTINGS", "STATISTICS"]
    handler.startup_timeout()
    handler.paramTabDisable.set(True)
    QApplication.processEvents()
    assert titles(handler) == ["MAIN", "PLASMANEST", "SETTINGS", "STATISTICS"]


def test_other_qtvcp_programs_are_skipped(app, tab_log):
    class Panel:
        class w:
            pass
    assert plasmanest_tab.install(Panel()) is None
    assert "skipping" in tab_log.read_text() and "Traceback" not in tab_log.read_text()


def test_tab_takes_conversationals_place_and_keeps_qtplasmacs_indexes_right(app):
    handler = FakeHandler()
    plasmanest_tab.install(handler)
    tabs = handler.w.main_tab_widget
    assert titles(handler)[-1] == "PLASMANEST" and handler.PARAMETERS == 2
    handler.startup_timeout()
    handler.convTabDisable.set(True)
    QApplication.processEvents()
    assert titles(handler) == ["MAIN", "PLASMANEST", "PARAMETERS", "SETTINGS", "STATISTICS"]
    assert handler.CONVERSATIONAL == -1
    assert tabs.tabText(handler.PARAMETERS) == "PARAMETERS"
    assert tabs.tabText(handler.SETTINGS) == "SETTINGS"
    assert tabs.tabText(handler.STATISTICS) == "STATISTICS"
    # While paused QtPlasmaC enables tabs from index 2: PARAMETERS again, not PLASMANEST.
    for n in range(1, tabs.count()):
        tabs.setTabEnabled(n, False)
    handler.set_tab_jog_states_paused(True)
    assert not tabs.isTabEnabled(1) and tabs.isTabEnabled(handler.PARAMETERS)
    # Showing CONVERSATIONAL again puts the tab last.
    handler.convTabDisable.set(False)
    QApplication.processEvents()
    assert titles(handler) == ["MAIN", "CONVERSATIONAL", "PARAMETERS", "SETTINGS", "STATISTICS", "PLASMANEST"]
    assert handler.PARAMETERS == 2 and handler.CONVERSATIONAL == 1


class FixedHandler:
    """What the glue uses of QtPlasmaC 2.10's handler: fixed tab indexes, no disable_tabs."""

    def __init__(self):
        class W:
            pass
        self.w = W()
        self.w.main_tab_widget = QTabWidget()
        for name in ("MAIN", "CONVERSATIONAL", "PARAMETERS", "SETTINGS", "STATISTICS"):
            self.w.main_tab_widget.addTab(QLabel(name), name)
        self.MAIN, self.CONVERSATIONAL, self.PARAMETERS, self.SETTINGS, self.STATISTICS = 0, 1, 2, 3, 4
        self.tabNames = {0: "main_tab", 1: "conversational_tab", 2: "parameters_tab",
                         3: "settings_tab", 4: "statistics_tab"}
        self.interlockRules = {}
        self.refreshed = 0
        self.convTabDisable, self.paramTabDisable, self.settingsTabDisable = FakePin(), FakePin(), FakePin()
        tabs = self.w.main_tab_widget
        # As in QtPlasmaC 2.10: the pin slots read the indexes when they are called.
        self.convTabDisable.value_changed.connect(lambda v: tabs.setTabVisible(self.CONVERSATIONAL, not v))
        self.paramTabDisable.value_changed.connect(lambda v: tabs.setTabVisible(self.PARAMETERS, not v))

    def refresh_button_states(self, force=False):
        self.refreshed += 1


@pytest.fixture
def fixed_handler_modules(monkeypatch):
    import sys
    import types
    rules = types.ModuleType("qtvcp.lib.qtplasmac.interlock_rules")
    rules.INTERLOCK_RULES = {"conversational_tab": {"all": {"interp_idle": True}}}
    for name in ("qtvcp", "qtvcp.lib", "qtvcp.lib.qtplasmac"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "qtvcp.lib.qtplasmac.interlock_rules", rules)
    sys.modules["qtvcp.lib.qtplasmac"].interlock_rules = rules
    return rules


def visible_titles(handler):
    tabs = handler.w.main_tab_widget
    return [tabs.tabText(i) for i in range(tabs.count()) if tabs.isTabVisible(i)]


def test_install_on_fixed_tab_indexes(app, fixed_handler_modules):
    rules = fixed_handler_modules
    handler = FixedHandler()
    tab = plasmanest_tab.install(handler)
    assert tab is not None
    # CONVERSATIONAL shown: last, and the other tabs keep their indexes.
    assert titles(handler) == ["MAIN", "CONVERSATIONAL", "PARAMETERS", "SETTINGS", "STATISTICS", "PLASMANEST"]
    assert handler.CONVERSATIONAL == 1 and handler.tabNames[5] == "plasmanest_tab"
    assert handler.interlockRules["plasmanest_tab"] == {"all": {"interp_idle": True}}
    assert rules.INTERLOCK_RULES["plasmanest_tab"] is handler.interlockRules["plasmanest_tab"]
    assert handler.refreshed >= 1
    tab.stop()


def test_fixed_tabs_take_conversationals_place_when_it_is_hidden(app, fixed_handler_modules):
    handler = FixedHandler()
    tab = plasmanest_tab.install(handler)
    handler.convTabDisable.set(True)
    assert wait_for(lambda: titles(handler)[1] == "PLASMANEST")
    assert visible_titles(handler) == ["MAIN", "PLASMANEST", "PARAMETERS", "SETTINGS", "STATISTICS"]
    # QtPlasmaC's indexes and rule names follow the tabs.
    tabs = handler.w.main_tab_widget
    assert tabs.tabText(handler.CONVERSATIONAL) == "CONVERSATIONAL"
    assert tabs.tabText(handler.PARAMETERS) == "PARAMETERS" and handler.STATISTICS == 5
    assert handler.tabNames == {0: "main_tab", 1: "plasmanest_tab", 2: "conversational_tab",
                                3: "parameters_tab", 4: "settings_tab", 5: "statistics_tab"}
    # Hiding another tab afterwards uses the renumbered index.
    handler.paramTabDisable.set(True)
    assert visible_titles(handler) == ["MAIN", "PLASMANEST", "SETTINGS", "STATISTICS"]
    # And it goes last again when CONVERSATIONAL is shown.
    handler.convTabDisable.set(False)
    assert wait_for(lambda: titles(handler)[-1] == "PLASMANEST")
    assert handler.CONVERSATIONAL == 1
    tab.stop()


def test_fixed_tabs_follow_a_pin_set_before_the_screen_read_it(app, fixed_handler_modules):
    handler = FixedHandler()
    handler.convTabDisable.value = True  # no signal, as with a postgui setp that was missed
    tab = plasmanest_tab.install(handler)
    assert visible_titles(handler) == ["MAIN", "PLASMANEST", "PARAMETERS", "SETTINGS", "STATISTICS"]
    tab.stop()
