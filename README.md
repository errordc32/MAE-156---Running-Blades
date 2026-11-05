# MAE 156 – Low-Cost Running Blades

Repo for the MAE 156 project on designing a low-cost running blade.

## Project goals

- Design a running blade that costs a fraction of commercial carbon-fiber blades
- Match the stiffness and energy-return behaviour of a commercial blade closely enough to be usable
- Survive the loads of running (typically 2.5–3× body weight at ground contact) with an adequate safety factor
- Use materials and processes that can be built with student/makerspace equipment

## Getting started

You need **[Git for Windows](https://git-scm.com/download/win)** or **[GitHub Desktop](https://desktop.github.com)** (both include [Git LFS](https://git-lfs.com), which this repo uses for CAD files, PDFs and images), plus **Python 3.8+** for the CAD Checkout tool.

**GitHub Desktop:** File → Clone repository → `errordc32/MAE-156---Running-Blades`. Click yes if it asks to initialize Git LFS.

**Command line:**

```bash
git lfs install          # once per computer
git clone https://github.com/errordc32/MAE-156---Running-Blades.git
```

Then open `tools/CAD Checkout.bat` once. It checks your setup and fixes anything missing (see [Troubleshooting](#troubleshooting)).

## Repository layout

```
cad/
  parts/            Individual part files
  assemblies/       Assemblies (blade + socket adapter, test fixtures, etc.)
  drawings/         Engineering drawings (PDF exports too)
  exports/          Neutral formats: STEP, STL, DXF
analysis/
  fea/              FEA setup notes and result summaries (no raw solver output)
  calculations/     Hand calcs, MATLAB/Python scripts, spreadsheets
testing/
  procedures/       Test plans and procedures
  data/             Raw and processed test data
manufacturing/      Mold designs, layup schedules, process notes, BOM / cost tracking
docs/
  reports/          Proposals, design reviews, final report
  presentations/    Slide decks
  meeting-notes/    Weekly meeting notes (copy TEMPLATE.md, name files YYYY-MM-DD.md)
research/           Reference papers, see research/README.md for the index
tools/              CAD Checkout (SolidWorks lock/check-in tool)
```

## Conventions

- **File names:** lowercase with hyphens, e.g. `blade-c-shape.SLDPRT`. Don't put versions in source file names (git history is the version history); exports can carry one, e.g. `blade-c-shape-rev-b.step`.
- **Units:** SI (mm, N, MPa) unless noted.
- **Commits:** small and descriptive, e.g. `Add FEA of C-shape blade at 3x BW`.
- **Large files:** keep raw solver results and test videos out of git. Put them on the shared drive and link to them.

## Working with SolidWorks

**SolidWorks version:** everyone must use the same version, including the service pack: `RX_2025`. Files saved in a newer version can never be opened in an older one.

Git can't merge two people's changes to a SolidWorks file, so SolidWorks files (`.SLDPRT`, `.SLDASM`, `.SLDDRW`, templates) are **lockable**. They stay **read-only** until you lock them, and nobody else can push changes to a file you've locked.

### CAD Checkout (recommended)

Double-click **`tools/CAD Checkout.bat`**. It lists every SolidWorks file with who has it locked, whether you've changed it, and whether it's open in SolidWorks. Your locked files show in orange, and files a teammate has locked are greyed out.

1. **Double-click** a file. This locks it, pulls the latest version and opens it in SolidWorks, ready to edit. (To lock several at once, select them and click **Start editing**.)
2. Edit in SolidWorks, then **save and close**.
3. Select the same file(s) → **Done editing** → type what you changed. This commits, pushes and unlocks.

Double-clicking a file someone else has locked offers to open it read-only instead.

Files are shown in their folders (`assemblies/` and `parts/` start expanded). Each folder row sums up the locks and changes inside it. **Selecting a folder selects everything in it**, so you can lock or check in a whole sub-assembly folder in one go. The exception is the top-level folders (`assemblies/`, `drawings/`, `exports/`, `parts/`): they can't be acted on as a whole, and the buttons grey out when one is selected.

| Button | What it does |
|---|---|
| **Start editing** | Locks the selected files and pulls the latest version, without opening them |
| **Open read-only** / **Open** | Opens in SolidWorks without locking. It shows as **Open**, with an orange outline, when the selected files are already locked by you, so they open editable |
| **Discard changes** | Reverts your edits (after confirming) and unlocks |
| **Refresh** | Updates lock status. A full refresh happens every 60 s, and a quick one whenever you switch back to the window |

It won't check in a file that's still open in SolidWorks, and won't let you start editing a file someone else has locked.

### Command line

```bash
git lfs lock cad/parts/blade.SLDPRT         # 1. claim it (makes it writable)
git pull                                    # 2. get the latest version
#    ... edit and save in SolidWorks, then close the file ...
git add cad/parts/blade.SLDPRT
git commit -m "Thicken blade toe section to 9 mm"
git push                                    # 3. push your changes
git lfs unlock cad/parts/blade.SLDPRT       # 4. release it
```

`git lfs locks` shows who has what locked. GitHub Desktop has no lock buttons, so use CAD Checkout or *Repository → Open in Command Prompt* for these.

### Rules

1. **Lock the parent assembly too** if you're changing mates or adding components.
2. **Close files in SolidWorks before pulling or switching branches.** SolidWorks caches open files and can save a stale copy over a teammate's work.
3. **Never rename or move SolidWorks files in Explorer or with git.** Assemblies and drawings find parts by path. Use *File → Rename* in SolidWorks (with the assembly open), or Pack and Go.
4. **Keep every referenced file inside this repo.** Save Toolbox fasteners and downloaded vendor parts as normal parts in `cad/parts/`. Don't reference `C:\SOLIDWORKS Data`.
5. **Drive the blade with global variables** (Tools → Equations: `thickness`, `c_radius`, `width`, `layer_count`, ...). Make variants as **configurations**, not copied files.
6. **Assign real materials.** Custom materials (e.g. our glass/epoxy layup) go in a `.sldmat` library in `cad/`.
7. **Don't commit Simulation results** (`*.cwr`, ignored automatically). The study setup is saved in the part. Put summary plots and numbers in `analysis/fea/`.
8. **Commit at meaningful points**, not on every save. Every version of a CAD file is stored forever and counts against the LFS quota.
9. **Export a STEP and a PDF drawing** to `cad/exports/` and `cad/drawings/` at each design review.

## Troubleshooting

| Problem | Fix |
|---|---|
| SolidWorks opens a file **read-only** | You haven't locked it. Start editing in CAD Checkout, then close and reopen the file. |
| SolidWorks says a file is **corrupt** / file is ~130 bytes | It's an LFS placeholder. Open CAD Checkout and accept the download, or run `git lfs pull`. |
| Push **rejected** because of locks | Someone else has that file locked. Check `git lfs locks` and ask them to unlock. Don't force it. |
| CAD Checkout says **Git LFS not set up** | Click yes to run `git lfs install` (one-time). |
| CAD Checkout says **Git not found** | Install [Git for Windows](https://git-scm.com/download/win), or GitHub Desktop (CAD Checkout uses its built-in git). |
| Need to release a teammate's lock (they're away) | `git lfs locks`, then `git lfs unlock --force <file>`. Only after checking with them, because their unpushed work will conflict. |
