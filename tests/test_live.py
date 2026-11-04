"""The live export between PlasmaNest and the QtPlasmaC tab."""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "qtplasmac"))

from plasmanest import live

FLANS = os.path.join(ROOT, "samples", "flans.ngc")


@pytest.fixture(autouse=True)
def tab_log(tmp_path, monkeypatch):
    import plasmanest_tab
    monkeypatch.setattr(plasmanest_tab, "LOG_FILE", str(tmp_path / "qtplasmac-tab.log"))


def test_write_then_read(tmp_path):
    status = live.write(str(tmp_path), "G0 X1 Y1\nM2\n", 1, [], True)
    assert live.read(str(tmp_path)) == status
    assert open(status["gcode"], "rb").read() == b"G0 X1 Y1\nM2\n"
    assert status["checksum"] == live.checksum(b"G0 X1 Y1\nM2\n")
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


def test_decide(tmp_path):
    d = str(tmp_path)
    assert live.decide(None, None)[0] == "nothing"
    assert live.decide(live.write(d, None, 0, [], True), None)[0] == "nothing"
    status = live.write(d, "M2\n", 1, [], True)
    assert live.decide(status, None) == ("load", status["checksum"])
    assert live.decide(status, status["checksum"])[0] == "nothing"
    assert live.decide(live.write(d, "M2\n", 1, [], False), None)[0] == "nothing"
    action, text = live.decide(live.write(d, "M2\n", 1, ["2 figures overlap each other."], True), None)
    assert action == "error" and "overlap" in text


def test_half_written_gcode_is_not_loaded(tmp_path):
    status = live.write(str(tmp_path), "M2\n", 1, [], True)
    with open(status["gcode"], "w") as f:
        f.write("G0 X")
    assert live.decide(status, None)[0] == "nothing"


def test_unreadable_status_is_ignored(tmp_path):
    (tmp_path / live.STATUS_NAME).write_text("{trasig")
    assert live.read(str(tmp_path)) is None
    (tmp_path / live.STATUS_NAME).write_text("[1, 2]")
    assert live.read(str(tmp_path)) is None


def test_live_dir_follows_linuxcnc(tmp_path):
    assert live.live_dir(str(tmp_path / "missing")) == live.FALLBACK_DIR
    (tmp_path / "nc").mkdir()
    assert live.live_dir(str(tmp_path / "nc")) == str(tmp_path / "nc" / "plasmanest")


# ------------------------------------------------------------------ PlasmaNest side

@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    from plasmanest.gui import MainWindow
    win = MainWindow()
    yield win
    win.saved_state = win.undo_stack[win.undo_index]
    win.close()


def test_not_embedded_writes_nothing(window, tmp_path, monkeypatch):
    window.load_paths([FLANS])
    assert not window.autoload.isVisibleTo(window)
    assert not getattr(window, "live_dir", None)


def test_embedded_window_keeps_the_export_up_to_date(window, tmp_path):
    from plasmanest.nest import export_gcode
    d = str(tmp_path)
    window.enable_live_export(d)
    assert window.autoload.isVisibleTo(window)
    assert live.read(d)["parts"] == 0
    window.load_paths([FLANS])
    status = live.read(d)
    assert status["parts"] == 1 and status["problems"] == [] and status["autoload"]
    assert open(status["gcode"]).read() == export_gcode([p.placement for p in window.parts])
    assert live.decide(status, None)[0] == "load"
    # Moving a figure off the sheet is a problem; undo takes it back.
    window.parts[0].set_center(-5000, -5000)
    window.refresh()
    window.commit()
    assert "outside the sheet" in live.read(d)["problems"][0]
    window.undo()
    assert live.read(d)["problems"] == [] and live.read(d)["checksum"] == status["checksum"]
    window.autoload.setChecked(False)
    assert live.read(d)["autoload"] is False


# ------------------------------------------------------------------ QtPlasmaC side

def test_loader_loads_a_new_nest_once(tmp_path):
    import plasmanest_tab
    opened, errors = [], []
    loader = plasmanest_tab.NestLoader(None, str(tmp_path), lambda: True, opened.append, errors.append)
    assert loader.leave_tab() == "nothing"
    status = live.write(str(tmp_path), "M2\n", 1, [], True)
    assert loader.leave_tab() == "load" and opened == [status["gcode"]]
    assert loader.leave_tab() == "nothing" and len(opened) == 1
    live.write(str(tmp_path), "G0 X1 Y1\nM2\n", 1, [], True)
    assert loader.leave_tab() == "load" and len(opened) == 2


def test_loader_reports_problems_once_and_does_not_load(tmp_path):
    import plasmanest_tab
    opened, errors = [], []
    loader = plasmanest_tab.NestLoader(None, str(tmp_path), lambda: True, opened.append, errors.append)
    live.write(str(tmp_path), "M2\n", 2, ["2 figures overlap each other."], True)
    assert loader.leave_tab() == "error"
    assert loader.leave_tab() == "error"
    assert opened == [] and len(errors) == 1 and "overlap" in errors[0]


def test_loader_does_nothing_while_the_machine_runs(tmp_path):
    import plasmanest_tab
    opened = []
    loader = plasmanest_tab.NestLoader(None, str(tmp_path), lambda: False, opened.append, print)
    live.write(str(tmp_path), "M2\n", 1, [], True)
    assert loader.leave_tab() == "nothing" and opened == []


def test_leaving_the_tab_for_main_loads_but_other_tabs_do_not(app):
    import plasmanest_tab
    from test_qtplasmac_tab import FakeHandler
    handler = FakeHandler()
    handler.MAIN = 0
    tab = plasmanest_tab.install(handler)
    tab.command = [sys.executable, "-c", "import time; time.sleep(30)"]
    calls = []
    handler.plasmanest_loader.leave_tab = lambda: calls.append(1)
    tabs = handler.w.main_tab_widget
    tabs.setCurrentWidget(tab)
    tabs.setCurrentIndex(3)  # SETTINGS
    assert calls == []
    tabs.setCurrentWidget(tab)
    tabs.setCurrentIndex(0)  # MAIN
    assert calls == [1]
    tabs.setCurrentIndex(2)
    tabs.setCurrentIndex(0)  # from another tab
    assert calls == [1]
    tab.stop()
