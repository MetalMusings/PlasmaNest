# PlasmaNest

[![tests](https://github.com/MetalMusings/PlasmaNest/actions/workflows/tests.yml/badge.svg)](https://github.com/MetalMusings/PlasmaNest/actions/workflows/tests.yml)

Manual and automatic nesting of G-code for [LinuxCNC](https://linuxcnc.org) /
[QtPlasmaC](https://linuxcnc.org/docs/2.9/html/plasma/qtplasmac.html). Load G-code files, draw your
own parts and holes, place them on the sheet by hand or let **Autonest** pack them, and send one
nest straight to QtPlasmaC.

PlasmaNest runs on its own, or as a **PLASMANEST tab inside QtPlasmaC**: draw or place figures in
the tab, go to MAIN, and the nest is in the preview, ready to cut.

![PlasmaNest in QtPlasmaC's colours: a part with a hole circle, a centre hole and a slot, an L-shaped
polygon, copies of loaded G-code and a figure left out of the cut](docs/screenshot.png)

## Features

- **Load G-code** from your CAM program or QtPlasmaC's CONVERSATIONAL, copy, move and rotate it,
  and set the cutting order. Files that cannot be moved safely are refused rather than guessed at.
- **Draw figures:** rectangles, circles, hole circles, slots and polygons, as parts or holes. The
  kerf comes from QtPlasmaC's material file, and lead-ins are placed by pointing at the outline.
- **Small holes** under 30 mm are cut at 60 % speed, with half-circle lead-in and lead-out.
- **Autonest** packs the figures against the bottom edge of the sheet, with a gap between them and a
  margin to the edge, turning them in 90° steps where that packs them lower. Small parts go into
  the holes of larger ones, and whatever lies inside a contour is cut first. See
  [Autonest](#autonest).
- **Resize by dragging** a drawn figure's outline, or type exact sizes.
- **Sheets** of any shape: typed, drawn, or measured on the machine by jogging to its corners.
- **Checks** for figures outside the sheet and overlapping figures, before anything is cut.
- **Choose what to cut** with a check box per figure; the rest stays in the nest for later.
- **Exports** a single `.ngc` that QtPlasmaC's G-code filter accepts, and saves projects
  (`.pnest`) that hold the G-code itself.
- QtPlasmaC's own colours, in the tab and in its own window.

## Status

PlasmaNest is young, and written for one machine. It has been tested with LinuxCNC 2.9.4 on Debian
13 (Python 3.13), in QtPlasmaC's simulator and on a real machine with the torch disabled. The tab
relies on parts of QtPlasmaC that are not documented and may change in later versions.

**Always check the preview in QtPlasmaC, and do a dry run with TORCH ENABLE off, before cutting.**
PlasmaNest comes with no warranty; see [LICENSE](LICENSE).

## Installing on QtPlasmaC 2.9

A standard LinuxCNC 2.9 installation with QtPlasmaC already has everything PlasmaNest needs
(Python 3, PyQt5 and python3-xlib). Nothing has to be installed with apt.

### 1. Get PlasmaNest

```bash
git clone https://github.com/MetalMusings/PlasmaNest.git ~/plasmanest
```

Any other directory works too; change `~/plasmanest` below to match.

### 2. Try it on its own

```bash
python3 ~/plasmanest/plasmanest.py
```

With LinuxCNC running, PlasmaNest reads QtPlasmaC's materials and colours, and starts with a sheet
as large as the machine's work area. The files in `samples/` are good for trying it out.

### 3. Try the tab in the simulator first (recommended)

LinuxCNC ships a QtPlasmaC simulator. Make your own copy of it, so nothing in your machine's
configuration changes:

```bash
mkdir -p ~/linuxcnc/configs
cp -r /usr/share/doc/linuxcnc/examples/sample-configs/sim/qtplasmac ~/linuxcnc/configs/qtplasmac-sim
```

Add the tab as in step 4, to `~/linuxcnc/configs/qtplasmac-sim/qtplasmac_l_metric.ini`, and start
it with

```bash
linuxcnc ~/linuxcnc/configs/qtplasmac-sim/qtplasmac_l_metric.ini
```

The simulator's E-stop is on its own small panel. Close your machine's LinuxCNC first: the two
cannot run at the same time.

### 4. Add the PLASMANEST tab

Make a backup of your machine's INI file, for example `~/linuxcnc/configs/<machine>/<machine>.ini`,
and add this line under `[DISPLAY]`:

```ini
USER_COMMAND_FILE = ~/plasmanest/qtplasmac/user_command.py
```

Restart LinuxCNC. The tab **PLASMANEST** appears after QtPlasmaC's own tabs.

- PlasmaNest starts when the tab is first chosen and ends with QtPlasmaC. It runs as a program of
  its own, so an error in PlasmaNest does not affect QtPlasmaC.
- When you go from PLASMANEST to MAIN, the nest is loaded into the preview. That only happens if it
  has changed and the machine is idle. Figures outside the sheet, overlaps and material warnings
  give an error message instead. The **Load into QtPlasmaC when going to MAIN** check box turns this
  off.
- The tab is locked while the machine runs, like QtPlasmaC's other tabs. While a program is paused
  the tab may stay open, but nothing is loaded into MAIN until the machine is idle again.
- The nest is written to `~/linuxcnc/nc_files/plasmanest/current.ngc` for QtPlasmaC to load.
  Unsaved work is saved in `~/.plasmanest/autosave.pnest` when QtPlasmaC closes.

### 5. Optional: put PLASMANEST in CONVERSATIONAL's place

QtPlasmaC can hide its CONVERSATIONAL tab. Add this line at the end of your configuration's
`custom_postgui.hal`, and restart LinuxCNC:

```
setp qtplasmac.conv_disable 1
```

PLASMANEST then sits right after MAIN, and PlasmaNest hides its **Get from CONVERSATIONAL** button.
Shapes from QtPlasmaC's conversational wizards are then no longer available, and neither is the
editing of saved conversational files.

### Removing it

Delete the `USER_COMMAND_FILE` line (and the `conv_disable` line, if you added it) and restart
LinuxCNC.

### Troubleshooting

- The tab writes a log to `~/.plasmanest/qtplasmac-tab.log`. QtVCP's own log is `~/qtvcp.log`.
- If the tab only shows a message, press **Start PlasmaNest** in it, or run
  `python3 ~/plasmanest/plasmanest.py` in a terminal to see any error.

## Optional: user buttons and a pendant

### Save sheet corners from the machine

Add a button in QtPlasmaC under **SETTINGS** → **USER BUTTONS**, on a free row, and press **Save**:

| Name | Code |
|---|---|
| `Save\point` | `%~/plasmanest/save_point.py` |

QtPlasmaC only runs it with Python if the command ends in `.py`, so it takes no arguments.

- The machine must be homed. Each press saves a point, which shows up in PlasmaNest under
  **From the machine…**. QtPlasmaC only shows a message if the point could not be saved.
- With the laser on (the **Laser** button) the point under the laser cross is saved, otherwise the
  point under the torch. The laser's offset is read from `[LASER_OFFSET]` in QtPlasmaC's prefs file.
- The points are saved in `~/.plasmanest/points.txt` in **machine coordinates**. PlasmaNest converts
  them with the origin LinuxCNC has when the sheet is made, so the origin can be zeroed after
  measuring. If you zero the origin after the sheet has been made, make the sheet again.
- Take the corners in order around the sheet. **Clear all** before measuring a new sheet.
- The safety margin moves each edge inwards. If the margin makes the shape invalid, the sheet is
  refused.

### Save a point from a pendant or gamepad

A button on a pendant can save the point the same way. Add a line at the end of
`custom_postgui.hal` and restart LinuxCNC:

```
loadusr python3 /home/<user>/plasmanest/point_button.py input.0.btn-start
```

The last word is the button's HAL pin: `input.0.btn-start` is START on a Logitech F310 through
`hal_input`. The script only reads the pin and does not reconnect anything. It is loaded without
`-W`, so LinuxCNC starts even if the script is missing. Errors are shown in QtPlasmaC.

### Open the latest export with a button

On a Linux computer with `~/linuxcnc/nc_files`, **Export** suggests the directory
`~/linuxcnc/nc_files/plasmanest/`. A user button with QtPlasmaC's built-in `latest-file` code opens
the newest `.ngc` file there:

| Name | Code |
|---|---|
| `Load\nest` | `latest-file /home/<user>/linuxcnc/nc_files/plasmanest` |

Write the path out in full: `latest-file` does not understand `~`. The directory must not contain
spaces, and the button only works when the machine is idle. If you
export elsewhere the button does not find the file, and PlasmaNest's status bar says so.

## Use

| What | How |
|---|---|
| Load G-code | **Load…** or Ctrl+L. The figure is put in a free place on the sheet. |
| Rectangular sheet | Width and height in the panel. The sheet's lower left corner is G-code X0 Y0. At start the sheet is as large as QtPlasmaC's work area, the X and Y travel in its INI file; without LinuxCNC it is 3000 × 1500 mm. |
| Draw the sheet | **Draw**, and click the corners with the mouse. Finish with a double click, Enter or a click on the first corner. Backspace removes the latest corner, Esc cancels and Shift gives straight edges. The corners land on whole millimetres. |
| Type the sheet's corners | **Corners…** shows the corners in a table, where you can change, add and remove them. Use it for exact sizes. |
| Sheet from the machine | Jog to the corners of the sheet and press the **save point** user button in QtPlasmaC at each one. Open **From the machine…** in PlasmaNest, where the points appear one by one in a table and a drawing. The drawing shows the edges, the latest point and the sheet after the margin. Choose a safety margin and press **Use as sheet**. See [Optional: user buttons and a pendant](#optional-user-buttons-and-a-pendant) for the button. |
| Back to a rectangle | **Rectangle** gives a rectangle with the polygon's outer size, with its corner at X0 Y0. |
| Move | Drag with the left mouse button, type X/Y in the panel, or use the arrow keys (1 mm, 10 mm with Shift). |
| Rotate | Hold the right mouse button on the figure and drag around it, and the figure follows the pointer (Ctrl gives 15° steps). The green ring, Shift+mouse wheel, the R key, the buttons in the panel and typing the angle also work. |
| Select several | Drag a box on empty space, or Ctrl-click. Several figures rotate as a group. |
| Draw a rectangle | **Draw rectangle**: a label at the pointer shows X and Y. Press at a corner and drag to the opposite corner, or click both. The label shows width × height. The corners land on whole millimetres, and Esc cancels. Then the exact **Width** and **Height** can be typed in the panel: type the width, press Tab, type the height and press Enter. The first corner stays put. |
| Draw a circle | **Draw circle**: press at the centre and drag out to the edge, or click the centre and then the edge. The label shows X and Y and then the diameter in whole millimetres. The exact **Diameter** is typed in the panel. |
| Draw a hole circle | **Draw hole circle**: press at the centre and drag out to the hole centres, or click the centre and then a hole centre. The new hole circle gets the number and size of holes used last (at first 4 × Ø10). |
| Draw a polygon | **Draw polygon**, and click the points, on whole millimetres; Shift gives straight edges and Backspace takes back the latest point. A click on the first point closes it: a part or a hole, like a rectangle. A double click or Enter leaves it open: a line cut from one end to the other, with no kerf offset and no fill, so it is never counted as overlapping. Its lead-in carries straight on back from the start end, so the pierce is off the line; **Place lead-in** chooses the end it starts from. Esc cancels. Afterwards the panel lists the points as they lie on the sheet: type new values, add a point halfway along the edge after the chosen row, remove one, or close and open it. A point can also be dragged in the view, where the pointer turns into a cross. |
| Draw a slot | **Draw slot**: press at a corner and drag to the opposite corner, or click both, as for a rectangle. The slot fills the box along its longer side: two half circles joined by straight sides. Then the exact **Length** (end to end) and **Width** can be typed in the panel. Turn it like any figure. |
| Resize a drawn figure | Point at the outline of a rectangle, circle, hole circle or slot drawn in PlasmaNest: the pointer turns into a resize arrow. Drag to resize, on whole millimetres; a label shows the size, and Esc cancels. A rectangle's edge or corner moves and the opposite edges stay. A circle keeps its centre. Any hole of a hole circle sizes all its holes, and the dotted circle through the hole centres, between the holes, sizes that circle. A slot's side sets its width, and an end its length while the other end stays. Figures under about 24 pixels across can only be moved. |
| Copy | **Copy…** or Ctrl+D, and give the number. The copies are put in free places. |
| Autonest | **Autonest…** or Edit → Autonest… packs the selected figures, or all checked figures, against the bottom edge of the sheet. See [Autonest](#autonest). |
| Delete | **Delete** (selected figures) or **Delete the whole G-code** (all copies of the file). |
| Cutting order | The list shows the order. Change it with **Cut earlier** and **Cut later**. |
| Choose what to cut | Uncheck a figure in the list to leave it out: it stays in the nest, drawn grey and dashed, but is neither exported nor sent to MAIN. It is not checked for overlaps or the sheet's edge either, so it can wait beside the sheet. The check boxes are saved in the project, copies get their original's, and Space toggles the selected row. |
| Undo | Ctrl+Z. Redo with Ctrl+Y (Linux: Ctrl+Shift+Z). |
| View | The mouse wheel zooms, the middle button or a right drag on empty space pans, F fits to the window. |

Figures outside the sheet turn **red** and overlapping figures turn **orange**. Overlaps stop the
nest going to MAIN and are asked about before an export, unless **Allow overlapping figures** is
checked: then they stay orange but go through. The check box is saved with the project. A figure
inside a hole in another does not count as overlapping, also when the hole is drawn in PlasmaNest,
nor do holes drawn in PlasmaNest (holes, hole circles and slots) that lie wholly in another
figure's material. The check uses the programmed path, so the kerf is not included: leave enough
distance yourself.

The sheet can have any shape as long as its edges do not cross, for example an offcut with a notch.
It need not lie at G-code X0 Y0, which is shown with a purple cross, but the corners' coordinates
must match how the sheet lies in the machine. The sheet's shape is only a check: it does not change
the G-code, and QtPlasmaC does not know about it.

### Autonest

**Autonest…** (in the figure panel, or Edit → Autonest…) places figures automatically. It packs
them against the bottom edge of the sheet, the edge at the lowest Y, so that the rest of the sheet
stays in one piece for later.

**What it moves**

- With figures selected, only those are packed. The other checked figures stay where they are, and
  the packed ones keep clear of them.
- With nothing selected, all checked figures are packed.
- Unchecked figures are never moved and never kept clear of: they are not cut.
- A figure lying inside another, such as a hole drawn in a part, moves together with it, also if
  only one of them is selected.

**Settings**

| Setting | Meaning |
|---|---|
| Gap between figures | The least distance between the cut paths of two figures, lead-ins included. Allow for the kerf: the gap is measured between programmed paths. |
| Margin to the sheet edge | The least distance from any cut path to the sheet's edge. |
| Turn figures | Allows turns of 90°, 180° and 270° when that packs a figure lower. A figure is only turned if it gains something. |

The dialog remembers the values until PlasmaNest is closed. At first both distances are 10 mm.

**How figures are placed**

- The largest figure goes first. Each figure goes as low as it can, and then as far left as it can.
  Smaller figures fill the gaps the larger ones leave, also between and beside them.
- Each figure counts as its convex outline, lead-ins included. The gap is therefore always kept,
  often by a few tenths of a millimetre more, but concave parts are not interlocked: an L-shape
  is not tucked into the corner of another.
- Sheets of any shape work, such as an offcut with a notch.
- Figures that do not fit are put beside the sheet, to the right, where they show red. The status
  bar tells how many.

**Parts in holes**

Before any free sheet is used, Autonest tries the holes of the figures already on the sheet. A
figure that fits in a hole goes there, keeping the gap to the hole's edge and to everything else
cut inside the hole, such as the hole's lead-in. This works for holes in loaded G-code and for holes
drawn in PlasmaNest. Only convex holes are used: circles, rectangles, slots and the like. A figure
put in a hole can take still smaller figures in its own holes.

**Cutting order**

After packing, the cutting order is sorted from the inside out. Every figure lying wholly inside a
closed contour of another figure is cut before it, while the sheet round it is still attached and
cannot drop or tip:

- a part in a hole is cut before the hole
- a hole drawn in a part is cut before the part's outline.

What lies inside is moved forward to just before its contour, and the rest of the order stays as it
was. The order inside one loaded G-code file is the file's own.

Ctrl+Z undoes the whole Autonest, the cutting order included. Check the result in QtPlasmaC's
preview before cutting, as for any nest.

### Figures drawn in PlasmaNest

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

- The **X** and **Y** fields give the rectangle's drawn corner, the centre of a circle, hole
  circle or slot and a polygon's first point, not the path's outer size.
- A closed polygon's kerf offset keeps sharp corners. A kerf or lead-in that does not fit the
  shape is refused, as is a polygon whose edges cross.
- Parts are cut clockwise and holes counter-clockwise, circles and slot ends with G2 and G3.
- Holes under 30 mm are cut at 60 % of the material's speed. They start at the centre with a half
  circle out to the edge and end with a half circle back in, at most 2 mm long, with the torch on.
  The **Lead-in** field is not used for them. Slots get no reduced speed, however narrow.
- A new figure gets the same settings as the one changed last.
- The settings are kept in the figure's G-code and stay in project files.
- An impossible setting is refused with a red message, for example a lead-in that does not fit in
  the hole.

### Two kinds of files

- **Export to QtPlasmaC (.ngc)**, Ctrl+E: the file you open in QtPlasmaC and cut with.
- **Save project (.pnest)**, Ctrl+S: your placement, to carry on working later. The file also holds
  the G-code, so it works even if the original files move. QtPlasmaC cannot read it.

### What the export does to the G-code

- All coordinates are converted to their final values, and arc centres (I/J) are rotated too.
- The file is always in millimetres with a single program end (`M2`). Inch files are converted to
  mm, including numeric F values.
- R arcs are rewritten as I/J. `F#<_hal[plasmac.cut-feed-rate]>`, `M3 $n`, `M5 $n`, `M67`, `M68`,
  `G41.1`/`G42.1` and comments are kept.
- `M190`/`M66` are only written when the material actually changes. Material comments `(o=1…)` and
  `(o=2…)` are moved to the start of the file, since QtPlasmaC needs that.
- Moves after the last cut (for example parking) are removed from each figure.

### G-code that cannot be loaded

Files that cannot be moved safely are refused with a line number, rather than giving a wrong cut:

- `G92`, `G52` and `G10` (offsets)
- O-words (subroutines, loops, conditions)
- `G18`/`G19`, splines, cycles (`G81` and others) and the axes A, B, C, U, V, W
- coordinates that depend on values only LinuxCNC knows, for example `X#<_hal[…]>`

Simple expressions and your own parameters, for example `X[#<w>/2]`, are worked out and are fine.

## Running PlasmaNest elsewhere

PlasmaNest also runs without LinuxCNC, for example to prepare nests on another computer. It needs
Python 3.9 or later and PyQt5:

```bash
python -m pip install -r requirements.txt
python plasmanest.py
```

On Windows, `start.bat` starts it; on Linux, `sh start.sh`. Without LinuxCNC there are no materials
or machine colours, and the sheet starts at 3000 × 1500 mm.

## Development

```bash
python -m pip install pytest
python -m pytest tests
```

The tests need no LinuxCNC and no screen: Qt runs offscreen. `QT_QPA_PLATFORM=xcb` runs them in real
windows. On Windows one test is skipped in windowless mode.

| Path | What |
|---|---|
| `plasmanest/gcode.py` | Reads G-code into moves in mm, and refuses what cannot be moved safely. |
| `plasmanest/nest.py` | Placements, the sheet, the export and project files. |
| `plasmanest/shapes.py` | The figures drawn in PlasmaNest, written as G-code. |
| `plasmanest/autonest.py` | Packs figures against the bottom edge of the sheet. |
| `plasmanest/gui.py` | The user interface. |
| `plasmanest/live.py` | The files the tab hands to QtPlasmaC. |
| `plasmanest/machine.py`, `save_point.py`, `point_button.py` | Sheet corners from the machine. |
| `plasmanest/materials.py`, `plasmanest/theme.py` | QtPlasmaC's materials, work area and colours. |
| `qtplasmac/` | The script QtPlasmaC runs to add the tab. |
| `tools/embed_host.py` | Embeds PlasmaNest as QtVCP does, to try the tab without QtPlasmaC. |

Bug reports and suggestions are welcome as GitHub issues.

## License

PlasmaNest is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the
License, or (at your option) any later version. See [LICENSE](LICENSE).
