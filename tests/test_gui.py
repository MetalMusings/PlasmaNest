import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from plasmanest.gcode import Program, load_program
from plasmanest.gui import ROTATE_MIN_RADIUS, MainWindow

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")
FLANS = os.path.join(SAMPLES, "flans.ngc")
BRICKA = os.path.join(SAMPLES, "bricka.ngc")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    QTest.qWaitForWindowExposed(win)
    yield win
    win.saved_state = win.undo_stack[win.undo_index]
    win.close()


def to_view(win, x, y):
    return win.view.mapFromScene(QPointF(x, y))


def drag(win, start, end):
    viewport = win.view.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    for k in range(1, 6):
        # QTest.mouseMove sends no button state, so a drag needs real move events.
        pos = start + (end - start) * k / 5
        QApplication.sendEvent(viewport, QMouseEvent(QEvent.MouseMove, QPointF(pos), QPointF(viewport.mapToGlobal(pos)),
                                                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    win.refresh()


def edge_point(part):
    """A scene point in the solid material of the flange sample."""
    p = part.placement
    return p.map_point((10, 10))


@pytest.mark.skipif(sys.platform == "win32" and os.environ["QT_QPA_PLATFORM"] == "offscreen",
                    reason="message boxes crash Qt's offscreen plugin on Windows; run with QT_QPA_PLATFORM=windows")
def test_warning_dialog_does_not_crash(window, tmp_path):
    from PyQt5.QtCore import QTimer
    path = tmp_path / "warn.ngc"
    path.write_text("G0 X0 Y0\nM3 $0 S1\nG1 X10 Y0\nM5 $0\nM2\n")
    QTimer.singleShot(200, lambda: QApplication.activeModalWidget().accept())
    window.load_paths([str(path)])
    assert len(window.parts) == 1


def test_load_places_figures_on_the_sheet_without_overlap(window):
    window.load_paths([FLANS, BRICKA])
    window.refresh()
    assert len(window.parts) == 2
    assert window.outside_count == 0 and window.overlap_count == 0


def test_drag_moves_figure_with_mouse(window):
    window.load_paths([FLANS])
    part = window.parts[0]
    window.scene_.clearSelection()
    x0, y0 = part.placement.x, part.placement.y
    start = to_view(window, *edge_point(part))
    end = start + QPoint(120, -60)
    drag(window, start, end)
    scale = abs(window.view.transform().m11())
    assert part.placement.x - x0 == pytest.approx(120 / scale, abs=2 / scale)
    assert part.placement.y - y0 == pytest.approx(60 / scale, abs=2 / scale)  # screen up is +Y
    assert window.is_dirty()


def test_handle_rotates_figure(window):
    window.load_paths([FLANS])
    part = window.parts[0]
    part.setSelected(True)
    window.update_handles()
    assert part.handle.isVisible()
    grip = part.handle.scenePos()
    center = part.pos()
    radius = math.hypot(grip.x() - center.x(), grip.y() - center.y())
    # Handle sits straight above the centre at angle 0; drag it to the left: +90 degrees.
    target = to_view(window, center.x() - radius, center.y())
    drag(window, to_view(window, grip.x(), grip.y()), target)
    assert part.placement.angle == pytest.approx(90, abs=2)
    x0, y0, x1, y1 = part.placement.bounds()
    assert x1 - x0 == pytest.approx(60, abs=12) and y1 - y0 == pytest.approx(100, abs=12)  # 1 px is ~6 mm here
    # The graphics and the numbers that get exported must agree.
    mapped = part.mapToScene(QPointF(10, 5))
    expected = part.placement.map_point((part.placement.program.center[0] + 10,
                                         part.placement.program.center[1] + 5))
    assert (mapped.x(), mapped.y()) == pytest.approx(expected, abs=1e-6)


def test_copy_delete_and_undo(window):
    window.load_paths([FLANS])
    window.copy_parts(window.selected_parts(), 3)
    assert len(window.parts) == 4
    assert window.overlap_count == 0
    assert window.parts[0].placement.program is window.parts[3].placement.program
    # The new copies are what is selected after copying.
    window.delete_selection()
    assert len(window.parts) == 1
    window.undo()
    assert len(window.parts) == 4
    window.redo()
    assert len(window.parts) == 1
    window.parts[0].setSelected(True)
    window.delete_programs()
    assert window.parts == []


def test_part_in_hole_is_allowed_but_real_overlap_is_flagged(window):
    big = "G21\nG0 X10 Y10\nM3 $0 S1\nG1 X110 Y10\nG1 X110 Y110\nG1 X10 Y110\nG1 X10 Y10\nM5 $0\n" \
          "G0 X30 Y30\nM3 $0 S1\nG1 X90 Y30\nG1 X90 Y90\nG1 X30 Y90\nG1 X30 Y30\nM5 $0\nM2\n"
    small = "G21\nG0 X0 Y0\nM3 $0 S1\nG1 X10 Y0\nG1 X10 Y10\nG1 X0 Y10\nG1 X0 Y0\nM5 $0\nM2\n"
    from plasmanest.nest import Placement
    a = window.add_part(Placement(Program("big", big), 500, 500))
    b = window.add_part(Placement(Program("small", small), 500, 500))
    window.rebuild_list()
    window.refresh()
    assert window.overlap_count == 0, "a part inside a hole is what nesting is for"
    b.set_center(500 + 30, 500)  # across the hole edge
    window.refresh()
    assert window.overlap_count == 2
    b.set_center(800, 800)
    window.refresh()
    assert window.overlap_count == 0
    a.set_center(760, 800)  # small now lies wholly within the solid ring of big
    window.refresh()
    assert window.overlap_count == 2


def test_outside_sheet_is_flagged(window):
    window.load_paths([FLANS])
    window.parts[0].set_center(-500, -500)
    window.refresh()
    assert window.outside_count == 1
    assert window.parts[0].state == "outside"


def test_sheet_change_flags_figures(window):
    window.load_paths([FLANS])
    window.parts[0].set_center(2900, 100)
    window.sheet_width.setValue(1000)
    window.sheet_changed()
    assert window.outside_count == 1


def test_project_and_export_round_trip(window, tmp_path):
    window.load_paths([FLANS, BRICKA])
    window.parts[0].set_angle(33)
    window.parts[1].set_center(700, 400)
    window.refresh()
    project = tmp_path / "n.pnest"
    assert window.write_project(str(project))
    ngc = tmp_path / "n.ngc"
    assert window.export_to(str(ngc))
    assert b"\r" not in ngc.read_bytes()
    reparsed = load_program(str(ngc))
    assert reparsed.bounds is not None
    before = ngc.read_text()

    window.new_project()
    assert window.parts == []
    window.open_project_path(str(project))
    assert len(window.parts) == 2
    assert window.export_to(str(ngc))
    assert ngc.read_text() == before
    assert not window.is_dirty()


def test_reorder_changes_cut_order(window):
    window.load_paths([FLANS, BRICKA])
    window.scene_.clearSelection()
    window.parts[1].setSelected(True)
    window.reorder(-1)
    assert window.parts[0].placement.program.name == "bricka.ngc"
    assert window.parts[0].label.text() == "1"
    assert window.part_list.item(0).text().startswith("1.  bricka")


def test_placement_fields_set_lower_left_corner(window):
    window.load_paths([FLANS])
    part = window.parts[0]
    part.setSelected(True)
    window.pos_x.setValue(300)
    window.pos_y.setValue(200)
    x0, y0, _, _ = part.placement.bounds()
    assert (x0, y0) == pytest.approx((300, 200))


def test_screenshot(window, tmp_path_factory):
    window.load_paths([FLANS, BRICKA])
    window.copy_parts([window.parts[0]], 2)
    window.parts[1].set_angle(30)
    window.parts[-1].set_center(400, 500)
    window.parts[-1].set_angle(45)
    window.parts[-1].setSelected(True)
    window.fit_view()
    window.refresh()
    target = os.environ.get("PLASMANEST_SCREENSHOT")
    if target:
        window.grab().save(target)


def right_drag(win, points, modifiers=Qt.NoModifier):
    """Press the right button on the first scene point and follow the others."""
    viewport = win.view.viewport()
    views = [to_view(win, *p) for p in points]
    QTest.mousePress(viewport, Qt.RightButton, modifiers, views[0])
    for pos in views[1:]:
        QApplication.sendEvent(viewport, QMouseEvent(QEvent.MouseMove, QPointF(pos), QPointF(viewport.mapToGlobal(pos)),
                                                     Qt.NoButton, Qt.RightButton, modifiers))
    QTest.mouseRelease(viewport, Qt.RightButton, modifiers, views[-1])
    win.refresh()


def arc(center, radius, start, end, steps=12):
    return [(center[0] + radius * math.cos(math.radians(start + (end - start) * k / steps)),
             center[1] + radius * math.sin(math.radians(start + (end - start) * k / steps)))
            for k in range(steps + 1)]


@pytest.fixture
def small_sheet(window):
    """A sheet small enough for figures to be many pixels wide, with flange and washer."""
    window.sheet_width.setValue(400)
    window.sheet_height.setValue(250)
    window.sheet_changed()
    window.load_paths([FLANS, BRICKA])
    window.scene_.clearSelection()
    window.parts[0].set_center(120, 80)
    window.parts[1].set_center(300, 150)
    window.fit_view()
    window.refresh()
    window.commit()
    return window


def test_right_drag_rotates_figure_after_the_pointer(small_sheet):
    part = small_sheet.parts[0]
    # Start on the solid material to the right of the centre, go a quarter turn counter-clockwise.
    right_drag(small_sheet, arc((120, 80), 40, 0, 90))
    assert part.isSelected()
    assert part.placement.angle == pytest.approx(90, abs=2)
    assert (part.placement.x, part.placement.y) == pytest.approx((120, 80))
    assert part.rotation() == pytest.approx(part.placement.angle)


def test_right_drag_clockwise_gives_the_opposite_angle(small_sheet):
    right_drag(small_sheet, arc((120, 80), 40, 0, -30))
    assert small_sheet.parts[0].placement.angle == pytest.approx(330, abs=2)


def test_right_drag_with_ctrl_snaps_to_steps(small_sheet):
    part = small_sheet.parts[0]
    part.set_angle(7)
    right_drag(small_sheet, arc((120, 80), 40, 0, 50), Qt.ControlModifier)
    assert part.placement.angle == pytest.approx(60)


def test_right_drag_only_affects_the_figure_under_the_pointer(small_sheet):
    other = small_sheet.parts[1]
    other.setSelected(True)
    right_drag(small_sheet, arc((120, 80), 40, 0, 45))
    assert other.placement.angle == 0 and not other.isSelected()
    assert small_sheet.parts[0].isSelected()


def test_right_drag_on_a_selected_figure_rotates_the_group_rigidly(small_sheet):
    a, b = small_sheet.parts
    a.setSelected(True)
    b.setSelected(True)
    before = b.placement.map_point((10, 10))
    pivot = small_sheet.selection_pivot([a, b])
    start = math.degrees(math.atan2(80 - pivot[1], 120 - pivot[0]))
    radius = math.hypot(120 - pivot[0], 80 - pivot[1])
    right_drag(small_sheet, arc(pivot, radius, start, start + 90))
    assert a.placement.angle == pytest.approx(90, abs=2)
    assert a.placement.angle == pytest.approx(b.placement.angle)
    turned = math.radians(a.placement.angle)
    dx, dy = before[0] - pivot[0], before[1] - pivot[1]
    expected = (pivot[0] + dx * math.cos(turned) - dy * math.sin(turned),
                pivot[1] + dx * math.sin(turned) + dy * math.cos(turned))
    assert b.placement.map_point((10, 10)) == pytest.approx(expected, abs=1e-6)


def test_pointer_near_the_centre_does_not_spin_the_figure(small_sheet):
    part = small_sheet.parts[0]
    scale = abs(small_sheet.view.transform().m11())
    near = (ROTATE_MIN_RADIUS - 5) / scale
    right_drag(small_sheet, [(120, 80), (120 + near, 80), (120, 80 + near), (120 - near, 80)])
    assert part.placement.angle == 0
    # Leaving the dead zone starts from the angle there, without a jump.
    right_drag(small_sheet, [(120, 80), (120, 120)])
    assert part.placement.angle == pytest.approx(0, abs=1e-6)


def test_right_click_without_moving_only_selects(small_sheet):
    part = small_sheet.parts[0]
    steps = small_sheet.undo_index
    right_drag(small_sheet, [(160, 80)])
    assert part.isSelected() and part.placement.angle == 0
    assert small_sheet.undo_index == steps


def test_right_drag_on_empty_space_still_pans(small_sheet):
    origin = small_sheet.view.mapToScene(QPoint(300, 300))
    right_drag(small_sheet, [(200, 230), (250, 230)])
    assert small_sheet.view.mapToScene(QPoint(300, 300)).x() != pytest.approx(origin.x())
    assert [p.placement.angle for p in small_sheet.parts] == [0, 0]


def test_rotation_by_mouse_is_one_undo_step_and_reaches_the_export(small_sheet, tmp_path):
    part = small_sheet.parts[0]
    steps = small_sheet.undo_index
    right_drag(small_sheet, arc((120, 80), 40, 0, 90))
    assert small_sheet.undo_index == steps + 1
    ngc = tmp_path / "n.ngc"
    small_sheet.export_to(str(ngc))
    assert f"a={part.placement.angle:.6f}".rstrip("0") in ngc.read_text()
    small_sheet.undo()
    assert small_sheet.parts[0].placement.angle == 0
