"""Save the machine position as a sheet corner for PlasmaNest.

Run from a QtPlasmaC user button:  %/path/to/plasmanest/save_point.py
If the laser is on, the point under the laser is saved instead of the point under the torch.
Only errors are shown in QtPlasmaC; saved points show up in PlasmaNest.
"""

import datetime
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plasmanest import machine  # noqa: E402


def laser_is_on():
    result = subprocess.run(["halcmd", "getp", "qtplasmac.laser_on"], capture_output=True, text=True, timeout=5)
    value = result.stdout.strip().upper()
    if result.returncode != 0 or value not in ("TRUE", "FALSE"):
        raise machine.PointsError("Cannot read qtplasmac.laser_on.")
    return value == "TRUE"


def machine_name(ini_path):
    import linuxcnc
    return linuxcnc.ini(ini_path).find("EMC", "MACHINE")


def save(stat, path=machine.POINTS_FILE):
    if not all(stat.homed[:stat.joints]):
        raise machine.PointsError("The machine is not homed, no point saved.")
    laser = laser_is_on()
    offset = None
    if laser:
        offset = machine.read_laser_offset(machine.prefs_path(stat.ini_filename, machine_name(stat.ini_filename)))
    if laser and offset is None:
        raise machine.PointsError("The laser is on but the prefs file has no laser offset.")
    position = machine.aimed_point(stat.actual_position, laser, offset)
    point = machine.MachinePoint(position, machine.Offsets.from_stat(stat).work(position),
                                 "laser" if laser else "torch",
                                 datetime.datetime.now().isoformat(timespec="seconds"))
    count = machine.append_point(point, path)
    return (f"PlasmaNest: point {count} saved ({machine.SOURCE_NAMES[point.source].lower()}), "
            f"X {machine.mm(point.work[0])} Y {machine.mm(point.work[1])}")


def main():
    import linuxcnc
    try:
        stat = linuxcnc.stat()
        stat.poll()
        save(stat)
    except Exception as error:
        linuxcnc.command().error_msg(f"PlasmaNest: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
