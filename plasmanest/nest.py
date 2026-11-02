"""Placement of parsed programs on a sheet, export to G-code and project files."""

import json
import math
from dataclasses import dataclass

from .gcode import Program, format_number

PROJECT_VERSION = 1


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

    def map_vector(self, vector):
        c, s = rotation(self.angle)
        return (c * vector[0] - s * vector[1], s * vector[0] + c * vector[1])

    def bounds(self):
        """Bounding box of the cut geometry on the sheet."""
        points = [self.map_point(p) for path in self.program.paths
                  if path.kind == "cut" for p in path.points]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)

    def inside(self, width, height, tolerance=1e-6):
        x0, y0, x1, y1 = self.bounds()
        return x0 >= -tolerance and y0 >= -tolerance and x1 <= width + tolerance and y1 <= height + tolerance


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
        raise ValueError("Det finns inga figurer att exportera.")
    lines = [";PlasmaNest - nestad fil for QtPlasmaC"]

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
        return ("Dessa filer anger inget material och skärs med det som senast valdes: "
                + ", ".join(names))
    return None


def save_project(path, width, height, placements):
    """Save the sheet, the placements and the G-code itself, so the file stands alone."""
    programs = []
    for placement in placements:
        if placement.program not in programs:
            programs.append(placement.program)
    data = {
        "version": PROJECT_VERSION,
        "sheet": {"width": width, "height": height},
        "programs": [{"name": p.name, "text": p.text} for p in programs],
        "figures": [{"program": programs.index(p.program), "x": p.x, "y": p.y, "angle": p.angle}
                    for p in placements],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)


def load_project(path):
    """Return (width, height, placements)."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("version") != PROJECT_VERSION:
        raise ValueError("Okänd version av projektfilen.")
    programs = [Program(p["name"], p["text"]) for p in data["programs"]]
    placements = [Placement(programs[f["program"]], f["x"], f["y"], f["angle"]) for f in data["figures"]]
    return data["sheet"]["width"], data["sheet"]["height"], placements
