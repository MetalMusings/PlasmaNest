# SPDX-License-Identifier: GPL-2.0-or-later
"""PlasmaNest's exports through QtPlasmaC's own G-code filter, for each LinuxCNC version installed.

Skipped where no filter is found (for example on GitHub). See tools/filter_check.py.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import filter_check  # noqa: E402

FILTERS = filter_check.find_filters()
pytestmark = pytest.mark.skipif(not FILTERS, reason="no QtPlasmaC G-code filter installed")


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    directory = tmp_path_factory.mktemp("exports")
    paths = {}
    for name, gcode in filter_check.standard_exports().items():
        paths[name] = str(directory / f"{name}.ngc")
        with open(paths[name], "w", encoding="utf-8", newline="\n") as f:
            f.write(gcode)
    return paths


@pytest.mark.parametrize("flt", FILTERS, ids=lambda f: f.label)
def test_the_filter_reports_errors(flt, tmp_path):
    # So that the tests below cannot pass because the filter was never really run.
    bad = tmp_path / "bad.ngc"
    bad.write_text("G21 G90 G91.1\nM3 $0 S1\nG1 X10 Y10\nM5\n")  # 2.9.4 does not check G92, so not that
    code, _, errors = flt.run(str(bad))
    assert "M03 command detected before movement" in errors


@pytest.mark.parametrize("flt", FILTERS, ids=lambda f: f.label)
@pytest.mark.parametrize("name", ["flange_rotated", "washer_rotated", "conversational", "mirrored", "rectangles",
                                  "circles", "hole_circles", "slots", "polygons", "everything"])
def test_exports_pass_the_filter(flt, name, exports):
    code, filtered, errors = flt.run(exports[name])
    assert errors.strip() == ""
    assert code == 0
    assert filtered.strip()
