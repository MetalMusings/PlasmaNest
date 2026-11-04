import json
import math
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from plasmanest.gcode import GCodeError, Program, load_program, tokenize
from plasmanest.nest import Placement, Sheet, export_gcode, load_project, material_warning, save_project

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")
BASIC = """G21
G90
M190 P3
M66 P3 L3 Q1
F#<_hal[plasmac.cut-feed-rate]>
G0 X10 Y10
M3 $0 S1
G1 X20 Y10
G3 X20 Y30 I0 J10
G1 X10 Y30
G1 X10 Y10
M5 $0
G0 X0 Y0
M2
"""


def moves(program):
    return [op.move for op in program.ops if op.kind == "move"]


def cut_points(program):
    return [p for path in program.paths if path.kind == "cut" for p in path.points]


def assert_same_points(a, b, tolerance=1e-4):
    assert len(a) == len(b)
    for p, q in zip(a, b):
        assert math.hypot(p[0] - q[0], p[1] - q[1]) < tolerance, (p, q)


def reparse(placements):
    return Program("out", export_gcode(placements))


def test_tokenize_handles_tight_and_bracketed_words():
    assert tokenize("G1X10.5Y-.5F#<_hal[plasmac.cut-feed-rate]>") == [
        ("G", "1"), ("X", "10.5"), ("Y", "-.5"), ("F", "#<_hal[plasmac.cut-feed-rate]>")]
    assert tokenize("M5 $-1") == [("M", "5"), ("$", "-1")]
    assert tokenize("X[1+[2*3]]") == [("X", "[1+[2*3]]")]
    assert tokenize("G53 G0 Z[#<_ini[axis_z]max_limit> - 5]")[2] == ("Z", "[#<_ini[axis_z]max_limit> - 5]")


def test_bounds_ignore_rapids_and_parking():
    program = Program("a", BASIC)
    x0, y0, x1, y1 = program.bounds
    assert (x0, y0) == pytest.approx((10, 10))
    assert (x1, y1) == pytest.approx((30, 30), abs=1e-6)
    assert moves(program)[-1].cut, "parking move must be dropped"


@pytest.mark.parametrize("angle", [0, 90, 180, 270, 33.3, -47.1, 359.9])
def test_export_matches_transformed_source(angle):
    program = Program("a", BASIC)
    placement = Placement(program, 500, 300, angle)
    out = reparse([placement])
    expected = [placement.map_point(p) for p in cut_points(program)]
    assert_same_points(cut_points(out), expected)


@pytest.mark.parametrize("angle", [0, 17, 90, 123.456])
def test_arcs_stay_within_linuxcnc_radius_tolerance(angle):
    out = reparse([Placement(load_program(os.path.join(SAMPLES, "flans.ngc")), 1234.5678, 987.654, angle)])
    arcs = [m for m in moves(out) if m.center is not None]
    assert arcs
    for arc in arcs:
        cx, cy = arc.start[0] + arc.center[0], arc.start[1] + arc.center[1]
        assert abs(math.hypot(arc.start[0] - cx, arc.start[1] - cy)
                   - math.hypot(arc.end[0] - cx, arc.end[1] - cy)) < 0.002


def test_rotation_keeps_arc_direction_and_is_counter_clockwise():
    program = Program("a", BASIC)
    placement = Placement(program, 100, 100, 90)
    assert placement.map_vector((1, 0)) == pytest.approx((0, 1))
    out = reparse([placement])
    assert [m.mode for m in moves(out)] == [m.mode for m in moves(program)]


def test_several_parts_are_one_program_with_a_single_end():
    program = Program("a", BASIC)
    text = export_gcode([Placement(program, 100, 100), Placement(program, 200, 100, 45)])
    lines = text.splitlines()
    assert lines[-1] == "M2"
    assert sum(1 for l in lines if l.startswith("M2")) == 1
    assert sum(1 for l in lines if l.startswith("M3")) == 2
    # Same material twice: selected only once, and its wait line goes with it.
    assert sum(1 for l in lines if l.startswith("M190")) == 1
    assert sum(1 for l in lines if l.startswith("M66")) == 1
    assert sum(1 for l in lines if l.startswith("F#<_hal")) == 2


def test_material_is_reselected_when_it_changes():
    a, b = Program("a", BASIC), Program("b", BASIC.replace("M190 P3", "M190 P4"))
    lines = export_gcode([Placement(a), Placement(b), Placement(a)]).splitlines()
    assert [l for l in lines if l.startswith("M190")] == ["M190 P3", "M190 P4", "M190 P3"]
    assert sum(1 for l in lines if l.startswith("M66")) == 3


def test_material_comes_after_compensation_off():
    src = BASIC.replace("G0 X10 Y10", "G41.1 D1.5\nG0 X10 Y10")
    lines = export_gcode([Placement(Program("a", src)), Placement(Program("b", src.replace("P3", "P5", 1)))]).splitlines()
    m190 = [i for i, l in enumerate(lines) if l == "M190 P5"][0]
    assert any(l.startswith("G40") for l in lines[m190 - 3:m190])


def test_magic_comment_materials():
    permanent = "(o=1, nu=7, na=Steel, ph=3, pd=0.1, ch=1, fr=2000)\n"
    temporary = "(o=0, nu=2, na=Tmp, ph=3, pd=0.1, ch=1, fr=2000)\n"
    body = BASIC.replace("M190 P3\nM66 P3 L3 Q1\n", "")
    a, b = Program("a", temporary + body), Program("b", permanent + body)
    lines = export_gcode([Placement(a), Placement(a), Placement(b)]).splitlines()
    assert lines.index(permanent.strip()) < lines.index(temporary.strip())
    assert sum(1 for l in lines if l.startswith("(o=0")) == 1
    assert sum(1 for l in lines if l.startswith("(o=1")) == 1


def test_incremental_file_and_spotting_micro_move_survive():
    src = """G21
G90
G0 X10 Y10
M3 $2 S1
F99999
G91
G1 X0.000001
G90
M5 $2
G0 X50 Y10
G91
M3 $0 S1
G1 X10
G1 Y10
G1 X-10
G90
M5 $0
M2
"""
    program = Program("a", src)
    for angle in (0, 45, 90):
        placement = Placement(program, 200, 200, angle)
        out = reparse([placement])
        micro = [m for m in moves(out) if m.incremental][0]
        assert math.hypot(micro.end[0] - micro.start[0], micro.end[1] - micro.start[1]) > 0
        assert_same_points(cut_points(out), [placement.map_point(p) for p in cut_points(program)])


def test_incremental_first_move_starts_at_the_part_not_at_previous_end():
    src = "G21\nG91\nG0 X10 Y10\nM3 $0 S1\nG1 X10\nG1 Y10\nM5 $0\nM2\n"
    program = Program("a", src)
    placement = Placement(program, 500, 500)
    out = reparse([Placement(program, 100, 100), placement])
    assert_same_points(cut_points(out)[-3:], [placement.map_point(p) for p in cut_points(program)])


def test_inch_file_is_converted_to_mm():
    src = "G20\nG90\nG64 P0.004\nF100\nG0 X1 Y1\nM3 $0 S1\nG1 X2 Y1 F50\nG2 X3 Y2 I0 J1\nM5 $0\nM2\n"
    program = Program("a", src)
    assert program.bounds[0] == pytest.approx(25.4)
    text = export_gcode([Placement(program, 100, 100)])
    assert "G20" not in text and "G21" in text
    assert "F2540" in text and "F1270" in text
    assert "G64 P0.1016" in text


def test_r_arcs_become_ij():
    for r, big in ((20, False), (-20, True)):
        src = f"G21\nG0 X0 Y0\nM3 $0 S1\nG1 X10 Y0\nG3 X10 Y20 R{r}\nM5 $0\nM2\n"
        move = moves(Program("a", src))[-1]
        cx = move.start[0] + move.center[0]
        cy = move.start[1] + move.center[1]
        assert math.hypot(cx - 10, cy) == pytest.approx(20)
        assert math.hypot(cx - 10, cy - 20) == pytest.approx(20)
        assert (cx < 10) != big  # CCW upward: short way has its centre on the left


def test_absolute_ij_mode():
    src = "G21\nG90.1\nG0 X10 Y0\nM3 $0 S1\nG3 X10 Y0 I5 J0\nM5 $0\nM2\n"
    assert moves(Program("a", src))[-1].center == pytest.approx((-5, 0))


def test_expressions_and_parameters_are_resolved():
    src = "G21\n#<w> = 50\n#1 = [#<w> / 2]\nG0 X0 Y0\nM3 $0 S1\nG1 X#<w> Y[#1 + SIN[90]]\nM5 $0\nM2\n"
    assert moves(Program("a", src))[-1].end == pytest.approx((50, 26))


@pytest.mark.parametrize("line", [
    "G92 X0 Y0", "G52 X10", "G10 L2 P1 X0", "G18", "o100 sub", "o<x> call",
    "G1 X#<_hal[foo]> Y0", "G1 A10", "G81 X0 Y0 R1 Z-1", "G5 X1 Y1 I1 J1 P1 Q1",
])
def test_unsafe_gcode_is_refused(line):
    with pytest.raises(GCodeError) as error:
        Program("a", BASIC.replace("G0 X10 Y10", line + "\nG0 X10 Y10"))
    assert "Line" in str(error.value)


def test_files_without_motion_are_refused():
    with pytest.raises(GCodeError):
        Program("a", "G21\nM2\n")


def test_file_without_torch_warns():
    program = Program("a", "G21\nG0 X0 Y0\nG1 X10 Y0\nM2\n")
    assert any("M3" in w for w in program.warnings)


def test_torch_left_on_is_switched_off_between_parts():
    src = "G21\nG0 X0 Y0\nM3 $0 S1\nG1 X10 Y0\nM2\n"
    text = export_gcode([Placement(Program("a", src), 50, 50)])
    assert text.index("M5 $-1") > text.index("G1 X")


def test_conversational_header_is_removed():
    program = Program("a", ";conversational block\n" + BASIC)
    assert "conversational" not in export_gcode([Placement(program)])


def test_already_filtered_file_is_refused():
    # QtPlasmaC writes the marker last; exported, the file would be filtered a second time.
    with pytest.raises(GCodeError, match="filter"):
        Program("a", BASIC + ";qtplasmac filtered G-code file")
    with pytest.raises(GCodeError, match="filter"):
        Program("a", ";qtplasmac filtered G-code file\n" + BASIC)


def test_export_has_no_constructs_the_filter_rejects():
    text = export_gcode([Placement(load_program(os.path.join(SAMPLES, n)), 100, 100, 30)
                         for n in ("flans.ngc", "bricka.ngc")])
    assert "G92 " not in text.replace("G92.1", "")
    assert not any(" R" in l for l in text.splitlines() if not l.startswith(";"))
    # Every M3 must be preceded by a G0/G1 XY move since the last M5.
    moved = False
    for line in text.splitlines():
        if line.startswith(("G0 ", "G1 ")) and "X" in line:
            moved = True
        elif line.startswith("M3"):
            assert moved
        elif line.startswith("M5"):
            moved = False


def test_export_avoids_filter_hole_sensing_bugs():
    # The filter (2.9.4 and master) crashes on G90.1 arcs with J when hole sensing is on,
    # and only updates its last position on lines containing X.
    src = (BASIC.replace("G0 X", "G90.1\nG0 X", 1).replace("I0 J10", "I20 J20")
           .replace("G1 X10 Y10\nM5", "G1 Y10\nM5"))
    assert "G90.1" in src and "\nG1 Y10\n" in src
    text = export_gcode([Placement(Program("a", src), 100, 100, 30)])
    assert "G90.1" not in text
    for line in text.splitlines():
        if re.match(r"G[0-3] ", line):
            assert " X" in line and " Y" in line, line


def test_material_warning():
    a, b = Program("a", BASIC), Program("b", BASIC.replace("M190 P3\nM66 P3 L3 Q1\n", ""))
    assert material_warning([Placement(a), Placement(a)]) is None
    assert "b" in material_warning([Placement(a), Placement(b)])


def test_rectangle_uses_rotated_bounds():
    program = Program("a", "G21\nG0 X0 Y0\nM3 $0 S1\nG1 X100 Y0\nG1 X100 Y10\nM5 $0\nM2\n")
    sheet = Sheet.rectangle(100, 10)
    assert sheet.is_rectangle
    assert sheet.contains_placement(Placement(program, 50, 5))
    assert not sheet.contains_placement(Placement(program, 50, 5, 90))


# An offcut with a torn off corner and a notch in the top edge.
OFFCUT = [(0, 0), (800, 0), (800, 250), (520, 500), (300, 500), (300, 300), (150, 300), (150, 500), (0, 500)]
SQUARE = "G21\nG0 X0 Y0\nM3 $0 S1\nG1 X40 Y0\nG1 X40 Y40\nG1 X0 Y40\nG1 X0 Y0\nM5 $0\nM2\n"
BAR = "G21\nG0 X0 Y0\nM3 $0 S1\nG1 X200 Y0\nM5 $0\nM2\n"


@pytest.mark.parametrize("corners, message", [
    ([(0, 0), (10, 0)], "three"),
    ([(0, 0), (10, 0), (0, 0)], "three"),
    ([(0, 0), (10, 0), (20, 0)], "straight line"),
    ([(0, 0), (100, 100), (100, 0), (0, 100)], "cross"),
    ([(0, 0), (100, 0), (50, 0), (50, 50)], "fold back"),
    ([(0, 0), (100, 0), (100, 100), (50, 0), (0, 100)], "cross"),
])
def test_invalid_sheets_are_refused(corners, message):
    with pytest.raises(ValueError, match=message):
        Sheet(corners)


def test_sheet_cleans_repeated_corners_and_reports_size():
    sheet = Sheet([(0, 0), (0, 0), (10, 0), (10, 20), (0, 20), (0, 0)])
    assert len(sheet.corners) == 4 and sheet.is_rectangle
    assert (sheet.width, sheet.height, sheet.area) == (10, 20, 200)
    triangle = Sheet([(0, 0), (10, 0), (0, 10)])
    assert not triangle.is_rectangle and triangle.area == 50
    assert not Sheet([(0, 0), (10, 0), (15, 10), (5, 10)]).is_rectangle


def test_points_on_the_edge_are_on_the_sheet():
    sheet = Sheet(OFFCUT)
    for point in [(0, 0), (400, 0), (150, 400), (660, 375)]:
        assert sheet.contains_point(point)
    for point in [(225, 400), (790, 490), (-1, 100), (900, 100)]:
        assert not sheet.contains_point(point)


def test_segment_over_a_notch_is_off_the_sheet_though_its_ends_are_on_it():
    sheet = Sheet(OFFCUT)
    assert sheet.contains_point((100, 400)) and sheet.contains_point((400, 400))
    assert not sheet.contains_segment((100, 400), (400, 400))
    assert sheet.contains_segment((100, 200), (400, 200))
    # Along an edge, and between two corners of the notch's far side.
    assert sheet.contains_segment((0, 0), (800, 0))
    assert sheet.contains_segment((150, 300), (300, 300))
    # Through the notch's corner into the notch, and touching that corner from below.
    assert not sheet.contains_segment((100, 250), (200, 350))
    assert sheet.contains_segment((100, 350), (200, 250))


@pytest.mark.parametrize("x, y, angle, expected", [
    (300, 150, 0, True),      # in the open
    (225, 400, 0, False),     # in the notch
    (225, 305, 0, False),     # across the notch's bottom edge
    (740, 450, 0, False),     # in the torn off corner
    (650, 370, 0, False),     # across the torn edge
    (780, 20, 0, True),       # tight in a real corner
    (20, 480, 0, True),       # in the arm left of the notch
    (2000, 100, 0, False),    # beyond the sheet
])
def test_polygon_decides_on_the_cut_geometry(x, y, angle, expected):
    assert Sheet(OFFCUT).contains_placement(Placement(Program("a", SQUARE), x, y, angle)) is expected


def test_cut_bridging_a_notch_is_off_the_sheet():
    bar = Program("a", BAR)
    sheet = Sheet(OFFCUT)
    assert not sheet.contains_placement(Placement(bar, 225, 400))
    assert sheet.contains_placement(Placement(bar, 225, 200))
    # Turned upright, the same figure fits in the arm beside the notch.
    assert sheet.contains_placement(Placement(bar, 75, 400, 90))


def test_rapids_may_leave_the_sheet():
    program = Program("a", SQUARE.replace("G0 X0 Y0", "G0 X-500 Y-500\nG0 X0 Y0"))
    assert Sheet(OFFCUT).contains_placement(Placement(program, 300, 150))


def test_box_check():
    sheet = Sheet(OFFCUT)
    assert sheet.contains_box(10, 10, 200, 200)
    assert not sheet.contains_box(100, 250, 350, 400)
    assert not sheet.contains_box(-10, 10, 200, 200)


def test_sheet_may_lie_anywhere_relative_to_the_origin():
    sheet = Sheet([(-100, -50), (100, -50), (0, 150)])
    program = Program("a", SQUARE)
    assert sheet.contains_placement(Placement(program, 0, 0))
    assert not sheet.contains_placement(Placement(program, 90, 100))


def test_export_does_not_depend_on_the_sheet_shape():
    # Same placement means the same G-code; the sheet is only a check.
    placement = Placement(Program("a", SQUARE), 300, 150)
    text = export_gcode([placement])
    assert Sheet(OFFCUT).contains_placement(placement)
    assert export_gcode([placement]) == text


def test_project_with_polygon_round_trip(tmp_path):
    placements = [Placement(Program("a.ngc", SQUARE), 300, 150, 12)]
    path = tmp_path / "x.pnest"
    save_project(str(path), Sheet(OFFCUT), placements)
    sheet, loaded = load_project(str(path))
    assert sheet.corners == tuple((float(x), float(y)) for x, y in OFFCUT)
    assert export_gcode(loaded) == export_gcode(placements)


def test_version_1_project_opens_as_a_rectangle(tmp_path):
    path = tmp_path / "old.pnest"
    path.write_text(json.dumps({
        "version": 1, "sheet": {"width": 1500, "height": 3000},
        "programs": [{"name": "a.ngc", "text": SQUARE}],
        "figures": [{"program": 0, "x": 1, "y": 2, "angle": 3}]}), encoding="utf-8")
    sheet, loaded = load_project(str(path))
    assert sheet.is_rectangle and (sheet.width, sheet.height) == (1500, 3000)
    assert (loaded[0].x, loaded[0].y, loaded[0].angle) == (1, 2, 3)


def test_project_with_a_broken_polygon_is_refused(tmp_path):
    path = tmp_path / "bad.pnest"
    path.write_text(json.dumps({"version": 2, "sheet": {"corners": [[0, 0], [1, 1]]},
                                "programs": [], "figures": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(str(path))


def test_unknown_project_version_is_refused(tmp_path):
    path = tmp_path / "new.pnest"
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(str(path))


def test_project_round_trip(tmp_path):
    a = Program("a.ngc", BASIC)
    placements = [Placement(a, 1, 2, 3), Placement(a, 4, 5, 6)]
    path = tmp_path / "x.pnest"
    save_project(str(path), Sheet.rectangle(1500, 3000), placements)
    sheet, loaded = load_project(str(path))
    assert (sheet.width, sheet.height) == (1500, 3000) and sheet.is_rectangle
    assert [(p.x, p.y, p.angle) for p in loaded] == [(1, 2, 3), (4, 5, 6)]
    assert loaded[0].program is loaded[1].program
    assert export_gcode(loaded) == export_gcode(placements)


def test_rectangle_is_cut_clockwise_from_the_lower_left_corner():
    from plasmanest.shapes import rectangle_program
    program = rectangle_program(250, 120, 10, 20)
    assert program.name == "Rectangle 240 × 100"
    cut, = [p for p in program.paths if p.kind == "cut"]
    assert cut.points == [(10, 20), (10, 120), (250, 120), (250, 20), (10, 20)]
    assert program.center == (130, 70)
    # Exported like any figure, it obeys the filter's rules.
    text = export_gcode([Placement(program, 130, 70)])
    assert "M3" in text and "G40" in text


def test_too_small_rectangle_is_refused():
    from plasmanest.shapes import rectangle_program
    with pytest.raises(ValueError):
        rectangle_program(10, 10, 10.5, 50)


def test_rectangles_are_recognised_but_not_other_figures():
    from plasmanest.shapes import Rectangle, rectangle_of, rectangle_program
    assert rectangle_of(rectangle_program(100, 50, 340, 130)) == Rectangle(100, 50, 240, 80)
    assert rectangle_of(load_program(os.path.join(SAMPLES, "flans.ngc"))) is None
    tampered = rectangle_program(0, 0, 10, 10).text.replace("G1 X10 Y10", "G1 X12 Y10")
    assert rectangle_of(Program("x", tampered)) is None


def test_outside_cut_moves_out_half_the_kerf_clockwise_with_leadin_from_outside():
    from plasmanest.shapes import Rectangle, rectangle_of
    r = Rectangle(100, 50, 240, 80, kerf=1.2, material=3, leadin=4, edge=0, at=0.5)
    program = r.program()
    assert rectangle_of(program) == r
    assert "M190 P3" in program.text and "M66 P3 L3 Q1" in program.text
    cut, = [p for p in program.paths if p.kind == "cut"]
    assert cut.points == pytest.approx([(95.4, 90), (99.4, 90), (99.4, 130.6), (340.6, 130.6), (340.6, 49.4),
                                        (99.4, 49.4), (99.4, 90)])


def test_inside_cut_moves_in_half_the_kerf_counter_clockwise_with_leadin_from_inside():
    from plasmanest.shapes import Rectangle, rectangle_of
    r = Rectangle(0, 0, 50, 30, inside=True, kerf=1.6, leadin=5, edge=3, at=0.5)
    program = r.program()
    assert rectangle_of(program) == r and program.name == "Hole 50 × 30"
    cut, = [p for p in program.paths if p.kind == "cut"]
    assert cut.points == pytest.approx([(25, 5.8), (25, 0.8), (49.2, 0.8), (49.2, 29.2), (0.8, 29.2),
                                        (0.8, 0.8), (25, 0.8)])


def test_impossible_rectangles_are_refused():
    from plasmanest.shapes import Rectangle
    with pytest.raises(ValueError, match="does not fit"):
        Rectangle(0, 0, 50, 30, inside=True, kerf=1.6, leadin=29, edge=3).gcode()
    with pytest.raises(ValueError, match="too small"):
        Rectangle(0, 0, 50, 1.5, inside=True, kerf=1.6).gcode()
    with pytest.raises(ValueError):
        Rectangle(0, 0, 50, 30, leadin=-1).gcode()


def test_nearest_edge_for_the_leadin():
    from plasmanest.shapes import Rectangle
    r = Rectangle(0, 0, 100, 50)
    assert r.nearest_edge((-3, 25))[:2] == (0, 0.5)
    assert r.nearest_edge((25, 52))[:2] == (1, 0.25)
    assert r.nearest_edge((104, 10))[:2] == (2, 0.8)
    edge, at, distance = r.nearest_edge((50, -7))
    assert (edge, at) == (3, 0.5) and distance == pytest.approx(7)


def test_placement_unmaps_what_it_maps():
    program = load_program(os.path.join(SAMPLES, "flans.ngc"))
    placement = Placement(program, 300, 200, 37)
    assert placement.unmap_point(placement.map_point((12.5, -3))) == pytest.approx((12.5, -3))


def test_circle_part_is_cut_clockwise_outside_the_kerf_with_leadin_from_outside():
    from plasmanest.shapes import Circle, shape_of
    c = Circle(100, 100, 50, kerf=1.2, material=1, leadin=4)
    program = c.program()
    assert shape_of(program) == c and program.name == "Circle Ø50"
    assert "G2 X74.4 Y100 I25.6 J0" in program.text and "M190 P1" in program.text
    cut, = [p for p in program.paths if p.kind == "cut"]
    assert cut.points[0] == pytest.approx((70.4, 100)) and cut.points[1] == pytest.approx((74.4, 100))
    radii = [math.hypot(x - 100, y - 100) for x, y in cut.points[1:]]
    assert min(radii) == pytest.approx(25.6, abs=1e-6) and max(radii) == pytest.approx(25.6, abs=1e-6)


def test_circle_hole_is_cut_counter_clockwise_inside_the_kerf_with_leadin_from_inside():
    from plasmanest.shapes import Circle, shape_of
    c = Circle(0, 0, 40, inside=True, kerf=1.6, leadin=5, angle=90)
    program = c.program()
    assert shape_of(program) == c and program.name == "Hole Ø40"
    assert "G3 X0 Y19.2 I0 J-19.2" in program.text and "M67" not in program.text
    cut, = [p for p in program.paths if p.kind == "cut"]
    assert cut.points[0] == pytest.approx((0, 14.2))


def test_small_hole_is_cut_slowly_with_half_circle_leads_from_the_centre():
    from plasmanest.shapes import Circle, shape_of
    c = Circle(10, 20, 20, inside=True, kerf=1.6, leadin=5, angle=90)  # the lead-in field is not used
    program = c.program()
    assert shape_of(program) == c
    lines = program.text.split("\n")
    start = lines.index("M3 $0 S1")
    assert lines[start - 1] == "G0 X10 Y20"
    # the filter's hole sensing would add its own overburn
    assert lines[start + 1].startswith("#<holes>=0")
    assert lines[start + 2].startswith("M67 E3 Q60")
    # half a circle of radius 4.6 out to the edge, tangent to the hole, then the whole hole
    assert lines[start + 3] == "G3 X10 Y29.2 I0 J4.6"
    assert lines[start + 4] == "G3 X10 Y29.2 I0 J-9.2"
    # back in along the same small circle, 2 mm along the arc
    assert lines[start + 5].startswith("G3 ") and lines[start + 5].endswith("I0 J-4.6")
    assert lines[start + 6:start + 8] == ["M5 $0", "M68 E3 Q0 (full speed)"]
    cut, = [p for p in program.paths if p.kind == "cut"]
    end = cut.points[-1]
    assert math.hypot(end[0] - 10, end[1] - 24.6) == pytest.approx(4.6, abs=1e-6)
    assert math.atan2(end[1] - 24.6, end[0] - 10) - math.pi / 2 == pytest.approx(2 / 4.6, abs=1e-6)
    assert max(math.hypot(x - 10, y - 20) for x, y in cut.points) == pytest.approx(9.2, abs=1e-6)
    # a tiny hole ends with the whole half circle, back in the centre
    tiny = Circle(0, 0, 3, inside=True, kerf=1.6).program()
    assert "G3 X0 Y0 I0.35 J0" in tiny.text
    # from 30 mm, and parts of any size, as before
    assert "M67" not in Circle(0, 0, 30, inside=True, kerf=1.6).gcode()
    assert "M67" not in Circle(0, 0, 10, kerf=1.6).gcode()


def test_impossible_circles_are_refused():
    from plasmanest.shapes import Circle
    with pytest.raises(ValueError, match="does not fit"):
        Circle(0, 0, 40, inside=True, kerf=1.6, leadin=20).gcode()
    with pytest.raises(ValueError, match="too small"):
        Circle(0, 0, 1.5, inside=True, kerf=1.6).gcode()
    with pytest.raises(ValueError):
        Circle(0, 0, 0.5).gcode()


def test_circle_nearest_and_export_through_rotation():
    from plasmanest.shapes import Circle
    c = Circle(0, 0, 40)
    assert c.nearest((0, 25)) == (90, pytest.approx(5))
    assert c.nearest((-19, 0))[0] == 180
    program = Circle(100, 100, 50, kerf=1.2, leadin=4).program()
    text = export_gcode([Placement(program, 300, 200, 30)])
    assert "G2 " in text and "I" in text

