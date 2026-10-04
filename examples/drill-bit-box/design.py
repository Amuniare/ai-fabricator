"""Drill bit box with a sliding lid.

The lid slides in along the length of the box, in grooves cut into the top of the two
long walls. The end it slides in from is lowered so the lid can pass.
"""

from fabricator.design import *

BASE = (Align.CENTER, Align.CENTER, Align.MIN)


def span_x(x0, x1, width, height, z):
    """A box running from x0 to x1, centred side to side, starting at height z."""
    return Pos((x0 + x1) / 2, 0, z) * Box(x1 - x0, width, height, align=BASE)


def build(p, ctx):
    model = Model("Drill bit box")
    gap = ctx.fit("sliding")

    L, W = p.inside_length, p.inside_width
    lid_z = p.floor + p.inside_height  # underside of the lid
    height = lid_z + p.lid_thickness + 2 * gap + 1.2  # 1.2 mm of wall above the groove

    box = Box(L + 2 * p.wall, W + 2 * p.wall, height, align=BASE)
    box -= Pos(0, 0, p.floor) * Box(L, W, height, align=BASE)
    # Grooves in the long walls and the closed end (-X), running out through the open end (+X)
    groove_w = W + 2 * p.groove_depth
    groove_start = -L / 2 - p.groove_depth
    box -= span_x(groove_start, L / 2 + p.wall + 1, groove_w, p.lid_thickness + 2 * gap, lid_z - gap)
    # Lower the end wall at +X so the lid slides in over it
    box -= Pos(L / 2 + p.wall / 2, 0, lid_z - gap) * Box(p.wall + 2, W, height, align=BASE)

    # Lid: fills the groove less the gap on every side, with a finger notch at the open end
    lid_w = groove_w - 2 * gap
    lid = span_x(groove_start + gap, L / 2 + p.wall, lid_w, p.lid_thickness, lid_z)
    notch = Pos(L / 2 + p.wall / 2 - 6, 0, lid_z + p.lid_thickness - 0.8) * Box(4, 20, 2, align=BASE)
    lid -= notch

    b = model.add(box, "Box")
    l = model.add(lid, "Lid", face_down="-Z")
    model.join(l, b, "slide", fit="sliding", axis=(1, 0, 0),
               note="the lid slides in from the open end along the grooves")
    return model
