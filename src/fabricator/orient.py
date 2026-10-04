from dataclasses import dataclass
@dataclass
class Orientation:
    label: str = "as modelled"
    def to_dict(self): return {"label": self.label}
def choose(part, bed, ctx): return Orientation()
def place_on_bed(shape, choice):
    bb = shape.bounding_box()
    from build123d import Pos
    return Pos(-bb.center().X, -bb.center().Y, -bb.min.Z) * shape
