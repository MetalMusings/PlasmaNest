"""The live export that QtPlasmaC's PLASMANEST tab loads when going to MAIN. No Qt here.

PlasmaNest writes the G-code first and then a small status file, each under a temporary name that
is then swapped in. The status holds a checksum of the G-code, so a reader can tell that the two
belong together and whether the nest has changed since it was last loaded.
"""

import hashlib
import json
import os

NC_FILES = os.path.join(os.path.expanduser("~"), "linuxcnc", "nc_files")
FALLBACK_DIR = os.path.join(os.path.expanduser("~"), ".plasmanest")
GCODE_NAME = "aktuell.ngc"
STATUS_NAME = "aktuell.json"


def live_dir(nc_files=NC_FILES):
    """Next to the exports, so QtPlasmaC's file dialog finds it too."""
    return os.path.join(nc_files, "plasmanest") if os.path.isdir(nc_files) else FALLBACK_DIR


def checksum(data):
    return hashlib.sha1(data).hexdigest()


def _replace(path, data):
    temporary = path + ".tmp"
    with open(temporary, "wb") as f:
        f.write(data)
    os.replace(temporary, path)


def write(directory, gcode, parts, problems, autoload):
    """Write the G-code (None when there is nothing to cut) and then the status. Return the status."""
    os.makedirs(directory, exist_ok=True)
    gcode_path = os.path.join(directory, GCODE_NAME)
    data = None
    if gcode is not None:
        data = gcode.encode("utf-8")
        _replace(gcode_path, data)
    status = {
        "gcode": gcode_path,
        "checksum": checksum(data) if data is not None else None,
        "parts": parts,
        "problems": list(problems),
        "autoload": bool(autoload),
    }
    _replace(os.path.join(directory, STATUS_NAME), json.dumps(status, ensure_ascii=False, indent=1).encode("utf-8"))
    return status


def read(directory):
    """The status, or None if there is none or it cannot be read."""
    try:
        with open(os.path.join(directory, STATUS_NAME), encoding="utf-8") as f:
            status = json.load(f)
        if not isinstance(status, dict):
            return None
        return status
    except (OSError, ValueError):
        return None


def decide(status, last_loaded):
    """What to do when going to MAIN: ("load", checksum), ("error", text) or ("nothing", reason).

    Machine state is checked by the caller; this only looks at the files.
    """
    if status is None:
        return "nothing", "ingen nest"
    if not status.get("autoload", True):
        return "nothing", "automatisk inläsning av"
    if not status.get("parts") or not status.get("checksum"):
        return "nothing", "inga figurer"
    if status["checksum"] == last_loaded:
        return "nothing", "redan inläst"
    if status.get("problems"):
        return "error", ("PlasmaNest-nesten lästes inte in:\n" + "\n".join(status["problems"])
                         + "\nRätta till det i PLASMANEST, eller exportera och öppna filen själv.")
    try:
        with open(status["gcode"], "rb") as f:
            data = f.read()
    except OSError as error:
        return "error", f"PlasmaNest-nesten kunde inte läsas: {error}"
    if checksum(data) != status["checksum"]:
        return "nothing", "filen skrivs just nu"
    return "load", status["checksum"]
