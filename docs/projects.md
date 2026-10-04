# Your projects

Every object you make is its own project: a folder in **Documents\Fabricator Projects**
(or wherever `FABRICATOR_HOME` points). Projects live outside the Fabricator program
folder, so updating or replacing the program never touches them.

```
Fabricator Projects/
    My notes.md              your notes for every project (Claude reads this first)
    settings.yaml            your printer, material and the fit gaps learned from your prints
    garage-remote-bracket/   one folder per object
    battery-holder/
```

## Inside a project folder

| File or folder | What it is |
| --- | --- |
| `README.md` | A page about the object: pictures, pieces, print times, sizes you can change, versions. Remade after every build and slice. |
| `notes.md` | Your own notes about this object. Claude reads it and adds to it when you ask. |
| `build/pictures/` | The pictures of the latest build (`overview.png`, plus `exploded.png` and `print.png` for objects in pieces). |
| `slices/` | Print files (`plate_1.gcode.3mf` …). Double-click one to open it in Bambu Studio. |
| `package/` | The finished folder from `fabricator package`: parts, print files and an assembly guide. |
| `project.yaml` | What you asked for, the sizes you can change, measurements, and fit feedback. |
| `design.py` | The design itself, written by Claude. |
| `sources.yaml`, `imports/` | Where outside measurements and downloaded models came from. |

## Seeing a project

The quickest way to see a project is to open its `README.md` in VS Code and press
**Ctrl+Shift+V** (Open Preview). That shows the pictures, the pieces and the print time
together. The pictures themselves are in `build/pictures/`. You can also ask Claude to
"show me the shelf" or "show me piece 3".

## Notes

There are two places for notes, both plain text you can edit yourself:

- **My notes.md** in the projects folder is for things that apply to everything: screws
  you have on hand, colours you like, how your printer behaves.
- **notes.md** in a project folder is for that object: why you made it, what you changed,
  how the print came out.

Saying "remember that ..." to Claude adds a line to the right one.

## Versions

Every build that passes its checks is saved as a version, so you can always go back. Ask
Claude "what versions are there?" or "go back to version 2". Only the design and its
settings are saved; pictures and print files are remade from them.

## Backing up and sharing

Copy the project's folder. Everything needed to rebuild the object is in it. To give
someone just the printable result, share the `package/` folder after running
`fabricator package`.
