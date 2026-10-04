"""Battery holder: a row of open-top pockets on a wall plate.

Each battery drops into a pocket sized to its footprint plus a loose gap. The back
plate rises above the pockets and takes the wall screws.
"""

from fabricator.design import *

FRONT = (Align.CENTER, Align.MAX, Align.MIN)  # wall at y = 0, front towards -Y


def build(p, ctx):
    model = Model("Battery holder")
    gap = ctx.fit("loose")  # batteries should drop in and lift out without fuss

    pocket_w = p.battery_width + 2 * gap
    pocket_d = p.battery_depth + 2 * gap
    n = int(p.battery_count)
    total_w = n * pocket_w + (n - 1) * p.spacing + 2 * p.wall

    tray = Pos(0, -p.plate_thickness, 0) * Box(total_w, pocket_d + p.wall, p.hold_depth + p.wall, align=FRONT)
    # Round the tray's two front corners before the pockets go in (no thinner than the wall)
    tray = fillet(tray.edges().filter_by(Axis.Z).group_by(Axis.Y)[0], radius=min(p.corner_radius, p.wall))
    back = Box(total_w, p.plate_thickness, p.hold_depth + p.wall + p.back_height, align=FRONT)
    holder = back + tray

    first_x = -total_w / 2 + p.wall + pocket_w / 2
    for i in range(n):
        x = first_x + i * (pocket_w + p.spacing)
        holder -= Pos(x, -p.plate_thickness, p.wall) * Box(pocket_w, pocket_d, p.hold_depth + 1, align=FRONT)

    # Wall screws in the back plate, one above each gap between pockets and one at each end
    z = p.hold_depth + p.wall + p.back_height / 2
    xs = [first_x - pocket_w / 2 + 10, -first_x + pocket_w / 2 - 10]
    for x in xs:
        holder -= ctx.hw.wall_screw_hole(p.screw, at=(x, -p.plate_thickness, z), direction=(0, 1, 0),
                                         length=p.plate_thickness)

    model.add(holder, "Holder")
    model.hardware(f"{p.screw} wood screw, 30 mm", len(xs))
    return model
