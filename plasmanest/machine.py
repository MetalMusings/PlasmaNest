"""Sheet corners measured on the machine: a points file written from QtPlasmaC, read by PlasmaNest.

Points are stored in machine coordinates, so the sheet stays right if the work origin is changed
after measuring. They are turned into work coordinates with the offsets LinuxCNC has when the
sheet is made, the same way as get_position in hal_glib.py. No Qt and no linuxcnc import here.
"""

import configparser
import math
import os
from dataclasses import dataclass

POINTS_FILE = os.path.join(os.path.expanduser("~"), ".plasmanest", "points.txt")
HEADER = ("# PlasmaNest: sheet corners saved from QtPlasmaC\n"
          "# machine_x machine_y work_x work_y source time\n")
SOURCES = ("torch", "laser")
SOURCE_NAMES = {"torch": "Brännare", "laser": "Laser"}


class PointsError(Exception):
    pass


@dataclass(frozen=True)
class Offsets:
    """The parts of linuxcnc.stat that turn machine coordinates into work coordinates."""
    g5x: tuple = (0.0, 0.0)
    tool: tuple = (0.0, 0.0)
    g92: tuple = (0.0, 0.0)
    rotation: float = 0.0  # degrees, rotation_xy
    g5x_index: int = 1     # 1 = G54

    @classmethod
    def from_stat(cls, stat):
        return cls(tuple(stat.g5x_offset[:2]), tuple(stat.tool_offset[:2]), tuple(stat.g92_offset[:2]),
                   float(stat.rotation_xy), int(stat.g5x_index))

    @property
    def name(self):
        return {1: "G54", 2: "G55", 3: "G56", 4: "G57", 5: "G58", 6: "G59",
                7: "G59.1", 8: "G59.2", 9: "G59.3"}.get(self.g5x_index, f"G5x nr {self.g5x_index}")

    def work(self, machine):
        """Work coordinates of a machine position, as hal_glib.get_position."""
        x = machine[0] - self.g5x[0] - self.tool[0]
        y = machine[1] - self.g5x[1] - self.tool[1]
        if self.rotation:
            t = math.radians(-self.rotation)
            x, y = x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)
        return x - self.g92[0], y - self.g92[1]


@dataclass(frozen=True)
class MachinePoint:
    machine: tuple  # aimed point in machine coordinates, laser offset already removed
    work: tuple     # work coordinates when it was saved
    source: str     # "torch" or "laser"
    time: str


def mm(value):
    """Two decimals, without a minus sign on zero."""
    text = f"{value:.2f}"
    return "0.00" if text == "-0.00" else text


def aimed_point(actual_position, laser_on, laser_offset):
    """Machine position of the point aimed at. The laser spot is the torch minus the offset."""
    x, y = actual_position[0], actual_position[1]
    if laser_on:
        x, y = x - laser_offset[0], y - laser_offset[1]
    return x, y


def prefs_path(ini_path, machine_name):
    """QtPlasmaC keeps its settings in <MACHINE>.prefs next to the INI file."""
    if not machine_name:
        raise PointsError(f"MACHINE saknas under [EMC] i {ini_path}.")
    return os.path.join(os.path.dirname(ini_path), f"{machine_name.strip()}.prefs")


def read_laser_offset(path):
    """[LASER_OFFSET] from QtPlasmaC's prefs file, or None if not set."""
    prefs = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        if not prefs.read(path):
            raise PointsError(f"Kan inte läsa {path}.")
        x = prefs.getfloat("LASER_OFFSET", "X axis", fallback=0.0)
        y = prefs.getfloat("LASER_OFFSET", "Y axis", fallback=0.0)
    except (configparser.Error, ValueError):
        raise PointsError(f"Ogiltig laserförskjutning i {path}.")
    return None if x == 0.0 and y == 0.0 else (x, y)


def format_point(point):
    return (f"{point.machine[0]:.4f} {point.machine[1]:.4f} {point.work[0]:.4f} {point.work[1]:.4f} "
            f"{point.source} {point.time}\n")


def parse_points(text):
    points = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        try:
            if len(words) != 6 or words[4] not in SOURCES:
                raise ValueError
            values = [float(w) for w in words[:4]]
            if not all(math.isfinite(v) for v in values):
                raise ValueError
        except ValueError:
            raise PointsError(f"Rad {number} i punktfilen går inte att läsa: {line}")
        points.append(MachinePoint((values[0], values[1]), (values[2], values[3]), words[4], words[5]))
    return points


def read_points(path=POINTS_FILE):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return parse_points(f.read())


def append_point(point, path=POINTS_FILE):
    """Add a point and return how many points the file has."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = len(read_points(path)) + 1
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(HEADER)
        f.write(format_point(point))
    return count


def write_points(points, path=POINTS_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER)
        for point in points:
            f.write(format_point(point))
    os.replace(temporary, path)


def current_offsets():
    """Offsets from a running LinuxCNC, or None if it cannot be reached."""
    try:
        import linuxcnc
        stat = linuxcnc.stat()
        stat.poll()
        return Offsets.from_stat(stat)
    except Exception:
        return None


def work_corners(points, offsets):
    """Corners in work coordinates: with the current offsets if known, else as when saved."""
    if offsets is None:
        return [p.work for p in points]
    return [offsets.work(p.machine) for p in points]
