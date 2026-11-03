import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import save_point
from plasmanest import machine
from plasmanest.machine import MachinePoint, Offsets, PointsError
from plasmanest.nest import Sheet


def close(a, b, tolerance=1e-9):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def fake_stat(position=(0.0, 0.0), g5x=(0.0, 0.0), rotation=0.0, g92=(0.0, 0.0), homed=(1, 1, 1),
              ini="/nonexistent/QtPlasma.ini"):
    return SimpleNamespace(actual_position=position + (0.0,) * 7, g5x_offset=g5x + (0.0,) * 7,
                           tool_offset=(0.0,) * 9, g92_offset=g92 + (0.0,) * 7, rotation_xy=rotation,
                           g5x_index=1, homed=homed, joints=len(homed), ini_filename=ini)


# ------------------------------------------------------------------ coordinates

def test_work_coordinates_subtract_the_origin():
    assert close(Offsets(g5x=(40.05, 40.05)).work((140.05, 60.05)), (100.0, 20.0))


def test_work_coordinates_follow_hal_glib_rotation_and_g92():
    # Same order as hal_glib.get_position: origin, rotate by -rotation_xy, then G92.
    offsets = Offsets(g5x=(100.0, 200.0), rotation=90.0, g92=(1.0, 2.0))
    assert close(offsets.work((100.0, 210.0)), (10.0 - 1.0, 0.0 - 2.0))


def test_laser_point_is_torch_minus_offset():
    # QtPlasmaC's sheet_align puts the origin under the laser at torch - offset.
    assert close(machine.aimed_point((100.0, 100.0, 5.0), True, (32.7, 43.0)), (67.3, 57.0))
    assert close(machine.aimed_point((100.0, 100.0, 5.0), False, (32.7, 43.0)), (100.0, 100.0))


def test_laser_offset_is_read_from_the_prefs_file(tmp_path):
    prefs = tmp_path / "Mill.prefs"
    assert machine.prefs_path(str(tmp_path / "Mill.ini"), "Mill ") == str(prefs)
    prefs.write_text("[BUTTONS]\n1 Code = G0X0Y0\n[LASER_OFFSET]\nX axis = 32.7\nY axis = 43.0\n")
    assert machine.read_laser_offset(str(prefs)) == (32.7, 43.0)
    prefs.write_text("[LASER_OFFSET]\nX axis = 0.0\nY axis = 0.0\n")
    assert machine.read_laser_offset(str(prefs)) is None
    for bad in ("[LASER_OFFSET]\nX axis = abc\n", "no header\n"):
        prefs.write_text(bad)
        with pytest.raises(PointsError):
            machine.read_laser_offset(str(prefs))
    with pytest.raises(PointsError):
        machine.read_laser_offset(str(tmp_path / "missing.prefs"))
    with pytest.raises(PointsError):
        machine.prefs_path(str(tmp_path / "Mill.ini"), None)


def test_current_offsets_are_used_when_known():
    points = [MachinePoint((150.0, 70.0), (150.0, 70.0), "torch", "t")]
    assert machine.work_corners(points, None) == [(150.0, 70.0)]
    assert close(machine.work_corners(points, Offsets(g5x=(50.0, 20.0)))[0], (100.0, 50.0))


# ------------------------------------------------------------------ points file

def test_points_file_round_trip(tmp_path):
    path = str(tmp_path / "sub" / "points.txt")
    a = MachinePoint((1.5, 2.25), (0.5, 1.25), "torch", "2026-11-03T10:00:00")
    b = MachinePoint((300.0, -4.0), (299.0, -5.0), "laser", "2026-11-03T10:01:00")
    assert machine.append_point(a, path) == 1
    assert machine.append_point(b, path) == 2
    assert machine.read_points(path) == [a, b]
    machine.write_points([b], path)
    assert machine.read_points(path) == [b]
    assert open(path).read().startswith("#")


def test_unreadable_points_file_is_refused():
    with pytest.raises(PointsError):
        machine.parse_points("1 2 3 4 torch\n")
    with pytest.raises(PointsError):
        machine.parse_points("1 2 3 4 plasma 2026\n")
    with pytest.raises(PointsError):
        machine.parse_points("1 nan 3 4 torch 2026\n")


def test_missing_points_file_is_empty(tmp_path):
    assert machine.read_points(str(tmp_path / "none.txt")) == []


# ------------------------------------------------------------------ the button script

def test_save_point_with_torch(tmp_path, monkeypatch):
    monkeypatch.setattr(save_point, "laser_is_on", lambda: False)
    path = str(tmp_path / "points.txt")
    message = save_point.save(fake_stat((140.0, 60.0), g5x=(40.0, 40.0)), path)
    point, = machine.read_points(path)
    assert point.machine == (140.0, 60.0) and point.work == (100.0, 20.0) and point.source == "torch"
    assert "punkt 1" in message


def test_save_point_with_laser(tmp_path, monkeypatch):
    ini = tmp_path / "Mill.ini"
    (tmp_path / "Mill.prefs").write_text("[LASER_OFFSET]\nX axis = 32.7\nY axis = 43.0\n")
    monkeypatch.setattr(save_point, "laser_is_on", lambda: True)
    monkeypatch.setattr(save_point, "machine_name", lambda ini: "Mill")
    path = str(tmp_path / "points.txt")
    save_point.save(fake_stat((100.0, 100.0), ini=str(ini)), path)
    point, = machine.read_points(path)
    assert close(point.machine, (67.3, 57.0)) and point.source == "laser"


def test_save_point_refuses_unhomed_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(save_point, "laser_is_on", lambda: False)
    path = str(tmp_path / "points.txt")
    with pytest.raises(PointsError):
        save_point.save(fake_stat(homed=(1, 0, 1)), path)
    assert not os.path.exists(path)


def test_save_point_refuses_laser_without_offset(tmp_path, monkeypatch):
    ini = tmp_path / "Mill.ini"
    (tmp_path / "Mill.prefs").write_text("[LASER_OFFSET]\nX axis = 0\nY axis = 0\n")
    monkeypatch.setattr(save_point, "laser_is_on", lambda: True)
    monkeypatch.setattr(save_point, "machine_name", lambda ini: "Mill")
    with pytest.raises(PointsError):
        save_point.save(fake_stat(ini=str(ini)), str(tmp_path / "points.txt"))


# ------------------------------------------------------------------ margin

def test_inset_rectangle_either_direction():
    for corners in ([(0, 0), (100, 0), (100, 50), (0, 50)], [(0, 0), (0, 50), (100, 50), (100, 0)]):
        inset = Sheet(corners).inset(5)
        assert sorted(inset.corners) == [(5, 5), (5, 45), (95, 5), (95, 45)]


def test_inset_skewed_quadrilateral_keeps_distance():
    sheet = Sheet([(0, 0), (1000, 20), (990, 600), (-15, 580)])
    inset = sheet.inset(10)
    assert len(inset.corners) == 4
    for corner in inset.corners:
        assert sheet.contains_point(corner)
        assert min(machine_distance(corner, a, b) for a, b in sheet.edges) == pytest.approx(10, abs=1e-6)


def machine_distance(p, a, b):
    from plasmanest.nest import _distance_to_segment
    return _distance_to_segment(p, a, b)


def test_inset_notched_sheet():
    sheet = Sheet([(0, 0), (200, 0), (200, 100), (100, 100), (100, 50), (0, 50)])
    inset = sheet.inset(10)
    assert set(inset.corners) == {(10, 10), (190, 10), (190, 90), (110, 90), (110, 40), (10, 40)}


def test_inset_ignores_corner_on_a_straight_edge():
    inset = Sheet([(0, 0), (50, 0), (100, 0), (100, 50), (0, 50)]).inset(5)
    assert len(inset.corners) == 4


def test_inset_too_large_is_refused():
    with pytest.raises(ValueError):
        Sheet.rectangle(100, 50).inset(25)
    # A narrow neck between two wide parts.
    dumbbell = Sheet([(0, 0), (100, 0), (100, 45), (300, 45), (300, 0), (400, 0), (400, 100),
                      (300, 100), (300, 55), (100, 55), (100, 100), (0, 100)])
    dumbbell.inset(4)
    with pytest.raises(ValueError):
        dumbbell.inset(6)


def test_zero_margin_keeps_the_sheet():
    sheet = Sheet.rectangle(100, 50)
    assert sheet.inset(0) is sheet


# ------------------------------------------------------------------ dialog

@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_shows_new_points_and_makes_the_sheet(app, tmp_path):
    from PyQt5.QtWidgets import QDialogButtonBox
    from plasmanest.gui import MachineDialog
    path = str(tmp_path / "points.txt")
    offsets = {"value": Offsets(g5x=(10.0, 20.0))}
    dialog = MachineDialog(None, path, lambda: offsets["value"])
    ok = dialog.buttons.button(QDialogButtonBox.Ok)
    assert dialog.table.rowCount() == 0 and not ok.isEnabled()
    for x, y in [(10, 20), (1010, 20), (1010, 520), (10, 520)]:
        machine.append_point(MachinePoint((x, y), (x, y), "torch", "2026-11-03T10:00:00"), path)
        dialog.poll()
    assert dialog.table.rowCount() == 4
    assert dialog.table.item(1, 0).text() == "1000.00" and dialog.table.item(1, 1).text() == "0.00"
    assert ok.isEnabled()
    dialog.margin.setValue(5)
    assert sorted(dialog.sheet.corners) == [(5, 5), (5, 495), (995, 5), (995, 495)]

    # Moving the origin moves the sheet, since the points are kept in machine coordinates.
    offsets["value"] = Offsets(g5x=(0.0, 0.0))
    dialog.poll()
    assert dialog.table.item(0, 0).text() == "10.00"
    assert sorted(dialog.sheet.corners)[0] == (15, 25)

    dialog.table.setCurrentCell(3, 0)
    dialog.remove_selected()
    assert len(machine.read_points(path)) == 3 and dialog.table.rowCount() == 3
    dialog.clear()
    assert machine.read_points(path) == [] and not ok.isEnabled()
    dialog.close()


def test_dialog_without_linuxcnc_uses_saved_work_coordinates(app, tmp_path):
    from plasmanest.gui import MachineDialog
    path = str(tmp_path / "points.txt")
    machine.append_point(MachinePoint((500, 500), (7, 8), "laser", "2026-11-03T10:00:00"), path)
    dialog = MachineDialog(None, path, lambda: None)
    assert dialog.table.item(0, 0).text() == "7.00" and dialog.table.item(0, 2).text() == "Laser"
    assert "svarar inte" in dialog.coordinates.text()
    assert "minst tre" in dialog.result.text()
    dialog.close()


def test_dialog_reports_a_broken_points_file(app, tmp_path):
    from plasmanest.gui import MachineDialog
    path = tmp_path / "points.txt"
    path.write_text("garbage\n")
    dialog = MachineDialog(None, str(path), lambda: None)
    assert "Rad 1" in dialog.result.text() and dialog.sheet is None
    dialog.close()
