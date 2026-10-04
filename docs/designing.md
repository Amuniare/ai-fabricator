# Writing designs

Read this before writing or changing a project's `design.py`.

A design file defines `build(p, ctx)` and returns a `Model`. It is Python with
[build123d](https://build123d.readthedocs.io) for shapes. Keep it readable: short, named
steps, comments where a choice isn't obvious, and every key size taken from `p` (the
project's `parameters:` in project.yaml, with readable names like `battery_count`).

```python
from fabricator.design import *
from fabricator import joints


def build(p, ctx):
    model = Model("Remote bracket")

    # Back plate that screws to the wall
    plate = Box(p.width, p.thickness, p.height)
    plate = fillet(plate.edges().filter_by(Axis.Y), radius=p.corner_radius)
    for z in (-p.height / 4, p.height / 4):
        plate -= ctx.hw.wall_screw_hole("#8", at=(0, p.thickness / 2, z), direction=(0, -1, 0))

    # Cradle sized around the remote, with a sliding gap
    gap = ctx.fit("sliding")
    ...
    model.add(plate + cradle, "Bracket")
    return model
```

The `examples/` folder has four complete designs. Start from one when the request is close:
`uv run fabricator new "My shelf" -e shelf`.

## Directions

**-Y is the front** (what the "front" picture shows), +Y the back, +Z up, +X to the right.
For anything that mounts on a wall, put the wall at y = 0 and build out towards -Y, so the
pictures show it the way the user will see it.

## What `ctx` gives you

- `ctx.fit(name)`: gap per side in mm (see Fits below).
- `ctx.bed`: build volume (x, y, z) in mm. Also `ctx.nozzle`, `ctx.material`, `ctx.printer`.
- `ctx.min_wall`: the thinnest wall worth printing (2 nozzle widths). Make walls at least
  2–3 mm for anything that carries weight or takes screws.
- `ctx.hw`: holes for bought parts (below). `uv run fabricator library` lists sizes.
- `ctx.imported(name)`: a STEP/STL brought in with `fabricator import`.

## What `Model` gives you

- `model.add(shape, "Name")`: a printed part (ids P01, P02 … in order). Add
  `face_down="-Z"` when one side must be on the bed.
- `model.reference(shape, "Drill")`: shown and checked against, never printed.
- `model.join(a, b, kind, fit=..., hardware=[...])`: record a connection.
- `model.hardware("M3x12 screw", 4)`: bought parts for the parts list.
- `model.note("...")`: something to tell the user, kept with the build.

## Fits

Gaps between parts that fit together come from the user's settings, never from a guess.
Use `ctx.fit("snug")` and friends.

| Fit | Use for | Typical gap per side (PLA) |
| --- | --- | --- |
| `press` | pushed in firmly, stays without glue | 0.05 mm |
| `snug` | goes together by hand, holds, can come apart | 0.10 mm |
| `sliding` | lids, drawers, things that slide | 0.20 mm |
| `loose` | drops in with play, things that must move freely | 0.35 mm |
| `hole` | extra size for bought parts (screws, magnets) | 0.15 mm |

Translate the user's words: "snug but comes apart" → snug; "slides on" → sliding;
"easy to drop in" → loose.

## Joining separate parts

Use `from fabricator import joints`. Every helper takes the two shapes and returns new
ones with the joint cut in, plus what `model.join` needs:

```python
r = joints.peg(base, lid, at=[(-30, 0, 20), (30, 0, 20)], direction=(0, 0, 1), ctx=ctx)
a = model.add(r.a, "Base")
b = model.add(r.b, "Lid")
r.join(model, a, b)  # records the joint and its gap so the checks can measure it
```

`a` gets the sticking-out part (peg, tongue, hook, screw head), `b` the hole or slot. `at`
is a point (or list of points) on the face where they meet; `direction` points from `a`
into `b`. Always pass `ctx=ctx`.

- `joints.peg(a, b, at, direction, ctx=ctx, diameter=6, fit="snug")`: pegs on `a` into holes in `b`.
- `joints.dowel(..., pin="printed" | "steel")`: holes in both, joined by separate pins.
- `joints.screw(a, b, at, direction, ctx=ctx, size="M3", head="cap", nut="trap" | "insert")`:
  a screw through `a` into a nut pocket or heat-set insert in `b`; picks a stock length.
- `joints.dovetail(a, b, at, direction, slide, ctx=ctx, width=10, height=6)`: slides together along `slide`.
- `joints.tongue_groove(a, b, at, direction, along, ctx=ctx)`: a straight ridge into a groove.
- `joints.snap_hook(a, b, at, direction, hook, ctx=ctx, length=12)`: a bendy hook that clicks
  into a recess; its catch is sized so PLA bends without breaking.

## Holes for bought parts

`ctx.hw` functions each return a solid to subtract (`part -= ...`). `direction` points into
the part, from where the screw head or magnet goes in.

`screw_hole(size, length, head, at, direction)`, `nut_trap(size, at, direction)`,
`insert_hole(size, at, direction)`, `magnet_pocket(diameter, thickness, at, direction)`,
`bearing_seat(code, at, direction)`, `wall_screw_hole(size="#8", at, direction, length)`.

## Big objects

Anything bigger than the printer is split automatically into pieces joined by pegs.
Settings in project.yaml under `split:`:

- `joint: peg | dowel | screw | dovetail` and `fit: snug | press | sliding`.
- `seams: [{axis: x, at: 300}]` puts a cut at a chosen place (model coordinates), to hide
  it or keep a feature whole.

## Designing for printing

- Model parts where they sit in the finished object; the build decides how each lies on the bed.
- Prefer shapes that print without supports: flat bottoms, overhangs no steeper than 45°,
  chamfers instead of fillets on downward-facing edges.
- Round outside corners (2–5 mm) unless the user wants sharp ones.
- Holes for screws come from `ctx.hw`, never hand-picked diameters.
- Anything that holds an object needs the object's size plus a fit gap on each side.
