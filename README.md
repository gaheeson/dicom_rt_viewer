# DICOM RT Viewer

A lightweight desktop viewer for DICOM radiotherapy files — **RT Record**, **RT Plan**, and **RT Dose** — built with Python and tkinter.

Originally developed for reviewing carbon-ion therapy delivery logs (RT Records), it has been extended into a full RT DICOM inspector.

![DICOM RT Viewer screenshot](docs/screenshot.png)

---

## Features

### All modalities
- Tag tree browser with expand / collapse
- Modality-specific summary (Summary tab)
- PHI hide toggle (patient name / ID / DOB masked)
- Tag search by name, number, or value — hit count shown next to the search bar
- **Expand ▾ / Collapse ▴** toggle button — one click expands the full tree, one more collapses it
- Right panel (Map / Summary) is **hidden by default** — click **Map / Summary ▶** to reveal; auto-opens when a Control Point is clicked
- **📄 file indicator** (toolbar, right side) — hover to see primary and overlay filenames; `▶` marks which file is currently shown in the tree

### RT Record & RT Plan — Spot Map
- Spot delivery path visualization per energy layer (control point)
- Spot size scaled by MU weight; first / last spot highlighted
- Fixed-coordinate spots detected and labeled
- **Hover tooltip**: hover the cursor over any spot to see its coordinates and MU value
- **Scroll wheel**: scroll over the spot map to jump to the previous/next energy layer
- **Plan vs Record overlay**: load a second file (RT Plan or RT Record) to compare spot positions on the same axes
  - Plan spots: open blue circles · Record spots: filled red circles
  - **Deviation arrows**: arrows drawn from plan → record position; RMS Δ and max Δ shown in the title. Visible only when real positional differences exist between the two files.
  - **Show/hide toggles**: independently show or hide Primary and Overlay spots
  - **🌲 Tree: Primary / Overlay** button (toolbar) — switch the tag tree to browse the overlay file; click again to return. Active only when an overlay is loaded.
- **Spot Settings…**: configure spot display via a dedicated dialog:
  - Single file: spot color, path line color
  - First / last spot: show/hide toggle + legend label text + color
  - Overlay: Plan and Record legend label + color, deviation arrow color
- Layer navigation with keyboard arrows

### RT Dose — Dose Map
- Axial / Coronal / Sagittal slice viewer
- Slice slider with current index display
- **Scroll wheel**: scroll over the dose map to move to the previous/next slice
- **W/L (Window/Level) sliders**: adjust Min / Max display range in Gy independently of the data
- Reset button restores full range

### Export
- Save current figure as PNG / PDF / SVG

### Keyboard shortcuts
| Key | Action |
|-----|--------|
| `Ctrl+O` | Open file |
| `Ctrl+Shift+O` | Open overlay file |
| `Ctrl+S` | Save figure |
| `← →` or `↑ ↓` | RT Dose: previous/next slice · RT Plan/Record: previous/next energy layer |

---

## Requirements

```
python >= 3.8
pydicom
numpy
matplotlib
tkinter  # included in standard python.org builds
```

Install dependencies:

```bash
pip install pydicom numpy matplotlib
```

> **macOS (Homebrew Python)**: tkinter is not bundled. Install it with:
> ```bash
> brew install python-tk
> ```

---

## Usage

```bash
# Launch and open a file via the GUI
python dicom_rt_viewer.py

# Open a file directly
python dicom_rt_viewer.py path/to/file.dcm
```

### Basic workflow

1. Click **Open…** (or `Ctrl+O`) and select any `.dcm` file.
2. Browse the tag tree on the left. Click a sequence item (`> 0`, `> 1`, …) to expand it, or use **Expand ▾** to open everything at once.
3. Click a **Control Point** item (RT Plan / RT Record) — the right panel opens automatically and shows the spot map.
4. For RT Dose, the **Dose Map** tab opens automatically. Use the direction combo and slice slider to navigate.

### Plan vs Record overlay

1. Open an RT Plan **or** RT Record as the primary file.
2. Click **Overlay…** (or `Ctrl+Shift+O`) and select the complementary file.
3. Navigate to any control point — both spot patterns are drawn on the same axes.
4. Use the **Primary** / **Overlay** checkboxes to show or hide each set of spots independently.
5. Check **Deviation arrows** to draw arrows from each plan spot to the corresponding record spot (visible when the two files differ in position).
6. Click **🌲 Tree: Primary** (toolbar) to switch the tag tree to the overlay file; click **🌲 Tree: Overlay** to switch back.
7. Click **Clear overlay** to remove.

### Spot Settings

Click **Spot Settings…** to open a configuration dialog:

| Section | Options |
|---------|---------|
| Single file | Spot color, path line color |
| First spot | Show/hide toggle, legend label text, color |
| Last spot | Show/hide toggle, legend label text, color |
| Overlay | Plan label + color, Record label + color, deviation arrow color |

Unchecking a first/last spot toggle hides that marker entirely. Clearing the label text removes it from the legend.

### W/L adjustment (RT Dose)

- Drag the **Min** slider to raise the lower display cutoff (suppresses low-dose background).
- Drag the **Max** slider to lower the upper limit (enhances contrast in low-dose regions).
- Click **Reset** to restore `[0, max_dose]`.

---

## Sample files

| File | Description |
|------|-------------|
| `sample_rtplan.dcm` | Synthetic RT Ion Plan — prostate-shaped spot pattern, 2 beams × 7 energy layers |
| `sample_rtrecord.dcm` | Matching RT Record with realistic delivery deviations (σ=0.3 mm position noise, 0.5% MU noise) — load as overlay to see deviation arrows |

Generate new samples:

```bash
python make_sample_rtplan.py
python make_sample_rtrecord.py              # default σ=0.3 mm
python make_sample_rtrecord.py --sigma 1.5  # larger deviations for clearer arrows
python make_sample_rtrecord.py --fraction 0 sample_rtrecord_qa.dcm  # QA record
```

---

## File structure

```
dicom_rt_viewer.py      # main application (single file, no external dependencies beyond pip)
make_sample_rtplan.py   # generates sample_rtplan.dcm
make_sample_rtrecord.py # generates sample_rtrecord.dcm (matched to the plan)
sample_rtplan.dcm       # example RT Ion Plan
sample_rtrecord.dcm     # example RT Record with delivery deviations
README.md               # this file (English)
README_KO.md            # Korean version
requirements.txt
LICENSE
```

---

## Supported DICOM SOP Classes

| SOP Class UID | Modality |
|---------------|----------|
| 1.2.840.10008.5.1.4.1.1.481.2 | RT Dose |
| 1.2.840.10008.5.1.4.1.1.481.5 | RT Plan |
| 1.2.840.10008.5.1.4.1.1.481.8 | RT Ion Plan |
| 1.2.840.10008.5.1.4.1.1.481.9 | RT Ion Beams Treatment Record |
| 1.2.840.10008.5.1.4.1.1.481.4 | RT Beams Treatment Record |

---

## License

MIT
