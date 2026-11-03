"""Figures made in PlasmaNest itself, written as G-code so they work like any loaded file. No Qt.

First version: the path follows the drawn size (no kerf compensation), there is no lead-in, and no
material is chosen, so the figure is cut with the material active in QtPlasmaC.
"""

import re

from .gcode import Program, format_number

MIN_SIZE = 1.0  # mm


def rectangle_gcode(x0, y0, x1, y1):
    """An outer contour, cut clockwise from the lower left corner, as plasma outer contours are."""
    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))
    width, height = right - left, top - bottom
    if width < MIN_SIZE or height < MIN_SIZE:
        raise ValueError(f"Rektangeln måste vara minst {format_number(MIN_SIZE)} mm åt båda hållen.")
    n = format_number
    return "\n".join([
        f";PlasmaNest rektangel {n(width)} x {n(height)} mm",
        "G21 G40 G90",
        "F#<_hal[plasmac.cut-feed-rate]>",
        f"G0 X{n(left)} Y{n(bottom)}",
        "M3 $0 S1",
        f"G1 X{n(left)} Y{n(top)}",
        f"G1 X{n(right)} Y{n(top)}",
        f"G1 X{n(right)} Y{n(bottom)}",
        f"G1 X{n(left)} Y{n(bottom)}",
        "M5 $0",
        "M2",
        "",
    ])


def rectangle_program(x0, y0, x1, y1):
    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))
    name = f"Rektangel {format_number(right - left)} × {format_number(top - bottom)}"
    return Program(name, rectangle_gcode(x0, y0, x1, y1))


def rectangle_of(program):
    """(left, bottom, width, height) of a rectangle made by PlasmaNest, or None for any other figure.

    Recognised by its first line and checked against the path, so a hand-edited file is not
    taken for one.
    """
    first = program.text.lstrip().split("\n", 1)[0]
    match = re.match(r";PlasmaNest rektangel ([0-9.]+) x ([0-9.]+) mm", first)
    if not match:
        return None
    cuts = [p for p in program.paths if p.kind == "cut"]
    if len(cuts) != 1:
        return None
    xs, ys = [x for x, _ in cuts[0].points], [y for _, y in cuts[0].points]
    left, bottom, width, height = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if abs(width - float(match.group(1))) > 1e-6 or abs(height - float(match.group(2))) > 1e-6:
        return None
    return left, bottom, width, height
