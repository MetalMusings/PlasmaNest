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

from plasmanest.gcode import Program, load_program
from plasmanest.gui import MainWindow
from plasmanest.nest import (Placement, Sheet, closed_loops, export_gcode, load_project, mirrored_block,
                             save_project)
from plasmanest.reverse import reversed_program

HERE = os.path.dirname(__file__)
FLANGE = os.path.join(HERE, "..", "samples", "flange.ngc")
WASHER = os.path.join(HERE, "..", "samples", "washer.ngc")
CONVERSATIONAL = os.path.join(HERE, "data", "conversational_circles.ngc")

# A part with a straight lead-in square to its bottom edge, and an anticlockwise contour with an
# arc, in a box from 0,-5 to 30,30.
PART = """G21 G90
G0 X5 Y-5
M3 $0 S1
G1 X5 Y0
G1 X20 Y0
G3 X30 Y10 I0 J10
G1 X30 Y30
G1 X0 Y30
G1 X0 Y0
G1 X5 Y0
M5 $0
M2
"""

# A square hole, anticlockwise, with an arc lead-in that runs into its bottom edge along it, an
# overcut of 3 mm and a straight lead-out up into the hole.
HOLE = """G21 G90
G0 X10 Y5
M3 $0 S1
G3 X15 Y0 I0 J5
G1 X30 Y0
G1 X30 Y30
G1 X0 Y30
G1 X0 Y0
G1 X15 Y0
G1 X18 Y0
G1 X18 Y4
M5 $0
M2
"""


def cut_points(program, placement=None):
    points = [p for path in program.paths if path.kind == "cut" for p in path.points]
    return [placement.map_point(p) for p in points] if placement else points


def close(a, b, tolerance=1e-5):
    return all(math.hypot(p[0] - q[0], p[1] - q[1]) <= tolerance for p, q in zip(a, b)) and len(a) == len(b)


def signed_areas(program, placement=None):
    """Signed area of each closed contour, in cutting order: positive anticlockwise."""
    result = []
    for path in program.paths:
        if path.kind != "cut":
            continue
        points = [placement.map_point(p) for p in path.points] if placement else path.points
        for start, end in closed_loops(path.points):
            loop = points[start:end]
            result.append(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:] + loop[:1])) / 2)
    return result


def cut_moves(program):
    return [op.move for op in program.ops if op.kind == "move" and op.move.torch]


def moves_text(program):
    """The program's moves as G-code, without the other lines."""
    from plasmanest.gcode import format_number as f
    lines = []
    for op in program.ops:
        if op.kind == "move":
            m = op.move
            text = f"G{m.mode} X{f(round(m.end[0], 6))} Y{f(round(m.end[1], 6))}"
            if m.center is not None:
                text += f" I{f(round(m.center[0], 6))} J{f(round(m.center[1], 6))}"
            lines.append(text)
    return lines


def test_map_and_unmap_are_inverse_when_mirrored():
    program = Program("part", PART)
    for angle in (0.0, 30.0, 90.0, 215.0):
        placement = Placement(program, 100.0, 50.0, angle, mirror=True)
        for point in ((0.0, 0.0), (30.0, 10.0), (12.5, 27.0)):
            back = placement.unmap_point(placement.map_point(point))
            assert math.hypot(back[0] - point[0], back[1] - point[1]) < 1e-9


def test_mirror_flips_left_to_right_about_the_centre():
    program = Program("part", PART)
    placement = Placement(program, 15.0, 12.5, 0.0, mirror=True)
    assert placement.map_point((20.0, 0.0)) == pytest.approx((10.0, 0.0))
    assert placement.map_vector((0.0, 10.0)) == pytest.approx((0.0, 10.0))
    assert placement.map_vector((10.0, 0.0)) == pytest.approx((-10.0, 0.0))


def test_mirrored_export_keeps_the_lead_in_and_the_cut_direction():
    program = Program("part", PART)
    text = export_gcode([Placement(program, 15.0, 12.5, 0.0, mirror=True)])
    lines = text.splitlines()
    cut = lines[lines.index("M3 $0 S1") - 1:lines.index("M5 $0")]
    # The same lead-in, mirrored, square to the edge; then the contour, anticlockwise as before.
    assert cut == ["G0 X25 Y-5", "M3 $0 S1", "G1 X25 Y0", "G1 X30 Y0", "G1 X30 Y30", "G1 X0 Y30",
                   "G1 X0 Y10", "G3 X10 Y0 I10 J0", "G1 X25 Y0"]
    assert ";part 1 x=15 y=12.5 a=0 mirrored" in text
    plain = export_gcode([Placement(program, 15.0, 12.5)])
    assert "G3 X30 Y10 I0 J10" in plain and "mirrored" not in plain


def test_an_arc_lead_in_keeps_its_shape_and_meets_the_contour_the_other_way():
    turned = reversed_program(Program("hole", HOLE))
    assert moves_text(turned) == [
        "G0 X20 Y5",
        "G2 X15 Y0 I0 J5",   # the arc lead-in reflected in the normal at X15 Y0: tangent again
        "G1 X0 Y0", "G1 X0 Y30", "G1 X30 Y30", "G1 X30 Y0", "G1 X15 Y0",
        "G1 X12 Y0",         # the 3 mm overcut, along the contour the new way
        "G1 X12 Y4",         # the lead-out reflected in the normal where it leaves the contour
    ]
    assert turned.center == Program("hole", HOLE).center


def test_a_lead_in_into_a_corner_keeps_its_angle_to_the_contour():
    # A lead-in at 30 degrees to the bottom edge, into the bottom left corner from outside.
    text = PART.replace("G0 X5 Y-5", "G0 X-8.660254 Y-5").replace("G1 X5 Y0\nG1 X20 Y0", "G1 X0 Y0\nG1 X20 Y0")
    text = text.replace("G1 X0 Y0\nG1 X5 Y0\nM5", "G1 X0 Y0\nM5")
    turned = reversed_program(Program("corner", text))
    lead_in = cut_moves(turned)[0]
    assert lead_in.start == pytest.approx((-5.0, -8.660254))  # now 30 degrees to the left edge
    assert lead_in.end == pytest.approx((0.0, 0.0))
    assert cut_moves(turned)[1].end == pytest.approx((0.0, 30.0))


def test_the_overcut_and_the_torch_off_line_stay_where_they_were():
    turned = reversed_program(Program("conversational", open(CONVERSATIONAL).read()))
    text = turned.text.splitlines()
    hole = text[text.index("M67 E3 Q60 (reduce feed rate to 60%)"):]
    assert hole[:5] == ["M67 E3 Q60 (reduce feed rate to 60%)", "G1 X93.459262 Y93.459262",
                        "G2 X93.459262 Y93.459262 I6.540738 J6.540738", "M62 P3 (disable torch)",
                        "G2 X91.428498 Y96.522665 I6.540738 J6.540738"]


@pytest.mark.parametrize("angle", [0.0, 45.0, 90.0, 300.0])
def test_mirrored_export_cuts_the_mirror_image_the_same_way_round(angle):
    """Read back, the export cuts the mirror image the same way round, holes anticlockwise and
    the outside as before, where the view shows it."""
    for source in (Program("part", PART), Program("hole", HOLE), load_program(FLANGE), load_program(WASHER),
                   Program("conversational", open(CONVERSATIONAL).read())):
        placement = Placement(source, 400.0, 300.0, angle, mirror=True)
        again = Program("export", export_gcode([placement]))
        original = signed_areas(source)
        assert signed_areas(again) == pytest.approx(original, abs=0.05)
        assert signed_areas(source, placement) == pytest.approx([-a for a in original], abs=0.05)
        assert close(cut_points(again), cut_points(placement.cut_program, placement))
        assert again.bounds == pytest.approx(placement.bounds(), abs=0.01)


def test_flange_holes_are_cut_anticlockwise_when_mirrored():
    """QtPlasmaC warns about holes cut clockwise; mirrored, they stay anticlockwise."""
    text = export_gcode([Placement(load_program(FLANGE), 200, 200, 30, mirror=True)])
    holes = [line for line in text.splitlines() if line.startswith(("G2 ", "G3 "))]
    assert len(holes) == 2 and all(line.startswith("G3 ") for line in holes)


@pytest.mark.parametrize("text, reason", [
    (PART.replace("G1 X5 Y0\nG1 X20", "G41.1 D1.5\nG1 X5 Y0\nG1 X20"), "cutter compensation"),
    (PART.replace("G1 X30 Y30", "G1 X30 Y30\nM67 E3 Q50"), "in the middle of"),
    (PART.replace("G1 X0 Y0\nG1 X5 Y0\n", "G1 X0 Y0\nG1 X5 Y0.01\n"), "without closing it"),
    (PART.replace("G1 X20 Y0", "G91\nG1 X15 Y0\nG90"), "incremental"),
    # Reflected in the normal at X5 Y0, the hooked lead-in would come up through the bottom edge.
    (PART.replace("G0 X5 Y-5\nM3 $0 S1\n", "G0 X-1 Y5\nM3 $0 S1\nG1 X-1 Y-2\n"), "cross a contour"),
    # A hole: reflected, the pierce point would come 0.5 mm from its left edge instead of 3 mm.
    ("G21 G90\nG0 X9.5 Y3\nM3 $0 S1\nG1 X5 Y0\nG1 X30 Y0\nG1 X30 Y30\nG1 X0 Y30\nG1 X0 Y0\nG1 X5 Y0\n"
     "M5 $0\nM2\n", "pierce 0.50 mm from its contour"),
])
def test_figures_that_cannot_be_turned_round_are_not_mirrored(text, reason):
    program = Program("refused", text)
    with pytest.raises(ValueError, match=reason):
        reversed_program(program)
    with pytest.raises(ValueError, match=reason):
        export_gcode([Placement(program, mirror=True)])


def test_an_open_cut_stays_as_it_is():
    program = Program("slit", "G21 G90\nG0 X0 Y0\nM3 $0 S1\nG1 X10 Y0\nG1 X10 Y5\nM5 $0\nM2\n")
    assert [op.move for op in reversed_program(program).ops if op.kind == "move"] == \
        [op.move for op in program.ops if op.kind == "move"]


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


def test_a_project_with_a_figure_that_cannot_be_mirrored_is_refused(tmp_path):
    path = tmp_path / "nest.pnest"
    save_project(path, Sheet.rectangle(1000, 500), [Placement(Program("part", PART), 50, 60, 0, True, True)])
    data = json.loads(path.read_text())
    data["programs"][0]["text"] = PART.replace("G1 X5 Y0\nG1 X20", "G41.1 D1.5\nG1 X5 Y0\nG1 X20")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="is mirrored, but"):
        load_project(path)


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
    return [(q.x(), q.y()) for q in (transform.map(QPointF(x - cx, y - cy))
                                     for x, y in cut_points(part.placement.cut_program))]


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
    assert close(view_points(part), cut_points(part.placement.cut_program, part.placement), 1e-6)
    assert "mirrored" in window.part_list.item(0).text()

    assert window.mirror_selection(upside_down=True)  # both flips: turned 180 degrees
    assert not part.placement.mirror
    assert close(cut_points(part.placement.program, part.placement), [(2 * x - px, 2 * y - py) for px, py in before], 1e-6)
    assert close(view_points(part), cut_points(part.placement.program, part.placement), 1e-6)

    window.undo()
    part = window.parts[0]
    assert part.placement.mirror
    assert close(view_points(part), cut_points(part.placement.cut_program, part.placement), 1e-6)
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
    assert close(view_points(triangle), cut_points(triangle.placement.cut_program, triangle.placement), 1e-6)
    window.undo()
    assert not any(p.placement.mirror for p in window.parts)


def test_the_window_refuses_a_figure_that_cannot_be_turned_round(window):
    text = PART.replace("G1 X5 Y0\nG1 X20", "G41.1 D1.5\nG1 X5 Y0\nG1 X20")
    window.add_part(Placement(Program("compensated", text), 300, 300))
    window.add_part(Placement(load_program(FLANGE), 600, 300))
    window.after_change(window.parts)
    window.select_all()
    assert not window.mirror_selection()
    assert not any(p.placement.mirror for p in window.parts)
    assert "compensated cannot be mirrored: " in window.statusBar().currentMessage()


def test_the_view_shows_the_reflected_lead_in_and_pierce_point(window):
    # The file's first rapid starts at an unknown place and is not drawn: one more before it.
    program = Program("hole", HOLE.replace("G0 X10 Y5", "G0 X-20 Y-20\nG0 X10 Y5"))
    window.add_part(Placement(program, 300, 300))
    window.after_change(window.parts)
    part = window.parts[0]
    part.setSelected(True)
    assert window.mirror_selection()
    assert close(view_points(part), cut_points(part.placement.cut_program, part.placement), 1e-6)
    cx, cy = program.center
    local = part.rapid_path.currentPosition()  # the rapid ends at the new pierce point
    assert (local.x(), local.y()) == pytest.approx((20.0 - cx, 5.0 - cy))
    mapped = part.sceneTransform().map(local)
    assert (mapped.x(), mapped.y()) == pytest.approx(part.placement.map_point((20.0, 5.0)))
    window.undo()
    assert cut_points(window.parts[0].placement.cut_program) == cut_points(program)


def test_autonest_uses_the_reflected_lead_in_of_a_mirrored_figure():
    from plasmanest.autonest import autonest
    program = Program("hole", HOLE)
    sheet = Sheet(SLANTED)
    moves, unplaced = autonest(sheet, [Placement(program) for _ in range(3)], gap=2, margin=0, mirror=True)
    placed = [Placement(program, m.x, m.y, m.angle, mirror=m.mirror) for m in moves]
    assert not unplaced and all(sheet.contains_placement(p) for p in placed)
