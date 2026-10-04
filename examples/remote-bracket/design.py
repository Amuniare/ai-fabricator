"""Remote bracket: a wall plate with a cradle the remote drops into.

The back of the plate sits on the wall (the XZ plane, y = 0); the cradle stands out
in front of it, towards -Y (the front, as in the "front" picture). Two countersunk screws above the cradle hold it up.
"""

from fabricator.design import *

FRONT = (Align.CENTER, Align.MAX, Align.MIN)  # centred side to side, from the wall forwards, from the floor up


def build(p, ctx):
    model = Model("Remote bracket")
    gap = ctx.fit("sliding")  # the remote should drop in and lift out easily

    # Wall plate, rounded on the corners you see from the front
    plate = Box(p.plate_width, p.plate_thickness, p.plate_height, align=FRONT)
    plate = fillet(plate.edges().filter_by(Axis.Y), radius=p.corner_radius)

    # Cradle: a box around the remote's footprint, open at the top
    inner_w = p.remote_width + 2 * gap
    inner_d = p.remote_thickness + 2 * gap
    outer = Pos(0, -p.plate_thickness, 0) * Box(
        inner_w + 2 * p.wall, inner_d + p.wall, p.hold_depth + p.wall, align=FRONT
    )
    pocket = Pos(0, -p.plate_thickness, p.wall) * Box(inner_w, inner_d, p.hold_depth + 1, align=FRONT)
    # A slot in the front so a finger can push the remote up and the buttons stay visible
    slot = Pos(0, -(p.plate_thickness + inner_d - 1), p.wall + 8) * Box(
        inner_w - 12, p.wall + 2, p.hold_depth, align=FRONT
    )
    cradle = outer - pocket - slot

    bracket = plate + cradle

    # Two wall screws above the cradle, sunk in from the front
    z = p.hold_depth + p.wall + (p.plate_height - p.hold_depth - p.wall) / 2
    for x in (-p.plate_width / 4, p.plate_width / 4):
        bracket -= ctx.hw.wall_screw_hole(
            p.screw, at=(x, -p.plate_thickness, z), direction=(0, 1, 0), length=p.plate_thickness
        )

    model.add(bracket, "Bracket")
    model.hardware(f"{p.screw} wood screw, 25 mm", 2)
    return model
