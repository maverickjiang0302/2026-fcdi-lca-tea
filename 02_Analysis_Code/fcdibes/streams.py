"""Process streams: a flowrate plus whatever else a unit wants to attach."""

from __future__ import annotations


class Stream:
    """A flow between unit operations.

    Deliberately loose: units attach the properties they need
    (concentrations, density, solids, selenium content) as attributes, so a
    stream carries only what the units connected to it actually set.
    """

    def __init__(self, name: str, flowrate_m3hr: float | None = None,
                 descriptor: str = "", **properties):
        self.name = name
        self.descriptor = descriptor
        self.flowrate_m3hr = flowrate_m3hr
        for key, value in properties.items():
            setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __repr__(self) -> str:
        if self.flowrate_m3hr is not None:
            flow = format(self.flowrate_m3hr, ".4g") + " m3/hr"
        elif getattr(self, "mass_flowrate_kghr", None) is not None:
            flow = format(self.mass_flowrate_kghr, ".4g") + " kg/hr"
        else:
            flow = "unset"
        return "Stream(" + repr(self.name) + ", " + flow + ")"
