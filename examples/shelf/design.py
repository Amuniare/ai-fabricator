"""Shelf: a board with a front lip and a back rail that screws to the wall.

It is much wider than most printers, so the build splits it into pieces that peg
together. Nothing here needs to know that: design it whole.
"""

from fabricator.design import *

BACK = (Align.CENTER, Align.MAX, Align.MIN)  # wall at y = 0, front towards -Y


def build(p, ctx):
    model = Model("Shelf")

    # Round the two front corners of the board (the ones you bump into)
    board = Box(p.width, p.depth, p.thickness, align=BACK)
    front_corners = board.edges().filter_by(Axis.Z).group_by(Axis.Y)[0]
    board = fillet(front_corners, radius=p.corner_radius)

    lip = Pos(0, -(p.depth - p.lip_thickness), p.thickness) * Box(
        p.width - 2 * p.corner_radius, p.lip_thickness, p.lip_height, align=BACK)
    rail = Pos(0, 0, p.thickness) * Box(p.width, p.rail_thickness, p.rail_height, align=BACK)
    shelf = board + lip + rail

    # Wall screws through the rail, spread evenly, sunk in from the front of the rail
    n = int(p.screw_count)
    z = p.thickness + p.rail_height / 2
    for i in range(n):
        x = -p.width / 2 + p.width * (i + 0.5) / n
        shelf -= ctx.hw.wall_screw_hole(p.screw, at=(x, -p.rail_thickness, z), direction=(0, 1, 0),
                                        length=p.rail_thickness)

    model.add(shelf, "Shelf")
    model.hardware(f"{p.screw} wood screw, 40 mm", n)
    return model
