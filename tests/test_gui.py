import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from plasmanest.gcode import Program, load_program
from plasmanest.gui import ROTATE_MIN_RADIUS, CornerDialog, MainWindow
from plasmanest.nest import Sheet

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


OFFCUT = [(0, 0), (800, 0), (800, 250), (520, 500), (300, 500), (300, 300), (150, 300), (150, 500), (0, 500)]


def click(win, x, y, modifiers=Qt.NoModifier):
    QTest.mouseClick(win.view.viewport(), Qt.LeftButton, modifiers, to_view(win, x, y))


def press_key(win, key):
    QTest.keyClick(win.view, key)


def draw(win, corners):
    win.toggle_drawing()
    for x, y in corners:
        click(win, x, y)


@pytest.fixture
def coarse(window):
    """A view where one pixel is a known, small part of a large sheet, for exact whole-mm clicks."""
    window.set_sheet(Sheet.rectangle(800, 500))
    window.commit()
    return window


def assert_corners_close(sheet, corners, tolerance=2.0):
    assert len(sheet.corners) == len(corners)
    for got, wanted in zip(sheet.corners, corners):
        assert math.hypot(got[0] - wanted[0], got[1] - wanted[1]) <= tolerance, (got, wanted)


def test_draw_polygon_and_finish_with_enter(coarse):
    corners = [(100, 100), (700, 100), (700, 400), (300, 450)]
    draw(coarse, corners)
    assert coarse.view.drawing is not None and coarse.draw_button.text() == "Avbryt"
    press_key(coarse, Qt.Key_Return)
    assert coarse.view.drawing is None and coarse.draw_button.text() == "Rita"
    assert_corners_close(coarse.sheet, corners)
    assert all(float(c).is_integer() for corner in coarse.sheet.corners for c in corner)
    assert not coarse.sheet.is_rectangle
    assert "4 hörn" in coarse.sheet_info.text()


def test_click_on_first_corner_closes_the_polygon(coarse):
    corners = [(100, 100), (700, 100), (400, 400)]
    draw(coarse, corners)
    click(coarse, 100, 100)
    assert coarse.view.drawing is None
    assert_corners_close(coarse.sheet, corners)


def test_double_click_places_the_last_corner_and_finishes(coarse):
    draw(coarse, [(100, 100), (700, 100)])
    viewport = coarse.view.viewport()
    pos = to_view(coarse, 400, 400)
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, pos)
    QTest.mouseDClick(viewport, Qt.LeftButton, Qt.NoModifier, pos)
    assert coarse.view.drawing is None
    assert_corners_close(coarse.sheet, [(100, 100), (700, 100), (400, 400)])


def test_escape_cancels_and_backspace_takes_back(coarse):
    before = coarse.sheet
    draw(coarse, [(100, 100), (700, 100), (700, 400)])
    press_key(coarse, Qt.Key_Backspace)
    assert len(coarse.view.drawing) == 2
    press_key(coarse, Qt.Key_Escape)
    assert coarse.view.drawing is None and coarse.sheet is before
    assert coarse.view.draft.path().isEmpty()
    assert coarse.draw_button.text() == "Rita"


def test_crossing_polygon_is_refused_and_can_be_corrected(coarse):
    before = coarse.sheet
    draw(coarse, [(100, 100), (700, 400), (700, 100), (100, 400)])
    press_key(coarse, Qt.Key_Return)
    assert coarse.view.drawing is not None and coarse.sheet is before
    assert "korsar" in coarse.sheet_info.text()
    press_key(coarse, Qt.Key_Backspace)
    press_key(coarse, Qt.Key_Return)
    assert coarse.view.drawing is None
    assert len(coarse.sheet.corners) == 3


def test_too_few_corners_stay_in_drawing(coarse):
    draw(coarse, [(100, 100), (700, 100)])
    press_key(coarse, Qt.Key_Return)
    assert coarse.view.drawing is not None and "tre" in coarse.sheet_info.text()
    press_key(coarse, Qt.Key_Escape)


def test_shift_gives_straight_edges(coarse):
    coarse.toggle_drawing()
    click(coarse, 100, 100)
    click(coarse, 600, 130, Qt.ShiftModifier)
    click(coarse, 630, 400, Qt.ShiftModifier)
    first, second, third = coarse.view.drawing
    assert second[1] == first[1]
    assert third[0] == second[0]
    press_key(coarse, Qt.Key_Escape)


def test_drawing_does_not_move_or_select_figures(coarse):
    coarse.load_paths([FLANS])
    part = coarse.parts[0]
    coarse.scene_.clearSelection()
    pose = (part.placement.x, part.placement.y)
    coarse.toggle_drawing()
    click(coarse, *edge_point(part))
    assert len(coarse.view.drawing) == 1
    assert not part.isSelected() and (part.placement.x, part.placement.y) == pose
    press_key(coarse, Qt.Key_Escape)
    # Back to normal: the same click now selects.
    click(coarse, *edge_point(part))
    assert part.isSelected()


def test_right_drag_on_a_figure_while_drawing_does_not_rotate_it(coarse):
    coarse.load_paths([FLANS])
    part = coarse.parts[0]
    coarse.scene_.clearSelection()
    coarse.toggle_drawing()
    x, y = part.placement.x, part.placement.y
    right_drag(coarse, arc((x, y), 40, 0, 90))
    assert part.placement.angle == 0 and coarse.view.rotation_drag is None
    press_key(coarse, Qt.Key_Escape)


def test_polygon_decides_what_is_outside(window):
    window.set_sheet(Sheet(OFFCUT))
    window.load_paths([FLANS])
    part = window.parts[0]
    part.set_center(300, 150)
    window.refresh()
    assert window.outside_count == 0 and part.state == "ok"
    part.set_center(225, 400)
    window.refresh()
    assert window.outside_count == 1 and part.state == "outside"
    assert "utanför" in window.status_label.text()
    part.set_center(300, 150)
    window.set_sheet(Sheet([(0, 0), (100, 0), (0, 100)]))
    assert part.state == "outside"


def test_new_figures_are_placed_on_the_polygon(window):
    window.set_sheet(Sheet(OFFCUT))
    window.load_paths([FLANS])
    window.copy_parts(window.parts, 11)
    assert len(window.parts) == 12
    assert window.outside_count == 0 and window.overlap_count == 0


def test_placement_finds_the_arm_beside_a_notch(window):
    # 105 mm wide figure on a 120 mm sheet: fits only without the usual gap to the edge.
    window.set_sheet(Sheet([(0, 0), (120, 0), (120, 400), (0, 400)]))
    window.load_paths([FLANS])
    assert window.outside_count == 0
    # An L-shaped sheet: copies fill the low strip and then go up the arm.
    arm = Sheet([(0, 0), (500, 0), (500, 90), (130, 90), (130, 500), (0, 500)])
    window.set_sheet(arm)
    window.copy_parts(window.parts, 4)
    assert window.outside_count == 0 and window.overlap_count == 0


def test_figure_goes_beside_a_too_small_polygon_and_is_flagged(window):
    window.set_sheet(Sheet([(0, 0), (30, 0), (0, 30)]))
    window.load_paths([FLANS])
    assert window.outside_count == 1
    assert window.parts[0].placement.bounds()[0] > 30


def test_size_fields_follow_the_shape(window):
    window.set_sheet(Sheet(OFFCUT))
    assert window.sheet_width.value() == 800 and window.sheet_height.value() == 500
    assert not window.sheet_width.isEnabled() and window.rectangle_button.isEnabled()
    window.sheet_changed()  # must not turn the polygon back into a rectangle
    assert len(window.sheet.corners) == len(OFFCUT)
    window.make_rectangle()
    assert window.sheet.is_rectangle and window.sheet.bounds == (0, 0, 800, 500)
    assert window.sheet_width.isEnabled() and not window.rectangle_button.isEnabled()
    window.sheet_width.setValue(900)
    window.sheet_changed()
    assert window.sheet.bounds == (0, 0, 900, 500)


def test_undo_covers_the_sheet_shape(window):
    original = window.sheet
    window.set_sheet(Sheet(OFFCUT))
    assert window.is_dirty()
    window.undo()
    assert window.sheet is original and window.sheet_width.isEnabled()
    window.redo()
    assert len(window.sheet.corners) == len(OFFCUT)


def test_undo_during_drawing_ends_the_drawing(coarse):
    coarse.set_sheet(Sheet(OFFCUT))
    draw(coarse, [(100, 100)])
    coarse.undo()
    assert coarse.view.drawing is None and coarse.draw_button.text() == "Rita"
    assert coarse.view.draft.path().isEmpty()


def test_polygon_survives_project_round_trip(window, tmp_path):
    window.set_sheet(Sheet(OFFCUT))
    window.load_paths([FLANS])
    project = tmp_path / "p.pnest"
    assert window.write_project(str(project))
    assert not window.is_dirty()
    window.new_project()
    window.make_rectangle()
    window.saved_state = window.undo_stack[window.undo_index]
    window.open_project_path(str(project))
    assert window.sheet.corners == tuple((float(x), float(y)) for x, y in OFFCUT)
    assert len(window.parts) == 1 and not window.is_dirty()
    assert not window.sheet_width.isEnabled()


def test_window_fits_a_small_machine_screen(window):
    window.set_sheet(Sheet(OFFCUT))
    assert window.minimumSizeHint().height() < 700
    assert window.minimumSizeHint().width() < 1000
    # On a full HD screen everything fits, so there is nothing to scroll.
    window.resize(1280, 1000)
    QTest.qWait(50)
    if os.environ["QT_QPA_PLATFORM"] != "offscreen":  # the offscreen plugin has other font metrics
        assert not window.panel_scroll.verticalScrollBar().isVisible()
    assert window.panel_scroll.width() < 380
    # On a small screen the panel scrolls instead of growing the window.
    window.resize(1024, 600)
    QTest.qWait(50)
    assert window.height() <= 600
    assert window.panel_scroll.verticalScrollBar().maximum() > 0


def test_view_fits_a_polygon_away_from_the_origin(window):
    window.set_sheet(Sheet([(-1000, -800), (-200, -800), (-600, -100)]))
    for corner in window.sheet.corners:
        assert window.view.viewport().rect().contains(to_view(window, *corner))


# ---------------------------------------------------------------- corner dialog

def test_corner_dialog_reads_comma_and_point(window):
    dialog = CornerDialog(window, OFFCUT[:3])
    dialog.table.item(0, 0).setText("1,5")
    dialog.table.item(0, 1).setText(" 2.5 ")
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.sheet.corners[0] == (1.5, 2.5)


def test_corner_dialog_add_and_remove_rows(window):
    dialog = CornerDialog(window, [(0, 0), (100, 0), (100, 100)])
    dialog.table.setCurrentCell(1, 0)
    dialog.add_after_selected()
    assert dialog.table.rowCount() == 4 and dialog.table.currentRow() == 2
    dialog.table.item(2, 0).setText("150")
    dialog.table.item(2, 1).setText("50")
    assert dialog.corners()[2] == (150, 50)
    dialog.remove_selected()
    assert dialog.table.rowCount() == 3


@pytest.mark.parametrize("edit, message", [
    (lambda d: d.table.item(0, 0).setText("abc"), "Rad 1"),
    (lambda d: d.table.item(1, 1).setText(""), "Rad 2"),
    (lambda d: d.table.item(2, 0).setText("nan"), "Rad 3"),
    (lambda d: (d.table.removeRow(0), d.table.removeRow(0)), "tre"),
    (lambda d: d.table.item(0, 0).setText("1000"), "korsar"),
])
def test_corner_dialog_refuses_bad_input_and_stays_open(window, edit, message):
    dialog = CornerDialog(window, [(0, 0), (100, 0), (100, 100), (0, 100)])
    edit(dialog)
    dialog.accept()
    assert dialog.sheet is None and dialog.result() != QDialog.Accepted
    assert message in dialog.error.text()


def test_export_goes_where_the_latest_file_button_looks(window, tmp_path, monkeypatch):
    import glob
    import time
    from plasmanest import gui
    monkeypatch.setattr(gui, "NC_FILES", str(tmp_path / "missing"))
    assert window.export_dir() is None
    assert window.export_suggestion() == "nest.ngc"

    nc_files = tmp_path / "nc_files"
    nc_files.mkdir()
    monkeypatch.setattr(gui, "NC_FILES", str(nc_files))
    directory = nc_files / "plasmanest"
    assert window.export_suggestion() == str(directory / "nest.ngc") and directory.is_dir()
    window.project_path = str(tmp_path / "plåt 3.pnest")
    assert window.export_suggestion() == str(directory / "plåt 3.ngc")

    window.load_paths([FLANS])
    assert window.export_to(str(directory / "a.ngc"))
    assert "Ladda nest" in window.statusBar().currentMessage()
    time.sleep(0.02)
    assert window.export_to(str(directory / "b.ngc"))
    time.sleep(0.02)
    assert window.export_to(str(directory / "a.ngc"))
    # QtPlasmaC's latest-file: max(glob('<dir>/*.ngc'), key=os.path.getctime)
    assert max(glob.glob(f"{directory}/*.ngc"), key=os.path.getctime) == str(directory / "a.ngc")
    assert window.export_to(str(tmp_path / "elsewhere.ngc"))
    assert "hittar bara filer i" in window.statusBar().currentMessage()


def test_embedded_mode_announces_the_window_and_stops_on_sigterm(tmp_path):
    import signal
    import subprocess
    import time
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path))
    root = os.path.join(os.path.dirname(__file__), "..")
    process = subprocess.Popen([sys.executable, os.path.join(root, "plasmanest.py"), "--embed"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        line = process.stdout.readline()
        assert int(line) > 0
        time.sleep(0.5)
        process.send_signal(signal.SIGTERM)
        assert process.wait(10) == 0
        # Nothing more on stdout: QtVCP never reads the pipe again.
        assert process.stdout.read() == b""
    finally:
        if process.poll() is None:
            process.kill()


def test_autosave_keeps_unsaved_work(window, tmp_path):
    from plasmanest import gui
    path = str(tmp_path / "auto" / "autosave.pnest")
    assert gui.autosave(window, path) is None
    window.load_paths([FLANS])
    assert gui.autosave(window, path) == path
    window.open_project_path(path)
    assert len(window.parts) == 1


def test_embedded_mode_quits_when_the_host_dies(tmp_path):
    import subprocess
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path))
    root = os.path.join(os.path.dirname(__file__), "..")
    process = subprocess.Popen([sys.executable, os.path.join(root, "plasmanest.py"), "--embed"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        assert int(process.stdout.readline()) > 0
        process.stdin.close()  # what the kernel does when the host process dies
        assert process.wait(10) == 0
    finally:
        if process.poll() is None:
            process.kill()


WRONG_SCREEN_OFFSET = QPointF(-700, 450)  # an embedded window's idea of where it is is wrong


def send_with_wrong_screen_pos(win, kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    viewport = win.view.viewport()
    local = QPointF(pos)
    screen = QPointF(viewport.mapToGlobal(QPoint(0, 0))) + local + WRONG_SCREEN_OFFSET
    QApplication.sendEvent(viewport, QMouseEvent(kind, local, local, screen, button, buttons, Qt.NoModifier))


def test_click_selects_a_figure_even_when_embedded(window):
    window.load_paths([FLANS])
    part = window.parts[0]
    window.scene_.clearSelection()
    point = to_view(window, *edge_point(part))
    send_with_wrong_screen_pos(window, QEvent.MouseButtonPress, point)
    send_with_wrong_screen_pos(window, QEvent.MouseButtonRelease, point, buttons=Qt.NoButton)
    assert part.isSelected()


def test_wheel_zoom_keeps_the_point_under_the_pointer_even_when_embedded(window):
    from PyQt5.QtGui import QWheelEvent
    window.view.fit()
    viewport = window.view.viewport()
    pos = QPointF(viewport.width() * 0.8, viewport.height() * 0.25)
    before = window.view.mapToScene(pos.toPoint())
    scale_before = abs(window.view.transform().m11())
    wrong = QPointF(viewport.mapToGlobal(QPoint(0, 0))) + pos + WRONG_SCREEN_OFFSET
    for _ in range(3):
        QApplication.sendEvent(viewport, QWheelEvent(pos, wrong, QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                                     Qt.NoModifier, Qt.NoScrollPhase, False))
    after = window.view.mapToScene(pos.toPoint())
    assert abs(window.view.transform().m11()) == pytest.approx(scale_before * 1.2 ** 3)
    assert abs(after.x() - before.x()) < 1.5 / scale_before and abs(after.y() - before.y()) < 1.5 / scale_before


def test_tooltip_names_the_figure_even_when_embedded(window):
    from PyQt5.QtGui import QHelpEvent
    from PyQt5.QtWidgets import QToolTip
    window.load_paths([FLANS])
    part = window.parts[0]
    viewport = window.view.viewport()
    point = to_view(window, *edge_point(part))
    wrong = viewport.mapToGlobal(point) + WRONG_SCREEN_OFFSET.toPoint()
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, point, wrong))
    assert QToolTip.text() == part.placement.program.name
    QToolTip.hideText()


def test_menus_are_moved_by_the_embedding_offset_but_not_their_sub_menus(window):
    from PyQt5.QtWidgets import QMenu
    from plasmanest.gui import PopupPlacer
    placer = PopupPlacer(window, lambda w: QPoint(300, -120))
    QApplication.instance().installEventFilter(placer)
    try:
        menu = QMenu(window)
        sub = menu.addMenu("Under")
        sub.addAction("x")
        menu.popup(QPoint(400, 400))
        QApplication.processEvents()
        assert menu.pos() == QPoint(700, 280)
        sub.popup(QPoint(500, 500))
        QApplication.processEvents()
        assert sub.pos() == QPoint(500, 500)
        sub.close()
        menu.close()
        # Popups of other windows are left alone.
        other = QMenu()
        other.popup(QPoint(400, 400))
        QApplication.processEvents()
        assert other.pos() == QPoint(400, 400)
        other.close()
    finally:
        QApplication.instance().removeEventFilter(placer)


CONVERSATIONAL = os.path.join(os.path.dirname(__file__), "data", "conversational_circles.ngc")


@pytest.fixture
def messages(monkeypatch):
    from plasmanest import gui
    shown = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(gui.QMessageBox, kind, lambda parent, title, text, *a, kind=kind: shown.append((kind, text)))
    return shown


def test_shapes_from_conversational_become_a_figure(window, messages):
    part = window.load_conversational(CONVERSATIONAL)
    assert part is not None and messages == []
    assert part.placement.program.name.startswith("CONVERSATIONAL ")
    assert window.sheet.contains_placement(part.placement)


def test_conversational_without_shapes_or_with_the_main_file_is_explained(window, messages, tmp_path):
    assert window.load_conversational(str(tmp_path / "missing.ngc")) is None
    empty = tmp_path / "backup.ngc"
    empty.write_text("(new conversational file)\nM2\n")
    assert window.load_conversational(str(empty)) is None
    filtered = tmp_path / "filtered.ngc"
    filtered.write_text(open(CONVERSATIONAL).read() + ";qtplasmac filtered G-code file")
    assert window.load_conversational(str(filtered)) is None
    assert [kind for kind, text in messages] == ["information", "critical", "information"]
    assert "tom" in messages[1][1] and "MAIN" in messages[2][1]
    assert window.parts == []


def test_conversational_button_follows_qtplasmac(monkeypatch):
    from types import SimpleNamespace
    from plasmanest import gui
    answer = lambda out, code=0: (lambda *a, **k: SimpleNamespace(returncode=code, stdout=out))
    assert gui.conversational_disabled(answer("TRUE\n"))
    assert not gui.conversational_disabled(answer("FALSE\n"))
    assert not gui.conversational_disabled(answer("", 1))

    def missing(*a, **k):
        raise FileNotFoundError("halcmd")
    assert not gui.conversational_disabled(missing)


def test_embedded_window_hides_the_button_when_conversational_is_off(window, tmp_path, monkeypatch):
    from plasmanest import gui
    monkeypatch.setattr(gui, "conversational_disabled", lambda: True)
    window.conversational_button.show()
    window.enable_live_export(str(tmp_path))
    assert window.conversational_button.isHidden()


def test_embedded_window_waits_for_the_host_before_showing(window):
    from plasmanest import gui
    window.hide()
    answers = iter([False, False, True])
    gui.show_when_embedded(window, lambda w: next(answers), poll_ms=10)
    QTest.qWait(15)
    assert not window.isVisible()
    assert wait_until(window.isVisible)


def test_embedded_window_shows_anyway_without_a_host(window):
    from plasmanest import gui
    window.hide()
    gui.show_when_embedded(window, lambda w: False, poll_ms=10, timeout_ms=50)
    assert wait_until(window.isVisible)
    window.hide()
    gui.show_when_embedded(window, lambda w: None, poll_ms=10)  # X11 cannot be asked
    assert wait_until(window.isVisible)


def wait_until(condition, ms=2000):
    for _ in range(ms // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


def mouse(win, kind, scene_xy, buttons=Qt.LeftButton, button=Qt.LeftButton):
    viewport = win.view.viewport()
    pos = QPointF(to_view(win, *scene_xy))
    QApplication.sendEvent(viewport, QMouseEvent(kind, pos, QPointF(viewport.mapToGlobal(pos.toPoint())),
                                                 button, buttons, Qt.NoModifier))


def zoom_in(win, x, y):
    """4 pixels per mm, so that clicks land within a quarter mm of the scene point."""
    from PyQt5.QtGui import QTransform
    win.user_has_view = True
    win.view.setTransform(QTransform.fromScale(4, -4))
    win.view.centerOn(x, y)


def test_draw_rectangle_by_dragging_shows_coordinates_then_size(window):
    zoom_in(window, 220, 90)
    window.toggle_rectangle()
    assert window.rectangle_shape_button.text() == "Avbryt rektangel"
    mouse(window, QEvent.MouseMove, (100.2, 49.8), buttons=Qt.NoButton, button=Qt.NoButton)
    assert window.view.label.text == "X 100   Y 50"
    mouse(window, QEvent.MouseButtonPress, (100.2, 49.8))
    for x, y in ((150, 80), (250, 110), (340.4, 129.6)):
        mouse(window, QEvent.MouseMove, (x, y), button=Qt.NoButton)
    assert window.view.label.text == "240 × 80 mm"
    mouse(window, QEvent.MouseButtonRelease, (340.4, 129.6), buttons=Qt.NoButton)
    assert len(window.parts) == 1
    part = window.parts[0]
    assert part.placement.program.name == "Rektangel 240 × 80"
    assert part.placement.bounds() == (100, 50, 340, 130)
    assert window.view.shape_mode is None and not window.view.label.isVisible()
    assert window.rectangle_shape_button.text() == "Rita rektangel"
    window.undo()
    assert window.parts == []


def test_draw_rectangle_with_two_clicks(window):
    zoom_in(window, 450, 400)
    window.toggle_rectangle()
    mouse(window, QEvent.MouseButtonPress, (500, 500))
    mouse(window, QEvent.MouseButtonRelease, (500, 500), buttons=Qt.NoButton)
    assert window.parts == [] and window.view.shape_start == (500, 500)
    mouse(window, QEvent.MouseMove, (400, 300), buttons=Qt.NoButton, button=Qt.NoButton)
    assert window.view.label.text == "100 × 200 mm"
    mouse(window, QEvent.MouseButtonPress, (400, 300))
    assert len(window.parts) == 1 and window.parts[0].placement.bounds() == (400, 300, 500, 500)


def test_too_small_rectangle_waits_and_escape_cancels(window):
    zoom_in(window, 500, 510)
    window.toggle_rectangle()
    mouse(window, QEvent.MouseButtonPress, (500, 500))
    mouse(window, QEvent.MouseButtonRelease, (500, 500), buttons=Qt.NoButton)
    mouse(window, QEvent.MouseButtonPress, (500.3, 520))  # 0 mm wide after rounding
    assert window.parts == [] and window.view.shape_mode == "rectangle"
    QTest.keyClick(window.view, Qt.Key_Escape)
    assert window.view.shape_mode is None and window.parts == []
    assert window.view.draft.path().isEmpty()


def test_drawing_the_sheet_ends_rectangle_drawing(window):
    window.toggle_rectangle()
    window.toggle_drawing()
    assert window.view.shape_mode is None and window.view.drawing == []
    window.toggle_drawing()


def test_exact_size_can_be_typed_right_after_drawing(window):
    from plasmanest.shapes import rectangle_program
    window.activateWindow()
    window.add_shape(rectangle_program(100, 50, 340, 130))
    assert window.size_w.isVisibleTo(window) and window.size_w.value() == 240 and window.size_h.value() == 80
    assert window.size_w.hasFocus()
    QTest.keyClicks(window.size_w, "250")
    QTest.keyClick(window.size_w, Qt.Key_Tab)
    assert window.size_h.hasFocus()
    window.size_h.selectAll()
    QTest.keyClicks(window.size_h, "120")
    QTest.keyClick(window.size_h, Qt.Key_Return)
    part, = window.parts
    assert part.placement.bounds() == pytest.approx((100, 50, 350, 170))
    assert part.placement.program.name == "Rektangel 250 × 120"
    window.undo()
    assert window.parts[0].placement.bounds() == pytest.approx((100, 50, 350, 130))  # one step per field
    window.undo()
    assert window.parts[0].placement.bounds() == pytest.approx((100, 50, 340, 130))


def test_resized_rectangle_keeps_its_corner_and_cutting_order(window):
    from plasmanest.shapes import rectangle_program
    window.load_paths([FLANS])
    first = window.add_shape(rectangle_program(100, 50, 340, 130))
    window.parts.remove(first)
    window.parts.insert(0, first)
    window.after_change([first])
    window.rotate_selection(30)
    rect = window.parts[0]
    corner = rect.placement.map_point((100, 50))
    window.size_h.setValue(200)
    window.rectangle_edited()
    rect = window.parts[0]
    assert rect.placement.program.name == "Rektangel 240 × 200"
    assert rect.placement.angle == pytest.approx(30)
    assert rect.placement.map_point((100, 50)) == pytest.approx(corner)
    assert window.parts[1].placement.program.name == "flans.ngc"


def test_size_fields_only_for_rectangles(window):
    window.load_paths([FLANS])
    window.parts[0].setSelected(True)
    assert not window.size_w.isVisibleTo(window)


def test_rectangle_settings_from_the_panel(window, monkeypatch):
    from plasmanest.materials import Material
    from plasmanest.shapes import rectangle_of
    from plasmanest import gui
    monkeypatch.setattr(gui, "available_materials", lambda: [Material(1, "1 mm plåt", 1.2), Material(5, "5 mm", 1.6)])
    win = gui.MainWindow()
    try:
        part = win.add_shape(win.new_rectangle(100, 50, 340, 130).program())
        assert win.rect_box.isVisibleTo(win) and win.rect_kerf.isEnabled()
        win.rect_material.setCurrentIndex(win.rect_material.findData(5))
        r = rectangle_of(win.parts[0].placement.program)
        assert r.material == 5 and r.kerf == 1.6 and not win.rect_kerf.isEnabled()
        win.rect_side.setCurrentIndex(1)
        win.rect_leadin.setValue(6)
        win.rectangle_edited()
        r = rectangle_of(win.parts[0].placement.program)
        assert r.inside and r.leadin == 6 and win.parts[0].placement.program.name == "Hål 240 × 80"
        # A new rectangle starts with the settings used last.
        assert win.new_rectangle(0, 0, 10, 10).material == 5 and win.new_rectangle(0, 0, 10, 10).inside
        # An impossible setting is refused with a message and the rectangle stays as it was.
        win.rect_leadin.setValue(500)
        win.rectangle_edited()
        assert rectangle_of(win.parts[0].placement.program).leadin == 6
        assert "får inte plats" in win.rect_error.text() and win.rect_leadin.value() == 6
    finally:
        win.saved_state = win.undo_stack[win.undo_index]
        win.close()


def test_leadin_is_placed_by_pointing_near_the_rectangle(window):
    from plasmanest.shapes import Rectangle, rectangle_of
    zoom_in(window, 220, 90)
    window.add_shape(Rectangle(100, 50, 240, 80, leadin=4).program())
    window.place_leadin()
    assert window.view.shape_mode == "leadin"
    mouse(window, QEvent.MouseMove, (220, 200), buttons=Qt.NoButton, button=Qt.NoButton)  # too far away
    assert window.view.leadin_choice is None and "närmare" in window.view.label.text
    mouse(window, QEvent.MouseMove, (280, 133), buttons=Qt.NoButton, button=Qt.NoButton)
    assert window.view.leadin_choice == (1, 0.75) and "övre" in window.view.label.text
    assert not window.view.draft.path().isEmpty()
    mouse(window, QEvent.MouseButtonPress, (280, 133))
    r = rectangle_of(window.parts[0].placement.program)
    assert (r.edge, r.at) == (1, 0.75) and window.view.shape_mode is None
    cut, = [p for p in window.parts[0].placement.program.paths if p.kind == "cut"]
    assert cut.points[0] == pytest.approx((280, 134)) and cut.points[1] == pytest.approx((280, 130))


def test_leadin_on_a_rotated_rectangle(window):
    from plasmanest.shapes import Rectangle, rectangle_of
    zoom_in(window, 220, 90)
    part = window.add_shape(Rectangle(100, 50, 240, 80, leadin=4).program())
    window.rotate_selection(90)
    part = window.parts[0]
    # The right edge (x=340 before rotating) now points down; its middle is easy to find.
    target = part.placement.map_point((340, 90))
    zoom_in(window, *target)
    window.place_leadin()
    mouse(window, QEvent.MouseMove, (target[0] + 1, target[1] - 1), buttons=Qt.NoButton, button=Qt.NoButton)
    mouse(window, QEvent.MouseButtonPress, (target[0] + 1, target[1] - 1))
    r = rectangle_of(window.parts[0].placement.program)
    assert r.edge == 2 and r.at == pytest.approx(0.5, abs=0.02)


def test_position_fields_of_a_rectangle_give_its_drawn_corner(window):
    from plasmanest.shapes import Rectangle
    window.add_shape(Rectangle(363, 366, 20, 20, kerf=1.2, material=None, leadin=5, edge=1, at=0.03).program())
    assert window.pos_x_label.text() == "X (ritat hörn):"
    assert (window.pos_x.value(), window.pos_y.value()) == pytest.approx((363, 366))
    window.pos_x.setValue(30)
    window.pos_y.setValue(30)
    placement = window.parts[0].placement
    cut, = [p for p in placement.program.paths if p.kind == "cut"]
    points = [placement.map_point(q) for q in cut.points[1:]]
    assert min(x for x, _ in points) == pytest.approx(29.4) and max(x for x, _ in points) == pytest.approx(50.6)
    assert min(y for _, y in points) == pytest.approx(29.4) and max(y for _, y in points) == pytest.approx(50.6)
    # Other figures keep the bounds of the cut path.
    window.load_paths([FLANS])
    window.scene_.clearSelection()
    window.parts[1].setSelected(True)
    assert window.pos_x_label.text() == "X (vänster kant):"


def test_draw_circle_shows_the_diameter_and_types_an_exact_one(window):
    from plasmanest.shapes import Circle, shape_of
    zoom_in(window, 200, 200)
    window.activateWindow()
    window.toggle_circle()
    assert window.circle_shape_button.text() == "Avbryt cirkel"
    mouse(window, QEvent.MouseMove, (200, 200), buttons=Qt.NoButton, button=Qt.NoButton)
    assert window.view.label.text == "X 200   Y 200"
    mouse(window, QEvent.MouseButtonPress, (200, 200))
    for x in (210, 220, 230):
        mouse(window, QEvent.MouseMove, (x, 200), button=Qt.NoButton)
    assert window.view.label.text == "Ø 60 mm"
    mouse(window, QEvent.MouseButtonRelease, (230, 200), buttons=Qt.NoButton)
    part, = window.parts
    assert shape_of(part.placement.program) == Circle(200, 200, 60)
    assert window.rect_box.title() == "Cirkel" and window.size_d.isVisibleTo(window)
    assert not window.size_w.isVisibleTo(window)
    assert window.pos_x_label.text() == "X (centrum):" and window.pos_x.value() == 200
    assert window.size_d.hasFocus()
    QTest.keyClicks(window.size_d, "75")
    QTest.keyClick(window.size_d, Qt.Key_Return)
    assert shape_of(window.parts[0].placement.program).diameter == 75
    assert window.parts[0].placement.map_point((200, 200)) == pytest.approx((200, 200))  # the centre stays


def test_circle_position_fields_move_the_centre(window):
    from plasmanest.shapes import Circle
    window.add_shape(Circle(200, 200, 60, kerf=1.2, leadin=5).program())
    window.pos_x.setValue(50)
    window.pos_y.setValue(40)
    placement = window.parts[0].placement
    assert placement.map_point((200, 200)) == pytest.approx((50, 40))


def test_leadin_is_placed_by_pointing_near_the_circle(window):
    from plasmanest.shapes import Circle, shape_of
    zoom_in(window, 200, 200)
    window.add_shape(Circle(200, 200, 60, leadin=5).program())
    window.place_leadin()
    mouse(window, QEvent.MouseMove, (200, 232), buttons=Qt.NoButton, button=Qt.NoButton)
    assert window.view.leadin_choice == pytest.approx(90) and "90°" in window.view.label.text
    mouse(window, QEvent.MouseButtonPress, (200, 232))
    c = shape_of(window.parts[0].placement.program)
    assert c.angle == pytest.approx(90)
    cut, = [p for p in window.parts[0].placement.program.paths if p.kind == "cut"]
    assert cut.points[0] == pytest.approx((200, 235)) and cut.points[1] == pytest.approx((200, 230))
