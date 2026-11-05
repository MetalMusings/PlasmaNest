# SPDX-License-Identifier: GPL-2.0-or-later
"""Mirrored figures: left to right, or upside down, which is the same flip turned 180 degrees."""
import json
import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt5 import sip
from PyQt5.QtCore import QPointF
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from plasmanest.gcode import Program
from plasmanest.gui import MainWindow
from plasmanest.nest import Placement, Sheet, export_gcode, load_project, mirrored_block, save_project

FLANGE = os.path.join(os.path.dirname(__file__), "..", "samples", "flange.ngc")

# A part with an anticlockwise arc and compensation to the left, in a box from 0,0 to 30,30.
PART = """G21 G90
G0 X0 Y0
M3 $0 S1
G41.1 D1.5 (kerf)
G1 X20 Y0
G3 X30 Y10 I0 J10
G1 X30 Y30
G40
M5 $0
M2
"""


def cut_points(program, placement=None):
    points = [p for path in program.paths if path.kind == "cut" for p in path.points]
    return [placement.map_point(p) for p in points] if placement else points


def close(a, b, tolerance=1e-5):
    return all(math.hypot(p[0] - q[0], p[1] - q[1]) <= tolerance for p, q in zip(a, b)) and len(a) == len(b)


def test_map_and_unmap_are_inverse_when_mirrored():
    program = Program("part", PART)
    for angle in (0.0, 30.0, 90.0, 215.0):
        placement = Placement(program, 100.0, 50.0, angle, mirror=True)
        for point in ((0.0, 0.0), (30.0, 10.0), (12.5, 27.0)):
            back = placement.unmap_point(placement.map_point(point))
            assert math.hypot(back[0] - point[0], back[1] - point[1]) < 1e-9


def test_mirror_flips_left_to_right_about_the_centre():
    program = Program("part", PART)
    placement = Placement(program, 15.0, 15.0, 0.0, mirror=True)
    assert placement.map_point((20.0, 0.0)) == pytest.approx((10.0, 0.0))
    assert placement.map_vector((0.0, 10.0)) == pytest.approx((0.0, 10.0))
    assert placement.map_vector((10.0, 0.0)) == pytest.approx((-10.0, 0.0))


def test_export_reverses_arcs_and_compensation():
    program = Program("part", PART)
    placement = Placement(program, 15.0, 15.0, 0.0, mirror=True)
    text = export_gcode([placement])
    assert "G1 X10 Y0" in text
    assert "G2 X0 Y10 I0 J10" in text
    assert "G42.1 D1.5 (kerf)" in text
    assert "G41.1" not in text and "G3 " not in text
    assert ";part 1 x=15 y=15 a=0 mirrored" in text
    # Not mirrored, nothing changes.
    plain = export_gcode([Placement(program, 15.0, 15.0)])
    assert "G3 X30 Y10 I0 J10" in plain and "G41.1 D1.5" in plain and "mirrored" not in plain


@pytest.mark.parametrize("angle", [0.0, 45.0, 90.0, 300.0])
def test_exported_mirror_cuts_where_the_view_shows_it(angle):
    """Read back, the export cuts the same points, arcs included, as the placement maps."""
    for source in (Program("part", PART), Program("flange.ngc", open(FLANGE).read())):
        placement = Placement(source, 400.0, 300.0, angle, mirror=True)
        again = Program("export", export_gcode([placement]))
        assert close(cut_points(again), cut_points(source, placement))


def test_mirrored_block_keeps_everything_else():
    assert mirrored_block("G41 D1 ;left") == "G42 D1 ;left"
    assert mirrored_block("G42.1 D#<_hal[plasmac_run.kerf-width-f]>") == "G41.1 D#<_hal[plasmac_run.kerf-width-f]>"
    assert mirrored_block("G40") == "G40"
    assert mirrored_block("(G41 in a comment)") == "(G41 in a comment)"
    assert mirrored_block("#<holes> = 2") == "#<holes> = 2"
    assert mirrored_block("M3 $0 S1") == "M3 $0 S1"
    assert mirrored_block("G410") == "G410"


def test_project_keeps_the_mirror(tmp_path):
    program = Program("part", PART)
    path = tmp_path / "nest.pnest"
    save_project(path, Sheet.rectangle(1000, 500), [Placement(program, 50, 60, 90, True, True),
                                                    Placement(program, 150, 60, 0)])
    data = json.loads(path.read_text())
    assert data["version"] == 3  # an older PlasmaNest refuses it rather than cutting it unmirrored
    _, placements = load_project(path)
    assert [p.mirror for p in placements] == [True, False]
    save_project(path, Sheet.rectangle(1000, 500), [Placement(program, 50, 60)])
    assert json.loads(path.read_text())["version"] == 2


# ------------------------------------------------------------------------- window

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
    win.updating_ui = True
    sip.delete(win)


def view_points(part):
    """The cut points where the view draws them."""
    cx, cy = part.placement.program.center
    transform = part.sceneTransform()
    return [(q.x(), q.y()) for q in (transform.map(QPointF(x - cx, y - cy)) for x, y in cut_points(part.placement.program))]


def test_mirror_buttons_flip_the_figure_in_place(window):
    window.load_paths([FLANGE])
    part = window.parts[0]
    part.set_angle(30.0)
    x, y = part.placement.x, part.placement.y
    before = cut_points(part.placement.program, part.placement)
    assert window.mirror_selection(upside_down=False)
    assert part.placement.mirror and part.placement.angle == pytest.approx(330.0)
    assert (part.placement.x, part.placement.y) == pytest.approx((x, y))
    after = cut_points(part.placement.program, part.placement)
    assert close(after, [(2 * x - px, py) for px, py in before], 1e-6)
    assert close(view_points(part), after, 1e-6)
    assert "mirrored" in window.part_list.item(0).text()

    assert window.mirror_selection(upside_down=True)  # both flips: turned 180 degrees
    assert not part.placement.mirror
    assert close(cut_points(part.placement.program, part.placement), [(2 * x - px, 2 * y - py) for px, py in before], 1e-6)
    assert close(view_points(part), cut_points(part.placement.program, part.placement), 1e-6)

    window.undo()
    part = window.parts[0]
    assert part.placement.mirror and close(view_points(part), after, 1e-6)
    window.undo()
    assert not window.parts[0].placement.mirror


def test_a_group_is_mirrored_as_a_whole(window):
    window.load_paths([FLANGE, FLANGE])
    a, b = window.parts
    window.select_all()
    pivot = window.selection_pivot([a, b])
    before = [cut_points(p.placement.program, p.placement) for p in (a, b)]
    window.mirror_selection(upside_down=True)
    for part, points in zip((a, b), before):
        assert close(cut_points(part.placement.program, part.placement),
                     [(px, 2 * pivot[1] - py) for px, py in points], 1e-6)


def test_copies_keep_the_mirror(window):
    window.load_paths([FLANGE])
    window.parts[0].setSelected(True)
    window.mirror_selection()
    window.copy_parts(window.parts, 1)
    assert [p.placement.mirror for p in window.parts] == [True, True]


def test_drawn_figures_are_not_mirrored(window):
    window.add_shape(window.new_rectangle(0, 0, 100, 50).program())
    assert not window.mirror_selection()
    assert not window.parts[0].placement.mirror


# ------------------------------------------------------------------------ autonest

TRIANGLE = "G21 G90\nG0 X0 Y0\nM3 $0 S1\nG1 X100 Y0\nG1 X0 Y30\nG1 X0 Y0\nM5 $0\nM2\n"
# The bottom edge slopes the way only the triangle's mirror image, upside down, fits against.
SLANTED = [(0, 0), (110, 33), (110, 300), (0, 300)]


def autonest_triangle(**options):
    from plasmanest.autonest import autonest
    program = Program("triangle", TRIANGLE)
    sheet = Sheet(SLANTED)
    moves, unplaced = autonest(sheet, [Placement(program)], gap=0, margin=0, turn=True, **options)
    move, = moves
    placed = Placement(program, move.x, move.y, move.angle, mirror=move.mirror)
    assert not unplaced and sheet.contains_placement(placed)
    return move, placed


def test_autonest_mirrors_only_when_asked_and_when_it_packs_lower():
    move, placed = autonest_triangle()
    assert not move.mirror and placed.bounds()[3] == pytest.approx(60.27, abs=0.05)
    move, placed = autonest_triangle(mirror=True)
    assert move.mirror and move.angle == pytest.approx(180.0)
    assert placed.bounds()[3] == pytest.approx(30.27, abs=0.05)
    move, _ = autonest_triangle(mirror=True, can_mirror=lambda placement: False)
    assert not move.mirror


def test_autonest_keeps_a_symmetric_figure_unmirrored():
    from plasmanest.autonest import autonest
    program = Program("flange.ngc", open(FLANGE).read())
    moves, _ = autonest(Sheet.rectangle(1000, 500), [Placement(program) for _ in range(6)], mirror=True)
    assert not any(move.mirror for move in moves)  # a tie keeps the orientation


def test_autonest_in_the_window_mirrors_loaded_figures_only(window):
    window.set_sheet(Sheet(SLANTED))
    window.load_paths([])
    program = Program("triangle", TRIANGLE)
    window.add_part(Placement(program, 500, 500))
    window.add_shape(window.new_rectangle(300, 300, 340, 312).program())
    window.scene_.clearSelection()
    window.autonest_parts(gap=0, margin=0, turn=True, mirror=True)
    triangle, rectangle = sorted(window.parts, key=lambda p: p.drawn_shape is not None)
    assert triangle.placement.mirror and not rectangle.placement.mirror
    assert close(view_points(triangle), cut_points(program, triangle.placement), 1e-6)
    window.undo()
    assert not any(p.placement.mirror for p in window.parts)
