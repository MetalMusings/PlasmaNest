"""Figures made in PlasmaNest itself, written as G-code so they work like any loaded file. No Qt.

A rectangle keeps its settings in its own G-code, on a comment line, so they survive project
files. Reading them back regenerates the G-code and compares it with the program's text, so a
hand-edited file is never taken for one.
"""

import re
from dataclasses import dataclass, replace

from .gcode import Program, format_number

MIN_SIZE = 1.0  # mm
HEADER = re.compile(r";PlasmaNest rektangel ([0-9.]+) x ([0-9.]+) mm")
PARAMETERS = ";PlasmaNest-parametrar:"

# Edges of the drawn rectangle, each with its start, end and outward normal, in clockwise order.
# A lead-in position is (edge, fraction along it from its start).
EDGE_NAMES = ("vänster", "övre", "höger", "nedre")


@dataclass(frozen=True)
class Rectangle:
    left: float
    bottom: float
    width: float
    height: float
    inside: bool = False      # cut as a hole: kerf inwards, counter-clockwise
    kerf: float = 0.0         # mm, the whole kerf width
    material: int = None      # QtPlasmaC material number, or None for the active one
    leadin: float = 0.0       # mm
    edge: int = 0
    at: float = 0.0

    # ---------------------------------------------------------------- geometry

    def edges(self, offset=0.0):
        """(start, end, outward normal) of each edge, moved outwards by offset."""
        l, b = self.left - offset, self.bottom - offset
        r, t = self.left + self.width + offset, self.bottom + self.height + offset
        return [((l, b), (l, t), (-1.0, 0.0)), ((l, t), (r, t), (0.0, 1.0)),
                ((r, t), (r, b), (1.0, 0.0)), ((r, b), (l, b), (0.0, -1.0))]

    @property
    def offset(self):
        return (-1 if self.inside else 1) * self.kerf / 2

    def check(self):
        n = format_number
        if self.width < MIN_SIZE or self.height < MIN_SIZE:
            raise ValueError(f"Rektangeln måste vara minst {n(MIN_SIZE)} mm åt båda hållen.")
        if self.kerf < 0 or self.leadin < 0:
            raise ValueError("Skärspalt och inledning kan inte vara negativa.")
        if self.inside:
            if min(self.width, self.height) <= self.kerf:
                raise ValueError(f"Hålet är för litet för skärspalten {n(self.kerf)} mm.")
            across = (self.width if self.edge in (0, 2) else self.height) - self.kerf
            if self.leadin >= across:
                raise ValueError(f"Inledningen {n(self.leadin)} mm får inte plats i hålet.")
        if self.edge not in range(4) or not 0.0 <= self.at <= 1.0:
            raise ValueError("Ogiltig plats för inledningen.")

    def path(self):
        """The torch path: lead-in start, cut start, then the corners back to the cut start."""
        edges = self.edges(self.offset)
        start, end, normal = edges[self.edge]
        point = (start[0] + (end[0] - start[0]) * self.at, start[1] + (end[1] - start[1]) * self.at)
        side = -1.0 if self.inside else 1.0  # the lead-in starts in the scrap
        leadin_start = (point[0] + side * normal[0] * self.leadin, point[1] + side * normal[1] * self.leadin)
        if not self.inside:  # clockwise: along the edges as listed
            corners = [edges[(self.edge + k) % 4][1] for k in range(4)]
        else:  # counter-clockwise: the other way round
            corners = [edges[(self.edge - k) % 4][0] for k in range(4)]
        return leadin_start, point, corners

    # ------------------------------------------------------------------ G-code

    def gcode(self):
        self.check()
        n = format_number
        leadin_start, point, corners = self.path()
        settings = (f"{PARAMETERS} left={n(self.left)} bottom={n(self.bottom)} width={n(self.width)} "
                    f"height={n(self.height)} side={'inside' if self.inside else 'outside'} kerf={n(self.kerf)} "
                    f"material={'-' if self.material is None else self.material} leadin={n(self.leadin)} "
                    f"edge={self.edge} at={n(self.at)}")
        lines = [f";PlasmaNest rektangel {n(self.width)} x {n(self.height)} mm", settings, "G21 G40 G90"]
        if self.material is not None:
            lines += [f"M190 P{self.material}", "M66 P3 L3 Q1"]
        lines += ["F#<_hal[plasmac.cut-feed-rate]>", f"G0 X{n(leadin_start[0])} Y{n(leadin_start[1])}", "M3 $0 S1"]
        if self.leadin > 0:
            lines.append(f"G1 X{n(point[0])} Y{n(point[1])}")
        moves = corners + [point]
        last = point if self.leadin > 0 else leadin_start
        for x, y in moves:
            if abs(x - last[0]) > 1e-9 or abs(y - last[1]) > 1e-9:  # a cut start on a corner
                lines.append(f"G1 X{n(x)} Y{n(y)}")
            last = (x, y)
        lines += ["M5 $0", "M2", ""]
        return "\n".join(lines)

    @property
    def name(self):
        kind = "Hål" if self.inside else "Rektangel"
        return f"{kind} {format_number(self.width)} × {format_number(self.height)}"

    def program(self):
        return Program(self.name, self.gcode())

    def nearest_edge(self, point):
        """(edge, fraction, distance in mm) of the drawn edge closest to a point in program coordinates."""
        best = None
        for index, (start, end, _) in enumerate(self.edges()):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length2 = dx * dx + dy * dy
            t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2))
            px, py = start[0] + t * dx, start[1] + t * dy
            distance = ((point[0] - px) ** 2 + (point[1] - py) ** 2) ** 0.5
            if best is None or distance < best[2]:
                best = (index, t, distance)
        return best


def rectangle_program(x0, y0, x1, y1, **settings):
    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))
    return Rectangle(left, bottom, right - left, top - bottom, **settings).program()


def rectangle_of(program):
    """The Rectangle a program was made from, or None for any other figure."""
    lines = program.text.lstrip().split("\n")
    if not lines or not HEADER.match(lines[0]):
        return None
    if len(lines) > 1 and lines[1].startswith(PARAMETERS):
        try:
            values = dict(word.split("=", 1) for word in lines[1][len(PARAMETERS):].split())
            rectangle = Rectangle(
                float(values["left"]), float(values["bottom"]), float(values["width"]), float(values["height"]),
                inside=values["side"] == "inside", kerf=float(values["kerf"]),
                material=None if values["material"] == "-" else int(values["material"]),
                leadin=float(values["leadin"]), edge=int(values["edge"]), at=float(values["at"]))
            return rectangle if rectangle.gcode() == program.text else None
        except (KeyError, ValueError):
            return None
    return _first_version(program, lines[0])


def _first_version(program, header):
    """Rectangles from the first version had no settings line: no kerf, lead-in or material."""
    match = HEADER.match(header)
    cuts = [p for p in program.paths if p.kind == "cut"]
    if len(cuts) != 1:
        return None
    xs, ys = [x for x, _ in cuts[0].points], [y for _, y in cuts[0].points]
    left, bottom, width, height = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if abs(width - float(match.group(1))) > 1e-6 or abs(height - float(match.group(2))) > 1e-6:
        return None
    if cuts[0].points != [(left, bottom), (left, bottom + height), (left + width, bottom + height),
                          (left + width, bottom), (left, bottom)]:
        return None
    return Rectangle(left, bottom, width, height)


def with_changes(rectangle, **changes):
    return replace(rectangle, **changes)
