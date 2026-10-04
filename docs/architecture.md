# How Fabricator is put together

Claude is the conversation. This repository is the workshop: every real decision about
whether a design is right is made by code here, and Claude acts on the results.

## The flow

```
design.py ──► runner (own process) ──► split oversized parts ──► orient each piece
         ──► export STEP/STL ──► checks ──► pictures ──► save version
         ──► plates ──► slicer (Bambu Studio / OrcaSlicer) ──► print files + time and grams
```

## Modules and the contracts between them

All paths below are under `src/fabricator/`. Lengths are millimetres. Shapes are
build123d `Shape` objects (usually `Solid`). "Placed" means rotated so it lies on the bed
the way it will print, with its lowest point at z = 0, centred on x = y = 0.

| Module | Job | Public functions |
| --- | --- | --- |
| `settings.py` | Projects folder, printer, material, fit gaps | `home()`, `Settings.load()/save()/fits()/fit(name)`, `load_data(file)` |
| `project.py` | Project folders and saved versions (git inside each project) | `Project.find/create/all`, `.data`, `.save()`, `.save_version(label)`, `.versions()`, `.restore_version(n)` |
| `design.py` | What design files use | `Model`, `Part3D`, `Joint`, `Context`, `Params`, plus all of build123d |
| `runner.py` | Runs design.py in a separate process | `python -m fabricator.runner <project> <out> <bed>` |
| `build.py` | The pipeline above | `build(project, note, save, pictures) -> BuildResult`, `load_built(project)` |
| `split.py` | Splits parts bigger than the bed into joined pieces | `split_oversized(model, ctx, options) -> (model, messages)` |
| `joints.py` | Makes joint geometry (pegs, dowels, screws, dovetails, tongue-and-groove, snap fits) | see module docstring |
| `library.py` | Bought hardware: screws, nuts, inserts, magnets, bearings | `Library(ctx)` (as `ctx.hw`), `describe(item) -> (text, data)` |
| `orient.py` | Chooses how each piece lies on the bed | `choose(part, bed, ctx) -> Orientation`, `place_on_bed(shape, orientation) -> Shape`, `candidates(part, bed, ctx) -> list[Orientation]` |
| `checks/geometry.py` | Closed solid, one body, fits the bed, wall thickness, small details | `check_part(part, placed, mesh, ctx, choice=None) -> list[Check]` |
| `checks/printability.py` | Overhangs, bridges, bed contact, tall-and-thin, layer direction | `check_part(part, placed, mesh, ctx, choice) -> list[Check]` |
| `checks/assembly.py` | Overlaps, joint gaps, screwdriver access, every piece fits | `check_model(model, ctx) -> list[Check]` |
| `render.py` | Pictures (PNG) | `build_pictures(model, out_dir, ctx) -> list[Path]`, `pictures(project, parts, exploded, views, hide, transparent, section) -> list[Path]` |
| `plates.py` | Groups pieces onto as few print plates as possible | `plan(pieces, bed, spacing=6) -> list[list[str]]` |
| `slicer/` | Finds the slicer, merges profiles, slices, reads time and grams | `printer_bed(settings)`, `list_printers(settings)`, `match_printer(name, settings)`, `find_slicer(settings)`, `slice_project(project, settings, parts=None) -> dict` |
| `compare.py` | Slices several options and recommends one | `compare(project, settings, part, what) -> dict` |
| `sources.py` | Records where outside numbers and files came from; imports STEP/STL as data | `add_source(...)`, `import_file(...)`, `load_import(project_dir, name)` |
| `feedback.py` | Turns "the pins were too tight" into better fit gaps | `record(project, settings, fit, result, part, note) -> dict` |
| `package.py` | The finished folder: parts, print files, guide | `make(project, settings, slice_plates=True) -> Path` |
| `viewer.py` | A 3D view in the browser | `make(project, open_browser=True) -> Path` |
| `doctor.py` | Checks the install; updates the tool | `run() -> list[(name, status, detail)]`, `update() -> str` |
| `cli.py` | The `fabricator` command | `main()` |

## Check results

Every check returns `Check(name, status, message, part, value, limit, where, fix)` from
`checks/__init__.py`. `status` is `pass`, `warn` (printable but worth mentioning) or
`fail` (must be fixed before printing). `message` is one plain sentence with the numbers
in it. `fix` says what to change, in plain words. A build with any `fail` is not saved
as a version and is not offered for printing.

## Rules every module follows

- **Plain messages.** Anything the user might see is a plain sentence with numbers and
  units. No stack traces, no jargon.
- **Code decides, not guesses.** If a module says something fits or prints, it measured it.
- **No wasted plastic.** Nothing asks the user to print a test piece.
- **Outside files are data.** Imported STEP and STL files are read as shapes and never run.
- **Nothing personal in the repository.** Settings, projects and printer details live in
  the user's projects folder.
- **Windows first.** Paths use `pathlib`, files are read and written as UTF-8, and
  external programs are called with argument lists, never shell strings.

## Slicing details worth knowing

Bambu Studio's printer, process and filament profiles inherit from parent profiles.
The command line does **not** fill in inherited settings when given a profile file, so
`slicer/` merges each profile with its parents before slicing. Without that, a 20 mm
cube on an A1 Mini is estimated at 29 minutes and 0 g; with it, 12 minutes and 3.7 g,
which matches the Bambu Studio app.

Bambu Studio writes `result.json` in the output folder (per-plate time in
`sliced_plates[].total_predication` seconds, grams in `filaments[].total_used_g`) and a
`.gcode.3mf` the user opens in Bambu Studio and prints.
