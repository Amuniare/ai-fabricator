"""Stand-in until the real packer lands: one piece per plate."""
def plan(pieces, bed, spacing=6.0):
    return [[p["id"]] for p in pieces]
