#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run PlasmaNest's exports through QtPlasmaC's own G-code filter, outside LinuxCNC.

The filter is found for each LinuxCNC version that is installed:

- 2.9: /bin/qtplasmac_gcode (or /usr/bin), run with the system Python.
- 2.10: a run-in-place source tree, ~/src/linuxcnc-2.10 or $PLASMANEST_LINUXCNC_210.

Each filter is copied and patched so it needs no LinuxCNC: its temporary files go to a scratch
directory and its error dialog is printed to stderr. A fake `halcmd` answers its HAL queries, and a
minimal configuration (tests/data/filter_config) stands in for the machine's. A good file gives no
output on stderr; the filter prints every error and warning there.

    python3 tools/filter_check.py              # the standard set of exports, every filter found
    python3 tools/filter_check.py file.ngc...  # your own exports

Run it after updating LinuxCNC. A filtered file is not run in LinuxCNC's interpreter, so this shows
that QtPlasmaC accepts the code, not that the paths are right.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CONFIG = os.path.join(ROOT, "tests", "data", "filter_config")
SAMPLES = os.path.join(ROOT, "samples")
CONVERSATIONAL = os.path.join(ROOT, "tests", "data", "conversational_circles.ngc")
SOURCE_210 = os.environ.get("PLASMANEST_LINUXCNC_210", os.path.expanduser("~/src/linuxcnc-2.10"))
FILTER_210 = "src/emc/usr_intf/qtplasmac/qtplasmac_gcode.py"

FAKE_HALCMD = """#!/bin/sh
if [ "$1" = getp ]; then
  case "$2" in plasmac.max-offset) echo 5;; *) echo 0;; esac
fi
exit 0
"""
TMP_LINE = "self.tmpPath = '/tmp/qtplasmac'"
DIALOG_LINE = "self.dialog.dialog_box(self, 'G-Code Errors & Warnings', errorText, warnText)"
REPORT = ("sys.stderr.write('ERRORS:\\n' + errorText + 'WARNINGS:\\n' + warnText + '\\n') "
          "if (errorText or warnText) else None")


class Filter:
    """One installed QtPlasmaC filter, patched to run on its own."""

    def __init__(self, label, source, prefix=""):
        self.label = label
        self.source = source
        self.prefix = prefix  # shell commands that set up the environment first

    def prepare(self, directory):
        """Copy and patch the filter into directory; return the path of the copy."""
        with open(self.source, encoding="utf-8") as f:
            text = f.read()
        tmp = os.path.join(directory, "tmp")
        os.makedirs(tmp, exist_ok=True)
        for old, new in ((TMP_LINE, f"self.tmpPath = {tmp!r}"), (DIALOG_LINE, REPORT)):
            if old not in text:
                raise RuntimeError(f"{self.label}: the filter has changed, cannot patch {old!r}")
            text = text.replace(old, new)
        path = os.path.join(directory, "filter.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def run(self, gcode_path):
        """Filter the file. Returns (return code, filtered text, errors and warnings)."""
        with tempfile.TemporaryDirectory(prefix="plasmanest-filter-") as directory:
            filter_path = self.prepare(directory)
            fake = os.path.join(directory, "fake")
            os.makedirs(fake)
            with open(os.path.join(fake, "halcmd"), "w") as f:
                f.write(FAKE_HALCMD)
            os.chmod(os.path.join(fake, "halcmd"), 0o755)
            config = os.path.join(directory, "config")
            shutil.copytree(CONFIG, config)  # the filter rewrites the material file
            env = dict(os.environ, LC_ALL="C", QT_QPA_PLATFORM="offscreen",
                       INI_FILE_NAME=os.path.join(config, "QtPlasma.ini"))
            env["PATH"] = fake + os.pathsep + env.get("PATH", "")
            command = f'{self.prefix}cd "$CONFIG" && exec /usr/bin/python3 "$FILTER" "$GCODE"'
            env.update(CONFIG=config, FILTER=filter_path, GCODE=os.path.abspath(gcode_path))
            done = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, timeout=120)
            return done.returncode, done.stdout, done.stderr


def find_filters():
    found = []
    for path in ("/bin/qtplasmac_gcode", "/usr/bin/qtplasmac_gcode"):
        if os.path.isfile(path):
            found.append(Filter("LinuxCNC 2.9", path))
            break
    rip = os.path.join(SOURCE_210, "scripts", "rip-environment")
    if os.path.isfile(os.path.join(SOURCE_210, FILTER_210)) and os.path.isfile(rip):
        found.append(Filter("LinuxCNC 2.10", os.path.join(SOURCE_210, FILTER_210),
                            prefix=f'. "{rip}" >/dev/null 2>&1; '))
    return found


def standard_exports():
    """{name: G-code}: every kind of figure and the sample files, turned and mirrored."""
    from plasmanest import shapes as sh
    from plasmanest.gcode import load_program
    from plasmanest.nest import Placement, export_gcode

    flange = load_program(os.path.join(SAMPLES, "flange.ngc"))
    washer = load_program(os.path.join(SAMPLES, "washer.ngc"))
    conversational = load_program(CONVERSATIONAL)

    def drawn(shape, x, y, angle=0.0):
        return Placement(shape.program(), x, y, angle)

    cases = {
        "flange_rotated": [Placement(flange, 100, 100, a) for a in (0, 90, 30)],
        "washer_rotated": [Placement(washer, 300, 100, 45), Placement(washer, 400, 100)],
        "conversational": [Placement(conversational, 200, 200, 0), Placement(conversational, 400, 200, 45)],
        "mirrored": [Placement(flange, 100, 100, 0, mirror=True), Placement(flange, 300, 100, 30, mirror=True),
                     Placement(washer, 500, 100, 0, mirror=True),
                     Placement(conversational, 700, 200, 0, mirror=True)],
        "rectangles": [drawn(sh.Rectangle(100, 100, 200, 80, kerf=1.2, material=1, leadin=4), 100, 100),
                       drawn(sh.Rectangle(0, 0, 50, 30, inside=True, kerf=1.2, material=1, leadin=5,
                                          edge=3, at=0.5), 120, 120, 30)],
        "circles": [drawn(sh.Circle(300, 300, 80, kerf=1.2, material=1, leadin=4), 300, 300),
                    drawn(sh.Circle(300, 300, 40, inside=True, kerf=1.2, material=1, leadin=4), 300, 300),
                    drawn(sh.Circle(400, 300, 12, inside=True, kerf=1.2, material=1), 400, 300, 45),
                    drawn(sh.Circle(450, 300, 29.9, inside=True, kerf=1.2, material=1), 450, 300)],
        "hole_circles": [drawn(sh.HoleCircle(300, 300, 100, holes=6, hole_diameter=8, kerf=1.2, material=1), 300, 300),
                         drawn(sh.HoleCircle(500, 300, 100, holes=4, hole_diameter=20, kerf=1.2, material=1,
                                             leadin=3), 500, 300, 20)],
        "slots": [drawn(sh.Slot(300, 300, 60, 12, kerf=1.2, material=1, leadin=3), 300, 300),
                  drawn(sh.Slot(400, 300, 60, 12, inside=True, kerf=1.2, material=1, leadin=3), 400, 300, 90)],
        "polygons": [drawn(sh.Polygon(((0, 0), (80, 0), (60, 50), (10, 60)), kerf=1.2, material=1, leadin=4), 100, 100),
                     drawn(sh.Polygon(((0, 0), (80, 0), (60, 50), (10, 60)), inside=True, kerf=1.2, material=1,
                                      leadin=4), 300, 100, 25),
                     drawn(sh.Polygon(((0, 0), (100, 30), (150, 10)), closed=False, material=1, leadin=4), 500, 100)],
    }
    everything = []
    for placements in list(cases.values()):
        for p in placements:
            everything.append(Placement(p.program, p.x + 20 * len(everything), p.y, p.angle, mirror=p.mirror))
    cases["everything"] = everything
    return {name: export_gcode(placements) for name, placements in cases.items()}


def run_standard(filters=None, out=sys.stdout):
    """Filter the standard exports; returns the number of files with errors or warnings."""
    filters = find_filters() if filters is None else filters
    if not filters:
        print("No QtPlasmaC filter found.", file=out)
        return 0
    problems = 0
    exports = standard_exports()
    with tempfile.TemporaryDirectory(prefix="plasmanest-exports-") as directory:
        for name, gcode in exports.items():
            path = os.path.join(directory, name + ".ngc")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(gcode)
            for flt in filters:
                code, _, errors = flt.run(path)
                ok = code == 0 and not errors.strip()
                problems += not ok
                print(f"{'ok  ' if ok else 'FAIL'} {flt.label}: {name}", file=out)
                if not ok:
                    print(errors.strip() or f"exit code {code}", file=out)
    return problems


def main(argv):
    filters = find_filters()
    if not argv:
        problems = run_standard(filters)
        print("All exports accepted." if not problems else f"{problems} with errors or warnings.")
        return 1 if problems else 0
    problems = 0
    for path in argv:
        for flt in filters:
            code, _, errors = flt.run(path)
            ok = code == 0 and not errors.strip()
            problems += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {flt.label}: {path}")
            if not ok:
                print(errors.strip() or f"exit code {code}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
