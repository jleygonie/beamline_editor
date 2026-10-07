# Beamline Layout Editor

A desktop editor (PyQt6) for viewing and editing beamline layouts stored as MAD-X survey (TFS) files.
Elements are drawn to scale along `s`. You can move them, resize them, insert or remove them, and
measure distances. Unchanged rows are written back byte for byte, so the files stay diff-friendly.

## Installation

Requires Python 3.10 or newer.

```bash
./create_venv.sh            # creates .venv/ and installs requirements.txt
./create_venv.sh python3.12 # or with a specific interpreter
```

Running the script again updates the packages in the existing environment.

## Running

```bash
./run_beamline_editor.sh                              # empty window
./run_beamline_editor.sh data/1_LINAC_Survey.txt      # open one file
./run_beamline_editor.sh data/*.txt                   # open several files glued together
```

The script can be called from any directory.

## Features

### Viewing
- **Zoom** with the mouse wheel, **pan** by dragging an empty area, **Fit** (`F`) to show the whole line.
- **Unit**: show lengths in m or mm.
- **Labels**: show or hide element names (overlapping names are hidden automatically).
- **Right to left**: draw the line with `s` increasing to the left.
- **Floor plan**: draw the top view, where the line turns at every element that has an angle (see below).
  Editing, dragging and measuring work the same as in the straight view. The ruler then shows the floor
  `x` coordinate.
- **Apertures**: show or hide the beam pipe sections defined in the JSON file (see below).
- Hover over an element to see its position and length.

### Editing
- Click an element to select it and edit it in the **Properties** panel: name, type, keyword,
  position (start, centre or end), length, gaps to its neighbours, and type-specific parameters.
  - Setting the **centre** slides the element along the line (same length). Setting the **start** or
    **end** moves only that edge, so the element grows or shrinks and its centre follows.
  - **Move** keeps the total line length: the drifts on both sides absorb the change.
  - **Shift** also moves everything downstream.
  - `$START` / `$END` markers can be moved too: the drift next to them stretches or shrinks, which
    changes the line length.
  - A position is always applied, even if the element ends up on top of another one: overlapping
    elements are drawn in red (canvas and table) and reported as errors until they are moved apart.
- Drag an element on the canvas to move it (positions snap to 0.1 mm).
- `Ctrl`+click a second element to show and edit the distances between the two
  (centre-to-centre, edge gap, start-to-start, end-to-end).
- **Insert…** (`Ins`) adds an element at the given centre (by default in the gap after the selected
  element), cutting the drift there; **Delete** removes the selected element and merges
  the drifts around it.
- The **Elements** table at the bottom lists every element. It can be filtered and edited directly.
- Undo / redo with `Ctrl+Z` / `Ctrl+Y`.
- The status bar shows errors and warnings (overlaps, chain breaks, elements outside `$START`–`$END`).
  Click it for the list.

### Angles and floor plan
Every element has an **angle** (the survey `ANGLE` column), in radians. You can edit it in the Properties
panel or in the `Angle` column of the table. As in MAD-X, a positive angle turns the beam to the right.
In the floor-plan view, a thick element with an angle follows an arc, and a zero-length one is a kink.
The line can start at a given position and direction. Set this with `origin` in the JSON file: `x` and
`y` in m (y upwards), and `angle` in rad (counter-clockwise from the x axis).

### Apertures (beam pipe)
Aperture sections are listed in the JSON file next to the elements, not inside them, so they can overlap
anything. They are drawn as a translucent band behind the elements and the beam line, with half-height
equal to the radius (1 px per mm). Hover over one to see its values. All values are in m:

```json
"apertures": [
  {"name": "VC.LINAC", "s_start": 0.0, "s_end": 21.0, "radius": 0.02},
  {"name": "VC.QUADS", "from": "CA.QFD0350", "to": "CA.QFD0360", "radius": 0.015, "radius_end": 0.025,
   "comment": "tapered"}
]
```

- `s_start` / `s_end` give the extent. Alternatively, `from` / `to` name an element: the section then
  starts at that element's start, or ends at its end, and follows it when it moves or is renamed.
- `radius_end` (optional) makes the radius change linearly along the section.
- Apertures are saved only in JSON. Survey files have no place for them.

### Measuring
- Press **Measure** (`M`). Every point you can pick is highlighted in orange: the start, centre and end
  of each element, and the markers.
- Click two points. A bar is added above the line with the distance written in a box.
- Add as many bars as you like. They stay on the figure when you leave measure mode, and they follow the
  elements when you edit them.
- Click a bar to select it, then press `Delete` to remove it. **Clear measures** removes them all.
- `Esc` cancels a half-made measurement; pressing it again leaves measure mode.
- Bars are not saved, and opening another file clears them.

### Gluing several files
**Open & glue…** (`Ctrl+Shift+O`) loads several files and joins them into one beamline.
In the dialog you set the order (upstream first) and choose one of two options:
- **Keep the s positions from the files**: any gap is filled with a drift named `GLUE.DRF_NEW_n`,
  and an overlap is reported as a warning.
- **Place the files end to end**: each file is moved so it starts where the previous one ends.

If two files use the same element name, the later one is renamed with a suffix (for example `_2`).
The result can be saved as a single survey or JSON file.

### Comparing two layouts
**Compare…** (`Ctrl+D`) opens a comparison window. Select 2 files to compare them with each other, or
select 1 file to compare it with the layout currently open (including unsaved edits).

- Elements are matched **by name**. Each one is classified as identical, **changed** (position, length,
  type, keyword, angle, parameters or comment), **only in A** or **only in B**.
- The two lines are drawn on the same `s` axis, with A above B. Differences are highlighted in orange
  (changed), red (only in A) and green (only in B), and unchanged elements are faded. A line joins the
  two versions of each element, so a moved element shows up as a slanted line.
- The **Differences** table lists every difference with positions, lengths and the change in each.
  Click a row to centre the view on that element, or click an element in the drawing to find its row.
- **Align B on** shifts B along `s` before comparing. The choices are the positions from the files, the
  best fit over all common elements, or one chosen common element. Use this when one file has a
  different origin.
- **Drifts** also compares the drifts (off by default, because a drift changes whenever its neighbour
  moves). **List unchanged** adds the identical elements to the table. **Swap A/B** swaps the two sides.
- **Export CSV…** saves the table, in metres.

### Files
- **Open…** (`Ctrl+O`) reads MAD-X survey files (`.txt`, `.tfs`) and the editor's JSON format.
- **Save survey…** (`Ctrl+S`) writes a survey file. Rows that were not changed are copied exactly.
- **Save JSON…** (`Ctrl+Shift+S`) writes a JSON file that also stores types, parameters, comments, the
  gaps to each element's neighbours, the apertures and the floor-plan origin.

## Project layout

```
beamline_editor/
├── create_venv.sh          create the virtual environment
├── run_beamline_editor.sh  launch the editor
├── requirements.txt
├── data/                   sample survey and JSON files
├── src/
│   ├── main.py             entry point and main window
│   ├── canvas.py           beamline drawing, dragging, measurements
│   ├── panels.py           element table, properties and distance panels
│   ├── model.py            data model, edit operations and validation
│   ├── geometry.py         floor-plan geometry from the bend angles
│   ├── element_types.py    element type catalogue (colours, heights, parameters)
│   ├── survey_io.py        MAD-X survey reader / writer
│   ├── json_io.py          JSON reader / writer
│   ├── glue.py             gluing several beamlines together
│   ├── compare.py          element-by-element comparison of two beamlines
│   └── compare_window.py   comparison window (drawing and table of differences)
└── tests/                  unit tests
```

## Tests

```bash
.venv/bin/python -m unittest
```
