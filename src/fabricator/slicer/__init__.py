from ..settings import load_data
def printer_bed(settings):
    return tuple(float(v) for v in load_data("printers.yaml")["printers"][settings.printer]["bed"])
