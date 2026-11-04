# PlasmaNest

Manual nesting of G-code for LinuxCNC/QtPlasmaC. Load one or more G-code files, place the figures
on a steel sheet and export a single `.ngc` file that QtPlasmaC can run.

## Installation

Needs Python 3.7 or later and PyQt5.

**Windows 11**

```bash
python -m pip install -r requirements.txt
```

Start with `start.bat`, or with `python plasmanest.py`.

**Debian** (the same package QtPlasmaC itself uses)

```bash
sudo apt install python3-pyqt5
```

```bash
sh start.sh
```

The files in `samples/` are good for trying it out.

## Use

| What | How |
|---|---|
| Load G-code | **Load…** or Ctrl+L. The figure is put in a free place on the sheet. |
| Rectangular sheet | Width and height in the panel. The sheet's lower left corner is G-code X0 Y0. |
| Draw the sheet | **Draw**, and click the corners with the mouse. Finish with a double click, Enter or a click on the first corner. Backspace removes the latest corner, Esc cancels and Shift gives straight edges. The corners land on whole millimetres. |
| Type the sheet's corners | **Corners…** shows the corners in a table, where you can change, add and remove them. Use it for exact sizes. |
| Sheet from the machine | Jog to the corners of the sheet and press the **save point** user button in QtPlasmaC at each one. Open **From the machine…** in PlasmaNest, where the points appear one by one in a table and a drawing. The drawing shows the edges, the latest point and the sheet after the margin. Choose a safety margin and press **Use as sheet**. See below for how to add the button. |
| Back to a rectangle | **Rectangle** gives a rectangle with the polygon's outer size, with its corner at X0 Y0. |
| Move | Drag with the left mouse button, type X/Y in the panel, or use the arrow keys (1 mm, 10 mm with Shift). |
| Rotate | Hold the right mouse button on the figure and drag around it, and the figure follows the pointer (Ctrl gives 15° steps). The green ring, Shift+mouse wheel, the R key, the buttons in the panel and typing the angle also work. |
| Select several | Drag a box on empty space, or Ctrl-click. Several figures rotate as a group. |
| Draw a rectangle | **Draw rectangle**: a label at the pointer shows X and Y. Press at a corner and drag to the opposite corner, or click both. The label shows width × height. The corners land on whole millimetres, and Esc cancels. Then the exact **Width** and **Height** can be typed in the panel: type the width, press Tab, type the height and press Enter. The first corner stays put. |
| Draw a circle | **Draw circle**: press at the centre and drag out to the edge, or click the centre and then the edge. The label shows X and Y and then the diameter in whole millimetres. The exact **Diameter** is typed in the panel. |
| Draw a hole circle | **Draw hole circle**: press at the centre and drag out to the hole centres, or click the centre and then a hole centre. The new hole circle gets the number and size of holes used last (at first 4 × Ø10). |
| Draw a slot | **Draw slot**: press at a corner and drag to the opposite corner, or click both, as for a rectangle. The slot fills the box along its longer side: two half circles joined by straight sides. Then the exact **Length** (end to end) and **Width** can be typed in the panel. Turn it like any figure. |
| Copy | **Copy…** or Ctrl+D, and give the number. The copies are put in free places. |
| Delete | **Delete** (selected figures) or **Delete the whole G-code** (all copies of the file). |
| Cutting order | The list shows the order. Change it with **Cut earlier** and **Cut later**. |
| Undo | Ctrl+Z. Redo with Ctrl+Y (Linux: Ctrl+Shift+Z). |
| View | The mouse wheel zooms, the middle button or a right drag on empty space pans, F fits to the window. |

Figures outside the sheet turn **red** and overlapping figures turn **orange**. A figure inside a
hole in another does not count as overlapping, nor do holes drawn in PlasmaNest (holes and hole
circles and slots) that lie wholly in another figure's material. The check uses the programmed path, so the kerf is
not included: leave enough distance yourself.

The sheet can have any shape as long as its edges do not cross, for example an offcut with a notch.
It need not lie at G-code X0 Y0, which is shown with a purple cross, but the corners' coordinates
must match how the sheet lies in the machine. The sheet's shape is only a check: it does not change
the G-code, and QtPlasmaC does not know about it.

## Saving the sheet's corners from the machine

Add the button once in QtPlasmaC, under **SETTINGS** → **USER BUTTONS** on a free row:

| Name | Code |
|---|---|
| `Save\point` | `%/home/debian/src/plasmanest/save_point.py` |

Press **Save**, and the button appears on the MAIN tab. Change the path if PlasmaNest is somewhere
else.

- The machine must be homed. Each press saves a point, which shows up in PlasmaNest. QtPlasmaC only
  shows a message if the point could not be saved.
- With the laser on (the **Laser** button) the point under the laser cross is saved, otherwise the
  point under the torch. The laser's offset is read from `[LASER_OFFSET]` in QtPlasmaC's prefs file.
- The points are saved in `~/.plasmanest/points.txt` in **machine coordinates**. PlasmaNest converts
  them with the origin LinuxCNC has when the sheet is made, so the origin can be zeroed after
  measuring. If you zero the origin after the sheet has been made, make the sheet again.
- Take the corners in order around the sheet. **Clear all** before measuring a new sheet.
- The safety margin moves each edge inwards. If the margin makes the shape invalid, the sheet is
  refused.

### Saving a point from a pendant

A button on a pendant or gamepad can save the point the same way. Add a line at the end of the
postgui HAL file (here `custom_postgui.hal`) and restart LinuxCNC:

```
loadusr python3 /home/debian/src/plasmanest/point_button.py input.0.btn-start
```

The last word is the button's HAL pin. `input.0.btn-start` is START on a Logitech F310 through
`hal_input`. The script only reads the pin and does not reconnect anything. It is loaded without
`-W`, so LinuxCNC starts even if the script is missing. Errors are shown in QtPlasmaC.

## Opening the export in QtPlasmaC with a button

On a Linux computer with `~/linuxcnc/nc_files`, **Export** suggests the directory
`~/linuxcnc/nc_files/plasmanest/`. Add a button in QtPlasmaC under **SETTINGS** → **USER BUTTONS**:

| Name | Code |
|---|---|
| `Load\nest` | `latest-file /home/debian/linuxcnc/nc_files/plasmanest` |

The button opens the latest exported `.ngc` file in the directory. It only works when the machine
is idle. If you export to another directory the button does not find the file, and PlasmaNest's
status bar says so.

## PlasmaNest as a tab in QtPlasmaC

PlasmaNest can be shown as the **PLASMANEST** tab in QtPlasmaC (2.9). So far it has only been tried
in QtPlasmaC's simulator. Add this under `[DISPLAY]` in the INI file and restart LinuxCNC:

```
USER_COMMAND_FILE = /home/debian/src/plasmanest/qtplasmac/user_command.py
```

- PlasmaNest starts when the tab is first chosen and ends with QtPlasmaC. It runs as a program of
  its own, so an error in PlasmaNest does not affect QtPlasmaC.
- When you go from PLASMANEST to MAIN, the nest is loaded into the preview. That only happens if
  it has changed and the machine is idle. Figures outside the sheet, overlaps and material warnings
  give an error message instead. The **Load into QtPlasmaC when going to MAIN** check box turns
  this off.
- The tab is locked while the machine runs, like QtPlasmaC's other tabs.
- Unsaved work is saved in `~/.plasmanest/autosave.pnest` when QtPlasmaC closes.

## Rectangles, circles, hole circles and slots drawn in PlasmaNest

When a drawn figure is selected, the panel shows the **Rectangle**, **Circle**, **Hole circle** or
**Slot** box:

| Field | Meaning |
|---|---|
| Width, Height, Diameter, Length | The drawn size. A slot's length is end to end. |
| Circle diameter, Holes, Hole diameter, First hole at | For a hole circle: the circle through the hole centres, the number of holes, their size and the angle from +X to the first hole. The holes are spread evenly and cut anticlockwise from the first. |
| Material | From QtPlasmaC's material file. Chosen with `M190` in the G-code, and the kerf comes from the material. "Active in QtPlasmaC" chooses no material. |
| Cut | Not shown for a hole circle, which is always holes. **Outside (part):** the path runs half a kerf outside the drawn size, clockwise. **Inside (hole):** half a kerf inside, counter-clockwise. |
| Kerf | Taken from the material. Typed by hand when no material is chosen. |
| Lead-in | Length in mm, 0 or more. A straight line square to the edge (radial for circles), starting in the scrap. |
| Place lead-in | Point at or near the figure and click where the lead-in should be. On a hole circle, point at any hole: all holes get their lead-in at the same angle. |

- The **X** and **Y** fields give the rectangle's drawn corner and the centre of a circle, hole
  circle or slot, not the path's outer size.
- Parts are cut clockwise and holes counter-clockwise, circles and slot ends with G2 and G3.
- Holes under 30 mm are cut at 60 % of the material's speed. They start at the centre with a half
  circle out to the edge and end with a half circle back in, at most 2 mm long, with the torch on.
  The **Lead-in** field is not used for them. Slots get no reduced speed, however narrow.
- A new figure gets the same settings as the one changed last.
- The settings are kept in the figure's G-code and stay in project files.
- An impossible setting is refused with a red message, for example a lead-in that does not fit in
  the hole.

## Look

PlasmaNest has QtPlasmaC's colours, read from QtPlasmaC's settings (`[COLOR_OPTIONS]`). If the
colours change in QtPlasmaC, PlasmaNest follows at its next start.

## Two kinds of files

- **Export to QtPlasmaC (.ngc)**, Ctrl+E: the file you open in QtPlasmaC and cut with.
- **Save project (.pnest)**, Ctrl+S: your placement, to carry on working later. The file also holds
  the G-code, so it works even if the original files move. QtPlasmaC cannot read it.

## What the export does to the G-code

- All coordinates are converted to their final values, and arc centres (I/J) are rotated too.
- The file is always in millimetres with a single program end (`M2`). Inch files are converted to
  mm, including numeric F values.
- R arcs are rewritten as I/J. `F#<_hal[plasmac.cut-feed-rate]>`, `M3 $n`, `M5 $n`, `M67`, `M68`,
  `G41.1`/`G42.1` and comments are kept.
- `M190`/`M66` are only written when the material actually changes. Material comments `(o=1…)` and
  `(o=2…)` are moved to the start of the file, since QtPlasmaC needs that.
- Moves after the last cut (for example parking) are removed from each figure.

## G-code that cannot be loaded

Files that cannot be moved safely are refused with a line number, rather than giving a wrong cut:

- `G92`, `G52` and `G10` (offsets)
- O-words (subroutines, loops, conditions)
- `G18`/`G19`, splines, cycles (`G81` and others) and the axes A, B, C, U, V, W
- coordinates that depend on values only LinuxCNC knows, for example `X#<_hal[…]>`

Simple expressions and your own parameters, for example `X[#<w>/2]`, are worked out and are fine.

## Tests

```bash
python -m pip install pytest
```

```bash
python -m pytest tests
```

On Windows one test is skipped in windowless mode. It runs with `QT_QPA_PLATFORM=windows` set,
and then real windows open for a moment.
