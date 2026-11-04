# SPDX-License-Identifier: GPL-2.0-or-later
# QtPlasmaC user command file for PlasmaNest. In the INI file, under [DISPLAY]:
#
#     USER_COMMAND_FILE = /path/to/plasmanest/qtplasmac/user_command.py
#
# QtVCP runs this with exec() after QtPlasmaC is built, with "self" as QtPlasmaC's handler and
# "rcfile" as this file's path. Errors are logged by QtVCP and do not stop QtPlasmaC.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(rcfile)))  # noqa: F821 - given by QtVCP
import plasmanest_tab  # noqa: E402

plasmanest_tab.install(self)  # noqa: F821 - QtPlasmaC's handler, given by QtVCP
