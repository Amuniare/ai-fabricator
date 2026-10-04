# Commands

Run every command as `uv run fabricator <command>`. Add `--json` for the full structured
result. `uv run fabricator --help` lists everything; this page covers the ones Claude uses
beyond the everyday new → build → slice → package.

## Showing things

- `show "<project>" --part P03`: one piece alone. `--hide P01 --hide P02`: everything except those.
- `--exploded`: pieces pulled apart to show how it goes together.
- `--view front --view top` (also back, left, right, bottom, angled, print, plates).
- `--section x|y|z`: a cut through the middle to show the inside. `--transparent`: see-through.
- `view "<project>"`: a 3D view in the browser that the user can spin around.

Always look at a picture yourself before describing it.

## Pieces and splitting

- `parts "<project>"`: lists the pieces and how they join. Answers "what holds this piece?"
  and "what do I reprint if this breaks?".
- `compare "<project>" --what split`: slices different ways of splitting and recommends one.

## Changing sizes and recording facts

- `param "<project>" --set width=80`: change a parameter.
- `measure --what "remote width" --value 38.5`: a measurement the user took.
- `source "<project>" --what "battery width" --value 76.5 --url "<page>" --kind manufacturer --confidence high`:
  a number found online.

## Looking things up online

When a design depends on a real product, search for its dimensions. Prefer, in order: the
manufacturer's spec sheet or drawing, a datasheet, a published standard, then retailers,
then forums. Record every number with `source`. If sources disagree, say so and use the
more authoritative one, or ask the user to measure.

If a manufacturer offers a STEP model, bring it in as reference geometry (it is data only
and is never run):

```
uv run fabricator import "<downloaded file>" "<project>" --name drill --url "<page>" --author "<vendor>" --license "<terms>"
```

Use it in the design as `ctx.imported("drill")`, usually with `model.reference(...)` so the
checks confirm the printed parts don't hit it. Never download or run code files, and never
put downloaded files anywhere except the project's `imports/` folder through this command.

## After a print

- `feedback "<project>" --part P02 --result too-tight` (or `too-loose`, `good`; add
  `--fit snug` if known): adjusts that fit for the user's printer and material from now on.
  Offer to rebuild with the new gap.
- `feedback "<project>" --note "part 3 cracked when I tightened the screw"`: records it and
  returns suggestions to discuss.

## Versions

Each successful build is a version. The user never needs to know it's git.

- `versions "<project>"`: list them.
- `restore 3 "<project>"`: go back to version 3 (saved as a new version).
- `diff 4 5 "<project>"`: compare two.

## The tool itself

- `doctor`: what is installed and what is missing, with a plain note for each.
- `update`: get the latest version.
- `printers`, `library`: the printers and the bought-part sizes it knows.
