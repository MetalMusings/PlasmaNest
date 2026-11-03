"""QtPlasmaC's look: colours from its preferences file and a style sheet in the same spirit. No Qt.

QtPlasmaC builds its style sheet from [COLOR_OPTIONS] in <MACHINE>.prefs. PlasmaNest reads the
same colours, so that it looks like a part of QtPlasmaC and follows when the colours are changed
there. Without LinuxCNC it uses QtPlasmaC's defaults.
"""

import configparser
import os
import re

from .materials import find_ini, machine_name

DEFAULTS = {
    "Foreground": "#ffee06", "Highlight": "#ffee06", "LED": "#ffee06", "Background": "#16160e",
    "Background Alt": "#36362e", "Frames": "#ffee06", "Estop": "#ff0000", "Disabled": "#b0b0b0",
    "Preview": "#000000",
}
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def prefs_path(ini_path):
    try:
        name = machine_name(ini_path) if ini_path else None
    except OSError:
        return None
    return os.path.join(os.path.dirname(ini_path), f"{name}.prefs") if name else None


def qtplasmac_colors(ini_path=None):
    """QtPlasmaC's colours, with its defaults for anything missing or not a #rrggbb colour."""
    colors = dict(DEFAULTS)
    path = prefs_path(ini_path or find_ini())
    if not path or not os.path.isfile(path):
        return colors
    prefs = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        prefs.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return colors
    for key in DEFAULTS:
        value = prefs.get("COLOR_OPTIONS", key, fallback="").strip()
        if COLOR.match(value):
            colors[key] = value
    return colors


def stylesheet(c):
    """Widgets drawn as QtPlasmaC draws them: dark ground, foreground text and frames, and buttons
    that turn into the foreground colour when pressed or checked."""
    fg, hi, bg, alt = c["Foreground"], c["Highlight"], c["Background"], c["Background Alt"]
    frames, disabled = c["Frames"], c["Disabled"]
    return f"""
* {{ color: {fg}; background: {bg}; outline: none; }}
*:disabled {{ color: {disabled}; border-color: {disabled}; }}
QToolTip {{ color: {fg}; background: {bg}; border: 1px solid {fg}; padding: 3px; }}
QGroupBox {{ border: 1px solid {frames}; border-radius: 4px; margin-top: 1.1em; padding-top: 0.3em; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QGroupBox:disabled {{ border-color: {disabled}; }}
QPushButton, QToolButton {{ border: 1px solid {fg}; border-radius: 4px; padding: 4px 8px; }}
QPushButton:pressed, QToolButton:pressed, QPushButton:checked, QToolButton:checked {{
    color: {bg}; background: {hi}; }}
QPushButton:disabled, QToolButton:disabled {{ border-color: {disabled}; }}
QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {alt}; border: 1px solid {fg}; border-radius: 2px;
    padding: 2px; selection-color: {bg}; selection-background-color: {hi}; }}
QComboBox {{ border: 1px solid {fg}; border-radius: 4px; padding: 2px 6px; }}
QComboBox QAbstractItemView {{ border: 1px solid {fg}; selection-color: {bg}; selection-background-color: {hi}; }}
QListView, QListWidget, QTableView, QTableWidget {{ border: 1px solid {fg}; border-radius: 4px;
    alternate-background-color: {alt}; selection-color: {bg}; selection-background-color: {hi};
    gridline-color: {alt}; }}
QHeaderView::section {{ background: {bg}; border: 1px solid {fg}; border-radius: 4px; padding: 3px; }}
QTableCornerButton::section {{ background: {bg}; border: none; }}
QCheckBox::indicator {{ border: 1px solid {fg}; border-radius: 4px; width: 16px; height: 16px; }}
QCheckBox::indicator:checked {{ background: {hi}; }}
QCheckBox::indicator:disabled {{ border-color: {disabled}; }}
QMenuBar::item:selected, QMenu::item:selected {{ color: {bg}; background: {hi}; }}
QMenu {{ border: 1px solid {fg}; }}
QMenu::separator {{ height: 1px; background: {alt}; margin: 3px 6px; }}
QDockWidget::title {{ background: {alt}; padding: 4px; }}
QStatusBar {{ border-top: 1px solid {alt}; }}
QScrollBar {{ background: {bg}; border: 1px solid {alt}; }}
QScrollBar::handle {{ background: {alt}; border: 1px solid {fg}; border-radius: 3px; min-height: 20px;
    min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""
