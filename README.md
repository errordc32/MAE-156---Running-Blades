# MAE 156 – Low-Cost Running Blades

Repo for the MAE 156 project on designing a low-cost running blade.

## Project goals

- Design a running blade that costs a fraction of commercial carbon-fiber blades
- Match the stiffness and energy-return behaviour of a commercial blade closely enough to be usable
- Survive the loads of running (typically 2.5–3× body weight at ground contact) with an adequate safety factor
- Use materials and processes that can be built with student/makerspace equipment

## Repository layout

```
cad/
  parts/            Individual part files
  assemblies/       Assemblies (blade + socket adapter, test fixtures, etc.)
  drawings/         Engineering drawings (PDF exports too)
  exports/          Neutral formats: STEP, STL, DXF
analysis/
  fea/              FEA models and result summaries (no raw solver output)
  calculations/     Hand calcs, MATLAB/Python scripts, spreadsheets
testing/
  procedures/       Test plans and procedures
  data/             Raw and processed test data
manufacturing/      Mold designs, layup schedules, process notes, BOM / cost tracking
docs/
  reports/          Proposals, design reviews, final report
  presentations/    Slide decks
  meeting-notes/    Weekly meeting notes (name files YYYY-MM-DD.md)
research/           Reference papers, commercial blade specs, benchmarks
```

## Conventions

- **File names:** lowercase with hyphens, e.g. `blade-c-shape-v2.sldprt`. Put the version in the name only for exported files; rely on git history for source files.
- **CAD:** binary CAD files can't be merged. Tell the team before editing a shared part/assembly, and commit + push as soon as you're done. Export a STEP alongside major revisions.
- **Units:** SI (mm, N, MPa) unless noted.
- **Commits:** small and descriptive, e.g. `Add FEA of C-shape blade at 3x BW`.
- **Large files:** keep raw solver results and huge test videos out of git; link to the shared drive instead.

## Getting started

This repo uses [Git LFS](https://git-lfs.com) for CAD files, PDFs and images. GitHub Desktop and Git for Windows include it.

```bash
git lfs install          # once per computer
git clone <repo-url>
cd "MAE 156 - Running Blades"
```

## Working with SolidWorks

**SolidWorks version:** everyone must use the same version (incl. service pack): `____`. Files saved in a newer version can never be opened in an older one.

### Lock before you edit

SolidWorks files (`.SLDPRT`, `.SLDASM`, `.SLDDRW`, templates) are *lockable*. They check out **read-only** until you lock them, so two people can't edit the same file at once (git can't merge them).

```bash
git pull                                    # 1. get the latest
git lfs lock cad/parts/blade.SLDPRT         # 2. claim it (makes it writable)
#    ... edit and save in SolidWorks, then close the file ...
git add cad/parts/blade.SLDPRT
git commit -m "Thicken blade toe section to 9 mm"
git push                                    # 3. push your changes
git lfs unlock cad/parts/blade.SLDPRT       # 4. release it
```

- `git lfs locks` shows who has what locked.
- Pushing a file someone else has locked will be rejected. Ask them to unlock, don't force it.
- If SolidWorks opens a file as read-only, you haven't locked it yet.
- Lock the parent assembly too if you're changing mates or adding components.

### Rules

1. **Close files in SolidWorks before `git pull` / `git checkout`.** SolidWorks caches open files and can save a stale copy over a teammate's work.
2. **Never rename or move SolidWorks files in Explorer or with git.** Assemblies and drawings find parts by path. Use *File → Rename* in SolidWorks (with the assembly open), or Pack and Go.
3. **All referenced files live inside this repo.** Save Toolbox fasteners and downloaded vendor parts as normal parts in `cad/parts/` rather than referencing `C:\SOLIDWORKS Data`.
4. **Drive the blade with global variables** (Tools → Equations: `thickness`, `c_radius`, `width`, `layer_count`, ...). Make variants as **configurations**, not copied files.
5. **Assign real materials.** Custom materials (e.g. our glass/epoxy layup) go in a `.sldmat` library in `cad/`.
6. **Don't commit Simulation results** (`*.cwr`, ignored automatically). Commit the study setup in the part, and put summary plots/numbers in `analysis/fea/`.
7. **Commit at meaningful points**, not every save. Every version of a binary file is stored forever.
8. **Export a STEP and a PDF drawing** to `cad/exports/` and `cad/drawings/` at each design review.
