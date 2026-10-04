# Fabricator

Fabricator lets you describe an object in plain words and get a file you can print on a
Bambu Lab printer. You talk to Claude inside VS Code. Claude designs the object, Fabricator
checks it and draws pictures of it, and you look at the pictures and say what to change.
When you like it, you get a print-ready file to open in Bambu Studio and send to your printer.

The idea is **describe, look, print**. You never need to know CAD.

## What you need

- Windows 11
- A Bambu Lab printer (tested on the A1 mini)
- [Bambu Studio](https://bambulab.com) installed (free)
- A Claude subscription
- VS Code is optional to install yourself, because setup installs it for you

## Install

1. On this page, click **Code**, then **Download ZIP**, and unzip it somewhere you will
   keep it (for example your Documents folder). If you know git, you can clone it instead.
2. Open the folder and double-click **setup.bat**.
3. Wait for it to finish. It installs the other programs it needs (Git, VS Code, Python
   tools), makes a projects folder, and puts a **Fabricator** icon on your desktop.
   It is safe to run again if anything goes wrong.

## First use

1. Double-click **Fabricator** on your desktop. VS Code opens.
2. Click the Claude icon, sign in, and say what you want.

An example:

> **You:** I need a holder for my cordless drill batteries that mounts on the wall.
> **Claude:** I can do that. Which battery is it, and how many do you want to hold?
> **You:** Two of the 18 volt ones from the red drill.
> **Claude:** Here are pictures of a first design. It fits your printer in two pieces.
> **You:** Make the shelf deeper and round the corners.
> **Claude:** Done, here is version 2. All checks pass. It will take 5 hours 40 minutes and about 210 g of filament.

## What it does

- Draws pictures of the design from several angles so you can see it before printing
- Checks the design automatically for things that would fail to print
- Splits things bigger than your printer into pieces with joints that fit together
- Slices the parts and tells you the real print time and weight of filament
- Saves every version so you can go back
- Learns from your feedback: tell it a joint was "too tight" or "too loose" and it adjusts for your printer and filament
- Makes a finished folder with the parts, print files and a short guide

The `examples` folder has four finished designs to try or start from: a wall bracket for a
remote, a battery holder, a 600 mm shelf that comes in 4 pieces, and a drill-bit box with
a sliding lid. Ask Claude to "start from the shelf example", or run
`uv run fabricator new "My shelf" -e shelf`.

## Where your work is kept

In **Documents\Fabricator Projects**, one folder per object. This is separate from the
Fabricator program folder, so updating or replacing the program never touches your designs.

## Updating

Double-click **update.bat**. If you downloaded a ZIP, it will tell you to download the new
ZIP and replace the folder, then run setup.bat in it. Your projects are safe either way.

## Troubleshooting

Ask Claude to "run the Fabricator system check", or in a terminal in the folder run:

```
uv run fabricator doctor
```

It lists what is installed and what is missing, with a plain note for each. The most common
fix is to install Bambu Studio, open it once, and run setup.bat again.

## For developers

```
uv sync
uv run pytest
uv run fabricator --help
```

`fabricator --help` lists every command. How the pieces fit together is in
[docs/architecture.md](docs/architecture.md). Design files are ordinary build123d Python;
imported models (STEP, STL, 3MF, OBJ) are treated as data and never run.

## License

MIT. See [LICENSE](LICENSE).
