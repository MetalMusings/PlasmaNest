"""Figures made in PlasmaNest itself, written as G-code so they work like any loaded file. No Qt.

A rectangle keeps its settings in its own G-code, on a comment line, so they survive project
files. Reading them back regenerates the G-code and compares it with the program's text, so a
hand-edited file is never taken for one.
"""

import math
import re
from dataclasses import dataclass, replace

from .gcode import Program, format_number

MIN_SIZE = 1.0  # mm
HEADER = re.compile(r";PlasmaNest rectangle ([0-9.]+) x ([0-9.]+) mm")
PARAMETERS = ";PlasmaNest parameters:"

# Small holes, the user's choice 2026-11-04: holes under this drawn diameter are cut at reduced
# speed, start with a half circle from the centre and end with a half circle into the scrap,
# at most OVERCUT mm along it.
SMALL_HOLE = 30.0  # mm, drawn diameter
SMALL_HOLE_SPEED = 60  # percent of the material's cut feed rate
OVERCUT = 2.0  # mm along the lead-out arc

# Edges of the drawn rectangle, each with its start, end and outward normal, in clockwise order.
# A lead-in position is (edge, fraction along it from its start).
EDGE_NAMES = ("left", "top", "right", "bottom")


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
            raise ValueError(f"The rectangle must be at least {n(MIN_SIZE)} mm both ways.")
        if self.kerf < 0 or self.leadin < 0:
            raise ValueError("Kerf and lead-in cannot be negative.")
        if self.inside:
            if min(self.width, self.height) <= self.kerf:
                raise ValueError(f"The hole is too small for the kerf {n(self.kerf)} mm.")
            across = (self.width if self.edge in (0, 2) else self.height) - self.kerf
            if self.leadin >= across:
                raise ValueError(f"The lead-in {n(self.leadin)} mm does not fit in the hole.")
        if self.edge not in range(4) or not 0.0 <= self.at <= 1.0:
            raise ValueError("Invalid place for the lead-in.")

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
        lines = [f";PlasmaNest rectangle {n(self.width)} x {n(self.height)} mm", settings, "G21 G40 G90"]
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
        kind = "Hole" if self.inside else "Rectangle"
        return f"{kind} {format_number(self.width)} × {format_number(self.height)}"

    def program(self):
        return Program(self.name, self.gcode())

    # ------------------------------------------------- shared with the other shapes

    @property
    def anchor(self):
        """The point that stays put when the shape changes, and that the X and Y fields give."""
        return self.left, self.bottom

    def nearest(self, point):
        """(lead-in position, distance in mm) on the drawn outline nearest a point."""
        edge, at, distance = self.nearest_edge(point)
        return (edge, round(at, 4)), distance

    def with_leadin_at(self, position):
        return replace(self, edge=position[0], at=position[1])

    def leadin_segment(self):
        start, point, _ = self.path()
        return start, point

    @staticmethod
    def describe_leadin(position):
        return f"{EDGE_NAMES[position[0]]} edge"

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


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    diameter: float
    inside: bool = False      # cut as a hole: kerf inwards, counter-clockwise (G3)
    kerf: float = 0.0
    material: int = None
    leadin: float = 0.0
    angle: float = 180.0      # where the lead-in meets the circle, degrees from +X

    @property
    def offset(self):
        return (-1 if self.inside else 1) * self.kerf / 2

    @property
    def path_radius(self):
        return self.diameter / 2 + self.offset

    @property
    def small_hole(self):
        """A hole cut slowly with half-circle lead-in and lead-out. The lead-in field is not used."""
        return self.inside and self.diameter < SMALL_HOLE

    def check(self):
        n = format_number
        if self.diameter < MIN_SIZE:
            raise ValueError(f"The circle must be at least {n(MIN_SIZE)} mm in diameter.")
        if self.kerf < 0 or self.leadin < 0:
            raise ValueError("Kerf and lead-in cannot be negative.")
        if self.inside:
            if self.diameter <= self.kerf:
                raise ValueError(f"The hole is too small for the kerf {n(self.kerf)} mm.")
            if self.leadin > self.path_radius and not self.small_hole:
                raise ValueError(f"The lead-in {n(self.leadin)} mm does not fit in the hole.")

    def path(self):
        """(lead-in start, cut start). The cut is one full circle from the cut start.

        A small hole's lead-in starts in the centre."""
        a = math.radians(self.angle)
        ux, uy = math.cos(a), math.sin(a)
        r = self.path_radius
        point = (self.cx + r * ux, self.cy + r * uy)
        if self.small_hole:
            return (self.cx, self.cy), point
        side = -1.0 if self.inside else 1.0  # the lead-in starts in the scrap
        return (point[0] + side * ux * self.leadin, point[1] + side * uy * self.leadin), point

    def gcode(self):
        self.check()
        n = format_number
        start, point = self.path()
        settings = (f"{PARAMETERS} cx={n(self.cx)} cy={n(self.cy)} diameter={n(self.diameter)} "
                    f"side={'inside' if self.inside else 'outside'} kerf={n(self.kerf)} "
                    f"material={'-' if self.material is None else self.material} leadin={n(self.leadin)} "
                    f"angle={n(self.angle)}")
        lines = [f";PlasmaNest circle {n(self.diameter)} mm", settings, "G21 G40 G90"]
        if self.material is not None:
            lines += [f"M190 P{self.material}", "M66 P3 L3 Q1"]
        lines += ["F#<_hal[plasmac.cut-feed-rate]>", f"G0 X{n(start[0])} Y{n(start[1])}", "M3 $0 S1"]
        if self.small_hole:
            lines += self.small_hole_cut(point)
        else:
            if self.leadin > 0:
                lines.append(f"G1 X{n(point[0])} Y{n(point[1])}")
            # Parts clockwise, holes counter-clockwise, as plasma contours are cut.
            lines.append(f"{'G3' if self.inside else 'G2'} X{n(point[0])} Y{n(point[1])} "
                         f"I{n(self.cx - point[0])} J{n(self.cy - point[1])}")
            lines.append("M5 $0")
        lines += ["M2", ""]
        return "\n".join(lines)

    def small_hole_cut(self, point):
        """After M3, from the centre: half a circle out to the edge, the hole, and half a circle back in,
        stopped after OVERCUT mm, all counter-clockwise and at reduced speed. The lead-in and
        lead-out lie on one circle, of half the hole's radius, that touches the hole at point."""
        n = format_number
        a = math.radians(self.angle)
        small = self.path_radius / 2
        centre = (self.cx + small * math.cos(a), self.cy + small * math.sin(a))
        turn = min(math.pi, OVERCUT / small)
        end = (centre[0] + small * math.cos(a + turn), centre[1] + small * math.sin(a + turn))
        # The filter's own hole sensing, switched on by an earlier part, would add a torch-off
        # overburn after the hole and move the start of the lead-out.
        return ["#<holes>=0 (PlasmaNest cuts this hole itself)",
                f"M67 E3 Q{SMALL_HOLE_SPEED} (small hole, {SMALL_HOLE_SPEED} percent speed)",
                f"G3 X{n(point[0])} Y{n(point[1])} I{n(centre[0] - self.cx)} J{n(centre[1] - self.cy)}",
                f"G3 X{n(point[0])} Y{n(point[1])} I{n(self.cx - point[0])} J{n(self.cy - point[1])}",
                f"G3 X{n(end[0])} Y{n(end[1])} I{n(centre[0] - point[0])} J{n(centre[1] - point[1])}",
                "M5 $0", "M68 E3 Q0 (full speed)"]

    @property
    def name(self):
        return f"{'Hole' if self.inside else 'Circle'} Ø{format_number(self.diameter)}"

    def program(self):
        return Program(self.name, self.gcode())

    @property
    def anchor(self):
        return self.cx, self.cy

    def nearest(self, point):
        dx, dy = point[0] - self.cx, point[1] - self.cy
        angle = math.degrees(math.atan2(dy, dx)) % 360 if (dx or dy) else self.angle
        distance = abs(math.hypot(dx, dy) - self.diameter / 2)
        return round(angle, 2), distance

    def with_leadin_at(self, position):
        return replace(self, angle=position)

    def leadin_segment(self):
        return self.path()

    @staticmethod
    def describe_leadin(position):
        return f"at {format_number(round(position))}°"


CIRCLE_HEADER = re.compile(r";PlasmaNest circle ([0-9.]+) mm")


def circle_of(program):
    """The Circle a program was made from, or None for any other figure."""
    lines = program.text.lstrip().split("\n")
    if len(lines) < 2 or not CIRCLE_HEADER.match(lines[0]) or not lines[1].startswith(PARAMETERS):
        return None
    try:
        values = dict(word.split("=", 1) for word in lines[1][len(PARAMETERS):].split())
        circle = Circle(float(values["cx"]), float(values["cy"]), float(values["diameter"]),
                        inside=values["side"] == "inside", kerf=float(values["kerf"]),
                        material=None if values["material"] == "-" else int(values["material"]),
                        leadin=float(values["leadin"]), angle=float(values["angle"]))
        return circle if circle.gcode() == program.text else None
    except (KeyError, ValueError):
        return None


def shape_of(program):
    """The Rectangle or Circle a program was made from, or None for loaded G-code."""
    return rectangle_of(program) or circle_of(program)


def rectangle_program(x0, y0, x1, y1, **settings):
    left, right = sorted((x0, x1))
    bottom, top = sorted((y0, y1))
    return Rectangle(left, bottom, right - left, top - bottom, **settings).program()


def rectangle_of(program):
    """The Rectangle a program was made from, or None for any other figure."""
    lines = program.text.lstrip().split("\n")
    if len(lines) < 2 or not HEADER.match(lines[0]) or not lines[1].startswith(PARAMETERS):
        return None
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


def with_changes(rectangle, **changes):
    return replace(rectangle, **changes)
