# SPDX-License-Identifier: GPL-2.0-or-later
"""The tests must not depend on whether LinuxCNC, or QtPlasmaC's simulator, happens to be running
on this computer: by default they see none. A test that wants one patches find_ini itself."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def no_running_linuxcnc(monkeypatch):
    from plasmanest import materials
    monkeypatch.setattr(materials, "find_ini", lambda: None)
