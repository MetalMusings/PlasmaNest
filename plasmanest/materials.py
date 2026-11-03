"""QtPlasmaC's material file: numbers, names and kerf widths. Read only, no Qt."""

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    number: int
    name: str
    kerf: float  # mm


def machine_name(ini_path):
    """MACHINE under [EMC]. A plain reader: LinuxCNC INI files need not suit configparser."""
    section = None
    with open(ini_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                section = line.strip("[] ").upper()
            elif section == "EMC" and "=" in line and line.split("=", 1)[0].strip().upper() == "MACHINE":
                return line.split("=", 1)[1].split("#")[0].strip()
    return None


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
