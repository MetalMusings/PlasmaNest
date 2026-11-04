"""Placement of parsed programs on a sheet, export to G-code and project files."""

import json
import math
from dataclasses import dataclass

from .gcode import Program, format_number

PROJECT_VERSION = 2
GEOMETRY_TOLERANCE = 1e-6  # mm


def rotation(angle):
    """Return (cos, sin), exact for multiples of 90 degrees."""
    quarter = round(angle / 90.0)
    if abs(angle - quarter * 90.0) < 1e-9:
        return [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)][quarter % 4]
    radians = math.radians(angle)
    return math.cos(radians), math.sin(radians)


@dataclass
class Placement:
    """A figure: x, y is where the centre of the program's bounding box ends up."""
    program: Program
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0

    def map_point(self, point):
        c, s = rotation(self.angle)
        dx, dy = point[0] - self.program.center[0], point[1] - self.program.center[1]
        return (c * dx - s * dy + self.x, s * dx + c * dy + self.y)

    def unmap_point(self, point):
        """Program coordinates of a point on the sheet: the inverse of map_point."""
        c, s = rotation(self.angle)
        dx, dy = point[0] - self.x, point[1] - self.y
        return (c * dx + s * dy + self.program.center[0], -s * dx + c * dy + self.program.center[1])

    def map_vector(self, vector):
        c, s = rotation(self.angle)
        return (c * vector[0] - s * vector[1], s * vector[0] + c * vector[1])

    def bounds(self):
        """Bounding box of the cut geometry on the sheet."""
        points = [self.map_point(p) for path in self.program.paths
                  if path.kind == "cut" for p in path.points]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)


def _orientation(a, b, c):
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return 0 if abs(value) < 1e-9 else (1 if value > 0 else -1)


def _on_segment(a, b, p):
    """p is known to be on the line through a and b."""
    return (min(a[0], b[0]) - GEOMETRY_TOLERANCE <= p[0] <= max(a[0], b[0]) + GEOMETRY_TOLERANCE
            and min(a[1], b[1]) - GEOMETRY_TOLERANCE <= p[1] <= max(a[1], b[1]) + GEOMETRY_TOLERANCE)


def _segments_touch(a, b, c, d):
    o1, o2, o3, o4 = _orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and _on_segment(a, b, c)) or (o2 == 0 and _on_segment(a, b, d))
            or (o3 == 0 and _on_segment(c, d, a)) or (o4 == 0 and _on_segment(c, d, b)))


def _distance_to_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _segments_distance(a, b, c, d):
    if _segments_touch(a, b, c, d):
        return 0.0
    return min(_distance_to_segment(a, c, d), _distance_to_segment(b, c, d),
               _distance_to_segment(c, a, b), _distance_to_segment(d, a, b))


class Sheet:
    """The steel sheet: a simple polygon in G-code coordinates (mm)."""

    def __init__(self, corners):
        cleaned = []
        for x, y in corners:
            point = (float(x), float(y))
            if not cleaned or math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) > GEOMETRY_TOLERANCE:
                cleaned.append(point)
        if len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0],
                                           cleaned[0][1] - cleaned[-1][1]) <= GEOMETRY_TOLERANCE:
            cleaned.pop()
        if len(cleaned) < 3:
            raise ValueError("The sheet needs at least three different corners.")
        self.corners = tuple(cleaned)
        self.edges = tuple(zip(self.corners, self.corners[1:] + self.corners[:1]))
        if all(_orientation(cleaned[0], cleaned[1], corner) == 0 for corner in cleaned):
            raise ValueError("The sheet's corners lie on a straight line.")
        self._check_simple()
        self.area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in self.edges)) / 2
        xs, ys = [c[0] for c in self.corners], [c[1] for c in self.corners]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))
        self.width = self.bounds[2] - self.bounds[0]
        self.height = self.bounds[3] - self.bounds[1]
        self.is_rectangle = len(self.corners) == 4 and all(
            abs(a[0] - b[0]) < GEOMETRY_TOLERANCE or abs(a[1] - b[1]) < GEOMETRY_TOLERANCE for a, b in self.edges)

    @classmethod
    def rectangle(cls, width, height, x0=0.0, y0=0.0):
        return cls([(x0, y0), (x0 + width, y0), (x0 + width, y0 + height), (x0, y0 + height)])

    def _check_simple(self):
        count = len(self.edges)
        for i in range(count):
            a, b = self.edges[i]
            for j in range(i + 1, count):
                c, d = self.edges[j]
                if j == i + 1 or (i == 0 and j == count - 1):
                    # Neighbours share a corner, which is only wrong if one folds back over the other.
                    shared, p, q = (b, a, d) if j == i + 1 else (a, b, c)
                    folded = (_orientation(p, shared, q) == 0
                              and (p[0] - shared[0]) * (q[0] - shared[0]) + (p[1] - shared[1]) * (q[1] - shared[1]) > 0)
                    if folded:
                        raise ValueError("The sheet's edges fold back over each other.")
                elif _segments_touch(a, b, c, d):
                    raise ValueError("The sheet's edges cross each other.")

    def inset(self, margin):
        """The sheet shrunk by margin mm from every edge, or ValueError if that is not a clean polygon."""
        if margin <= 0:
            return self
        # Corners on a straight edge give no offset corner of their own.
        corners = list(self.corners)
        changed = True
        while changed and len(corners) > 3:
            changed = False
            for i in range(len(corners)):
                if _orientation(corners[i - 1], corners[i], corners[(i + 1) % len(corners)]) == 0:
                    del corners[i]
                    changed = True
                    break
        signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(corners, corners[1:] + corners[:1]))
        side = 1.0 if signed > 0 else -1.0  # inward is to the left of a counter-clockwise edge
        lines = []
        for a, b in zip(corners, corners[1:] + corners[:1]):
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            lines.append(((a[0] - side * dy * margin, a[1] + side * dx * margin), (dx, dy)))
        result = []
        for (p, d), (q, e) in zip(lines[-1:] + lines[:-1], lines):
            denominator = d[0] * e[1] - d[1] * e[0]
            t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / denominator
            result.append((p[0] + t * d[0], p[1] + t * d[1]))
        too_large = ValueError(f"The margin {margin:g} mm is too large for the sheet's shape.")
        # An edge that turned around has been swallowed by its neighbours.
        for (a, b), (c, d) in zip(zip(corners, corners[1:] + corners[:1]), zip(result, result[1:] + result[:1])):
            if (b[0] - a[0]) * (d[0] - c[0]) + (b[1] - a[1]) * (d[1] - c[1]) <= 0:
                raise too_large
        try:
            sheet = Sheet(result)
        except ValueError:
            raise too_large
        for c, d in sheet.edges:
            if not self.contains_segment(c, d):
                raise too_large
            for a, b in self.edges:
                if _segments_distance(a, b, c, d) < margin - 1e-6:
                    raise too_large
        return sheet

    def contains_point(self, point):
        """True if the point is on the sheet, its edge included."""
        x, y = point
        inside = False
        for a, b in self.edges:
            if _distance_to_segment(point, a, b) <= GEOMETRY_TOLERANCE:
                return True
            if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                inside = not inside
        return inside

    def contains_segment(self, a, b):
        """True if the whole straight line a-b is on the sheet, also over notches."""
        if not self.contains_point(a) or not self.contains_point(b):
            return False
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 == 0:
            return True
        low_x, high_x = min(a[0], b[0]), max(a[0], b[0])
        low_y, high_y = min(a[1], b[1]), max(a[1], b[1])
        cuts = [0.0, 1.0]
        for c, d in self.edges:
            if (max(c[0], d[0]) < low_x or min(c[0], d[0]) > high_x
                    or max(c[1], d[1]) < low_y or min(c[1], d[1]) > high_y):
                continue
            ex, ey = d[0] - c[0], d[1] - c[1]
            denominator = dx * ey - dy * ex
            if abs(denominator) > 1e-12:
                cuts.append(((c[0] - a[0]) * ey - (c[1] - a[1]) * ex) / denominator)
            else:
                cuts += [((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / length2 for q in (c, d)]
        cuts = sorted(t for t in cuts if 0.0 <= t <= 1.0)
        # Between two neighbouring crossings the line is wholly on or wholly off the sheet.
        return all(self.contains_point((a[0] + (t0 + t1) / 2 * dx, a[1] + (t0 + t1) / 2 * dy))
                   for t0, t1 in zip(cuts, cuts[1:]) if t1 - t0 > 1e-12)

    def contains_box(self, x0, y0, x1, y1):
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return all(self.contains_segment(a, b) for a, b in zip(corners, corners[1:] + corners[:1]))

    def contains_placement(self, placement):
        """True if all cutting of the figure stays on the sheet."""
        if self.is_rectangle:
            x0, y0, x1, y1 = placement.bounds()
            return (x0 >= self.bounds[0] - GEOMETRY_TOLERANCE and y0 >= self.bounds[1] - GEOMETRY_TOLERANCE
                    and x1 <= self.bounds[2] + GEOMETRY_TOLERANCE and y1 <= self.bounds[3] + GEOMETRY_TOLERANCE)
        for path in placement.program.paths:
            if path.kind != "cut":
                continue
            points = [placement.map_point(p) for p in path.points]
            if not all(self.contains_segment(a, b) for a, b in zip(points, points[1:])):
                return False
        return True


def _move_text(placement, move):
    if move.incremental:
        x, y = placement.map_vector((move.end[0] - move.start[0], move.end[1] - move.start[1]))
    else:
        x, y = placement.map_point(move.end)
    words = [f"G{move.mode}", f"X{format_number(x)}", f"Y{format_number(y)}"]
    if move.center is not None:
        i, j = placement.map_vector(move.center)
        words += [f"I{format_number(i)}", f"J{format_number(j)}"]
    words += move.extras
    if move.comment:
        words.append(move.comment)
    return " ".join(words)


def export_gcode(placements):
    """Return the QtPlasmaC G-code for the placements, cut in list order."""
    if not placements:
        raise ValueError("There are no figures to export.")
    lines = [";PlasmaNest - nested file for QtPlasmaC"]

    # QtPlasmaC requires permanent material comments before any temporary one.
    seen = set()
    for placement in placements:
        for op in placement.program.ops:
            if op.kind == "lib_material" and op.text not in seen:
                seen.add(op.text)
                lines.append(op.text)

    lines.append("G21 G40 G49 G80 G90 G91.1 G92.1 G94 G97")

    # Re-selecting the active material costs a wait or a new temporary material.
    active_material = None
    skip_wait = False
    for number, placement in enumerate(placements, 1):
        lines.append(f";part {number} x={format_number(placement.x)} "
                     f"y={format_number(placement.y)} a={format_number(placement.angle)}")
        # QtPlasmaC refuses a material change while compensation is active.
        lines.append("G40 G90 G91.1")
        first_move = True
        for op in placement.program.ops:
            if op.kind == "lib_material":
                continue
            if op.kind in ("material", "temp_material"):
                if op.text == active_material:
                    skip_wait = op.kind == "material"
                    continue
                active_material = op.text
                skip_wait = False
                lines.append(op.text)
            elif op.kind == "material_wait":
                if not skip_wait:
                    lines.append(op.text)
                skip_wait = False
            elif op.kind == "move":
                if first_move and op.move.incremental:
                    # A relative start would continue from where the previous part ended.
                    x, y = placement.map_point(op.move.start)
                    lines += ["G90", f"G0 X{format_number(x)} Y{format_number(y)}", "G91"]
                first_move = False
                lines.append(_move_text(placement, op.move))
            else:
                lines.append(op.text)
        if placement.program.ends_with_torch_on:
            lines.append("M5 $-1")

    lines += [";end", "M5 $-1", "G40 G90", "M68 E3 Q0", "M2"]
    return "\n".join(lines) + "\n"


def material_warning(placements):
    """Warn when a file without material would inherit the previous part's material."""
    with_material = [p for p in placements if p.program.has_materials]
    if with_material and len(with_material) != len(placements):
        names = sorted({p.program.name for p in placements if not p.program.has_materials})
        return ("These files set no material and are cut with the one chosen last: "
                + ", ".join(names))
    return None


def save_project(path, sheet, placements):
    """Save the sheet, the placements and the G-code itself, so the file stands alone."""
    programs = []
    for placement in placements:
        if placement.program not in programs:
            programs.append(placement.program)
    data = {
        "version": PROJECT_VERSION,
        "sheet": {"corners": [list(corner) for corner in sheet.corners]},
        "programs": [{"name": p.name, "text": p.text} for p in programs],
        "figures": [{"program": programs.index(p.program), "x": p.x, "y": p.y, "angle": p.angle}
                    for p in placements],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)


def load_project(path):
    """Return (sheet, placements). Version 1 files hold a width and height instead of corners."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    version = data.get("version")
    if version == 1:
        sheet = Sheet.rectangle(data["sheet"]["width"], data["sheet"]["height"])
    elif version == PROJECT_VERSION:
        sheet = Sheet(data["sheet"]["corners"])
    else:
        raise ValueError("Unknown version of the project file.")
    programs = [Program(p["name"], p["text"]) for p in data["programs"]]
    placements = [Placement(programs[f["program"]], f["x"], f["y"], f["angle"]) for f in data["figures"]]
    return sheet, placements
