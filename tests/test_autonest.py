# SPDX-License-Identifier: GPL-2.0-or-later
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from plasmanest.autonest import (EPS, SIMPLIFY_TOLERANCE, Footprint, Obstruction, autonest, convex_hull, find_holes,
                                 figure_points, group_figures, grow_hull, simplify_hull)
from plasmanest.gcode import load_program
from plasmanest.nest import Placement, Sheet, _segments_distance
from plasmanest.shapes import Circle, Polygon, Rectangle

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")


def blocked_interval(moving, x, fixed):
    """Slow but plain reference for Obstruction.interval: the open range of y for which the moving
    footprint, shifted by (x, y), overlaps the fixed one, or None. Both are convex, so the range is one interval, and its ends are found at the
    corners of either polygon."""
    lo, hi = max(moving.minx + x, fixed.minx), min(moving.maxx + x, fixed.maxx)
    if hi - lo <= EPS:
        return None
    columns = {lo, hi}
    columns.update(px + x for px in moving.lower[0] + moving.upper[0] if lo < px + x < hi)
    columns.update(px for px in fixed.lower[0] + fixed.upper[0] if lo < px < hi)
    top = max(Footprint.at(fixed.upper, c) - Footprint.at(moving.lower, c - x) for c in columns)
    bottom = min(Footprint.at(fixed.lower, c) - Footprint.at(moving.upper, c - x) for c in columns)
    return bottom, top


def apply(moves):
    for move in moves:
        move.placement.x, move.placement.y, move.placement.angle = move.x, move.y, move.angle


def segments(placement):
    result = []
    for path in placement.program.paths:
        if path.kind == "cut":
            points = [placement.map_point(p) for p in path.points]
            result += list(zip(points, points[1:]))
    return result


def distance(a, b):
    """Shortest distance between the cuts of two figures; 0 if they cross."""
    sa, sb = segments(a), segments(b)
    return min(_segments_distance(p, q, r, s) for p, q in sa for r, s in sb)


def rectangle(width, height, x=0.0, y=0.0):
    program = Rectangle(0, 0, width, height).program()
    return Placement(program, x, y)


def test_grown_hull_holds_everything_within_the_distance():
    rng = random.Random(3)
    points = [(rng.uniform(-50, 50), rng.uniform(-20, 20)) for _ in range(200)]
    hull = convex_hull(points)
    simple = simplify_hull(hull, 0.5)
    grown = Footprint(grow_hull(simple, 5.5))
    for angle in range(0, 360, 3):
        for x, y in hull:
            p = (x + 5 * math.cos(math.radians(angle)), y + 5 * math.sin(math.radians(angle)))
            assert grown.contains_point(p)
    # And it is not much larger than it has to be.
    assert grown.maxx - max(x for x, _ in hull) < 5.5 * 1.1


def test_simplified_hull_stays_within_tolerance():
    circle = [(100 * math.cos(k * math.pi / 500), 100 * math.sin(k * math.pi / 500)) for k in range(1000)]
    hull = convex_hull(circle)
    simple = simplify_hull(hull, 0.2)
    assert len(simple) < 60
    grown = Footprint(grow_hull(simple, 0.2 + 1e-9))
    assert all(grown.contains_point(p) for p in hull)


def test_blocked_interval_of_two_squares():
    square = Footprint([(0, 0), (10, 0), (10, 10), (0, 10)])
    other = Footprint([(20, 30), (40, 30), (40, 50), (20, 50)])
    obstruction = Obstruction(square, other)
    for check in (blocked_interval, obstruction.interval):
        assert check(square, 15, other) == pytest.approx((20, 50))
        assert check(square, 30, other) == pytest.approx((20, 50))
        assert check(square, 10, other) is None  # only touches the side
        assert check(square, 41, other) is None


def test_rectangles_are_packed_along_the_bottom_edge():
    sheet = Sheet.rectangle(1000, 500)
    figures = [rectangle(100, 50, 2000 + 200 * i, 2000) for i in range(3)]
    moves, unplaced = autonest(sheet, figures, gap=10, margin=10, turn=False)
    assert not unplaced
    apply(moves)
    boxes = sorted(f.bounds() for f in figures)
    for box in boxes:
        assert box[1] == pytest.approx(10, abs=SIMPLIFY_TOLERANCE * 2 + 1e-6)
    assert boxes[0][0] == pytest.approx(10, abs=SIMPLIFY_TOLERANCE * 2 + 1e-6)
    for left, right in zip(boxes, boxes[1:]):
        assert 10 - 1e-6 <= right[0] - left[2] <= 10 + SIMPLIFY_TOLERANCE * 4


def test_turns_a_tall_figure_to_lie_low_only_when_allowed():
    sheet = Sheet.rectangle(1000, 1000)
    figure = rectangle(50, 300, 500, 500)
    moves, _ = autonest(sheet, [figure], gap=10, margin=0, turn=True)
    assert moves[0].angle in (90.0, 270.0)
    moves, _ = autonest(sheet, [figure], gap=10, margin=0, turn=False)
    assert moves[0].angle == 0.0


def test_square_figures_keep_their_angle():
    sheet = Sheet.rectangle(1000, 1000)
    figure = rectangle(100, 100, 500, 500)
    moves, _ = autonest(sheet, [figure], gap=10, margin=0, turn=True)
    assert moves[0].angle == 0.0


def check_nest(sheet, figures, unplaced, gap, margin):
    placed = [f for f in figures if f not in unplaced]
    for figure in placed:
        assert sheet.contains_placement(figure)
        if sheet.is_rectangle:
            x0, y0, x1, y1 = figure.bounds()
            assert x0 >= margin - 1e-6 and y0 >= margin - 1e-6
            assert x1 <= sheet.width - margin + 1e-6 and y1 <= sheet.height - margin + 1e-6
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert distance(a, b) >= gap - 1e-6


def test_mixed_figures_keep_the_gap_and_stay_on_the_sheet():
    flange = load_program(os.path.join(SAMPLES, "flange.ngc"))
    washer = load_program(os.path.join(SAMPLES, "washer.ngc"))
    figures = [Placement(flange, 3000 + 200 * i, 0, 30 * i) for i in range(5)]
    figures += [Placement(washer, 3000 + 200 * i, 1000) for i in range(8)]
    figures.append(Placement(Polygon(((0, 0), (200, 0), (200, 40), (40, 40), (40, 150), (0, 150)),
                                     closed=True, leadin=3).program(), 5000, 5000))
    sheet = Sheet.rectangle(693, 2190)
    moves, unplaced = autonest(sheet, figures, gap=6, margin=10)
    assert not unplaced
    apply(moves)
    check_nest(sheet, figures, unplaced, 6, 10)
    assert max(f.bounds()[3] for f in figures) < 400  # packed low, not spread over the sheet


def test_polygon_sheet_with_a_notch():
    sheet = Sheet([(0, 0), (300, 0), (300, 100), (200, 100), (200, 400), (0, 400)])
    figures = [rectangle(80, 80, 1000 + 100 * i, 1000) for i in range(8)]
    moves, unplaced = autonest(sheet, figures, gap=5, margin=5, turn=False)
    apply(moves)
    assert not unplaced
    check_nest(sheet, figures, unplaced, 5, 0)
    for figure in figures:
        assert sheet.inset(5 - 1e-6).contains_placement(figure)


def test_fixed_figures_are_avoided_and_not_moved():
    sheet = Sheet.rectangle(500, 500)
    fixed = rectangle(480, 100, 250, 60)
    figure = rectangle(100, 50, 1000, 1000)
    moves, unplaced = autonest(sheet, [figure], [fixed], gap=10, margin=10, turn=False)
    apply(moves)
    assert [m.placement for m in moves] == [figure]
    assert (fixed.x, fixed.y) == (250, 60)
    assert figure.bounds()[1] >= fixed.bounds()[3] + 10 - 1e-6
    assert figure.bounds()[1] <= fixed.bounds()[3] + 11


def test_figures_that_do_not_fit_go_beside_the_sheet():
    sheet = Sheet.rectangle(300, 300)
    figures = [rectangle(200, 200, 1000, 1000), rectangle(200, 200, 1500, 1000), rectangle(400, 100, 0, 2000)]
    moves, unplaced = autonest(sheet, figures, gap=10, margin=10)
    apply(moves)
    assert len(unplaced) == 2
    assert sheet.contains_placement(figures[0]) or sheet.contains_placement(figures[1])
    for figure in unplaced:
        assert figure.bounds()[0] > 300
    a, b = unplaced
    assert a.bounds()[3] < b.bounds()[1] or b.bounds()[3] < a.bounds()[1]


def test_a_hole_in_a_part_moves_with_the_part():
    part = Placement(Rectangle(0, 0, 200, 100).program(), 1000, 1000)
    hole = Placement(Circle(0, 0, 20, inside=True).program(), 1050, 1000)
    loose = rectangle(50, 50, 2000, 2000)
    assert sorted(map(sorted, group_figures([part, hole, loose]))) == [[0, 1], [2]]
    offset = (hole.x - part.x, hole.y - part.y)
    moves, _ = autonest(Sheet.rectangle(1000, 1000), [part, hole, loose], gap=10, margin=10, turn=True)
    apply(moves)
    c, s = math.cos(math.radians(part.angle)), math.sin(math.radians(part.angle))
    assert (hole.x - part.x, hole.y - part.y) == pytest.approx((c * offset[0] - s * offset[1],
                                                                s * offset[0] + c * offset[1]))
    assert hole.angle == part.angle


def test_figure_points_are_on_the_sheet():
    figure = rectangle(100, 50, 10, 20)
    xs = [p[0] for p in figure_points(figure)]
    assert min(xs) == pytest.approx(-40) and max(xs) == pytest.approx(60)


def test_negative_gap_is_refused():
    with pytest.raises(ValueError):
        autonest(Sheet.rectangle(100, 100), [rectangle(10, 10)], gap=-1)


def test_obstruction_matches_the_blocked_interval():
    rng = random.Random(7)
    for _ in range(30):
        a = Footprint(convex_hull([(rng.uniform(0, 40), rng.uniform(0, 30)) for _ in range(12)]))
        b = Footprint(convex_hull([(rng.uniform(50, 90), rng.uniform(40, 80)) for _ in range(12)]))
        obstruction = Obstruction(a, b)
        for x in range(0, 100, 3):
            expected, got = blocked_interval(a, x, b), obstruction.interval(a, x, b)
            if expected is None or got is None:
                assert expected is None or expected[1] - expected[0] < 1e-6 or \
                    abs(expected[1] - expected[0]) < 1e-3 or got is None
            else:
                assert got == pytest.approx(expected, abs=1e-6)


# A 300 x 200 part with a hole of 160 mm, whose lead-in runs in from the hole's centre.
HOLED_PART = """G21
G90
G0 X150 Y100
M3 $0 S1
G1 X230 Y100
G3 X230 Y100 I-80 J0
M5 $0
G0 X-5 Y-5
M3 $0 S1
G1 X0 Y0
G1 X300 Y0
G1 X300 Y200
G1 X0 Y200
G1 X0 Y0
M5 $0
M2
"""


def holed_part(x=150, y=100):
    from plasmanest.gcode import Program
    return Placement(Program("holed.ngc", HOLED_PART), x, y)


def test_find_holes_in_the_samples():
    flange = Placement(load_program(os.path.join(SAMPLES, "flange.ngc")), 0, 0)
    washer = Placement(load_program(os.path.join(SAMPLES, "washer.ngc")), 500, 0)
    holes = find_holes([flange, washer], gap=5)
    assert sorted(h.host for h in holes) == [0, 0, 1]
    for hole in holes:
        assert hole.slack < 0.01
    big = find_holes([holed_part()], gap=5)
    assert len(big) == 1
    assert len(big[0].inner) == 1  # the lead-in from the centre


def test_a_non_convex_hole_is_not_used():
    part = Placement(Rectangle(0, 0, 300, 300).program(), 150, 150)
    notch = Placement(Polygon(((50, 50), (250, 50), (250, 100), (100, 100), (100, 250), (50, 250)),
                              closed=True, inside=True).program(), 0, 0)
    notch.x, notch.y = notch.program.center  # where it was drawn
    assert find_holes([part, notch], gap=5) == []


def test_small_figures_go_into_a_hole_and_keep_the_gap_to_its_lead_in():
    sheet = Sheet.rectangle(1000, 600)
    host = holed_part(160, 110)
    small = [rectangle(40, 40, 2000 + 100 * i, 2000) for i in range(2)]
    moves, unplaced = autonest(sheet, small, [host], gap=8, margin=10, turn=False)
    apply(moves)
    assert not unplaced
    hole_box = (160 - 80, 110 - 80, 160 + 80, 110 + 80)
    for move in moves:
        assert move.host is host
        x0, y0, x1, y1 = move.placement.bounds()
        assert hole_box[0] < x0 and x1 < hole_box[2] and hole_box[1] < y0 and y1 < hole_box[3]
    for figure in small:
        assert distance(figure, host) >= 8 - 1e-6
    assert distance(small[0], small[1]) >= 8 - 1e-6


def test_figures_go_into_holes_of_figures_placed_before_them():
    sheet = Sheet.rectangle(1000, 600)
    host = holed_part(2000, 2000)
    small = rectangle(50, 50, 3000, 3000)
    moves, _ = autonest(sheet, [small, host], gap=8, margin=10, turn=False)
    apply(moves)
    assert {m.placement.program.name: m.host for m in moves}["holed.ngc"] is None
    assert [m.host for m in moves if m.placement is small] == [host]
    check_nest(sheet, [small, host], [], 8, 10)


def test_a_figure_too_large_for_the_hole_goes_on_the_sheet():
    sheet = Sheet.rectangle(1000, 600)
    host = holed_part(160, 110)
    large = rectangle(120, 120, 2000, 2000)
    moves, _ = autonest(sheet, [large], [host], gap=8, margin=10, turn=True)
    apply(moves)
    assert moves[0].host is None
    assert large.bounds()[0] >= host.bounds()[2] + 8 - 1e-6
