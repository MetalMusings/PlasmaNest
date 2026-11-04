"""QtPlasmaC's material file: numbers, names and kerf widths, and the machine's work area from
its INI file. Read only, no Qt."""

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    number: int
    name: str
    kerf: float  # mm


def ini_value(ini_path, section, key):
    """The first value of a key in a section. A plain reader: LinuxCNC INI files need not suit
    configparser. QtPlasmaC is given the expanded file, with #INCLUDE already filled in."""
    current = None
    with open(ini_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                current = line.strip("[] ").upper()
            elif current == section and "=" in line and line.split("=", 1)[0].strip().upper() == key:
                return line.split("=", 1)[1].split("#")[0].strip()
    return None


def machine_name(ini_path):
    """MACHINE under [EMC]."""
    return ini_value(ini_path, "EMC", "MACHINE")


def work_area(ini_path):
    """(width, height) in mm that X and Y can travel, from [AXIS_X] and [AXIS_Y], or None."""
    try:
        size = [float(ini_value(ini_path, axis, "MAX_LIMIT")) - float(ini_value(ini_path, axis, "MIN_LIMIT"))
                for axis in ("AXIS_X", "AXIS_Y")]
        units = (ini_value(ini_path, "TRAJ", "LINEAR_UNITS") or "mm").lower()
    except (OSError, TypeError, ValueError):
        return None
    scale = 25.4 if units in ("inch", "in", "imperial") else 1.0
    width, height = (round(value * scale, 1) for value in size)
    return (width, height) if width > 0 and height > 0 else None


def machine_work_area():
    """The running LinuxCNC's work area, or None when there is none."""
    ini = find_ini()
    return work_area(ini) if ini else None


def find_ini():
    """The running LinuxCNC's INI file, or None."""
    path = os.environ.get("INI_FILE_NAME")
    if path and os.path.isfile(path):
        return path
    try:
        import linuxcnc
        stat = linuxcnc.stat()
        stat.poll()
        path = stat.ini_filename
        return path if path and os.path.isfile(path) else None
    except Exception:
        return None


def material_file(ini_path):
    name = machine_name(ini_path)
    if not name:
        return None
    path = os.path.join(os.path.dirname(ini_path), f"{name}_material.cfg")
    return path if os.path.isfile(path) else None


def read_materials(path):
    """Materials in file order, as QtPlasmaC lists them."""
    materials, number, name, kerf = [], None, "", None

    def flush():
        if number is not None and kerf is not None:
            materials.append(Material(number, name, kerf))

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            match = re.match(r"\[MATERIAL_NUMBER_(\d+)\]", line)
            if match:
                flush()
                number, name, kerf = int(match.group(1)), "", None
            elif number is not None and "=" in line:
                key, value = (part.strip() for part in line.split("=", 1))
                if key == "NAME":
                    name = value
                elif key == "KERF_WIDTH":
                    try:
                        kerf = float(value)
                    except ValueError:
                        kerf = None
    flush()
    return materials


def available_materials():
    """Materials of the running LinuxCNC, or [] when there is none or no file."""
    ini = find_ini()
    path = material_file(ini) if ini else None
    if not path:
        return []
    try:
        return read_materials(path)
    except OSError:
        return []
