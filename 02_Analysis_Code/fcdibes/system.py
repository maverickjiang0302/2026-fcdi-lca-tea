"""The flowsheet: build the units, solve them in dependency order.

Two configurations:

  include_recovery=True   reactor + cultivation + mixer + P1/P2
                          + GAC screen + centrifuge + P3/P4/P5
  include_recovery=False  reactor + cultivation + mixer + P1/P2 only,
                          with the slurry recirculated directly

Note on the recycle: the reactor sizes its own recirculation stream from the
anti-clogging and selenium-production bounds, so the loop is closed in a single
pass rather than iterated to convergence. Assigning the mixer outlet back onto
the reactor inlet at the end is bookkeeping for the stream table; it does not
feed back into the balance.
"""

from __future__ import annotations

import pandas as pd

from .params import Parameters
from .streams import Stream
from .unitops import (Centrifuge, CentrifugePump, CultivationReactor, FCDIBESReactor,
                      Pump, RotaryScreen, SlurryMixer)


class System:
    """A complete FCDI-BES treatment train."""

    def __init__(self, p: Parameters, include_recovery: bool = True):
        self.p = p
        self.include_recovery = include_recovery

        self.reactor = FCDIBESReactor(p)
        self.Q5 = Stream("slurry_mixer_inflow", descriptor="Mixer inflow (Q4+Q6)")
        self.mixer = SlurryMixer(Q5=self.Q5)
        self.cultiv: CultivationReactor | None = None   # needs the reactor GAC mass

        if include_recovery:
            self.screen = RotaryScreen(Q2=self.reactor.Q2)
            self.centrifuge = Centrifuge(Q3=self.screen.Q3)
        else:
            self.screen = None
            self.centrifuge = None

        self.P1 = Pump(self.reactor.Qin, "P1 - Influent pump", "Influent Head", "P1")
        self.P2 = Pump(self.mixer.Q1, "P2 - Recirculation pump", "Recirc Head", "P2")
        if include_recovery:
            self.P3 = Pump(self.reactor.Q2, "P3 - Slurry to screen",
                           "Screen Input Head", "P3")
            self.P4 = CentrifugePump(self.screen.Q3, "P4 - Se-water to centrifuge", "P4")
            self.P5 = Pump(self.Q5, "P5 - Mixer inflow", "Mixer Input Head", "P5")
        else:
            self.P3 = self.P4 = self.P5 = None

        self.results: dict = {}
        self.power_kW = 0.0
        self._solved = False

    # -- stream aliases ----------------------------------------------------
    Qin = property(lambda self: self.reactor.Qin)
    Qout = property(lambda self: self.reactor.Qout)
    Q1 = property(lambda self: self.reactor.Q1)
    Q2 = property(lambda self: self.reactor.Q2)
    Q3 = property(lambda self: self.screen.Q3 if self.screen else None)
    Q4 = property(lambda self: self.centrifuge.Q4 if self.centrifuge else None)
    Q6 = property(lambda self: self.screen.Q6 if self.screen else None)
    Q7 = property(lambda self: self.centrifuge.Q7 if self.centrifuge else None)

    @property
    def units(self) -> list:
        candidates = [self.reactor, self.cultiv, self.mixer, self.P1, self.P2]
        if self.include_recovery:
            candidates += [self.screen, self.centrifuge, self.P3, self.P4, self.P5]
        return [u for u in candidates if u is not None]

    @property
    def se_recovered_kghr(self) -> float:
        return self.Q7.selenium_kghr if self.centrifuge else 0.0

    # -- solve -------------------------------------------------------------
    def solve(self) -> "System":
        p = self.p

        self.reactor.solve(p)

        self.cultiv = CultivationReactor(self.reactor.results["GAC mass in reactor, kg"])
        self.cultiv.solve(p)

        if self.include_recovery:
            self.screen.solve(p)
            self.centrifuge.solve(p)
            self.Q5.flowrate_m3hr = (self.centrifuge.Q4.flowrate_m3hr
                                     + self.screen.Q6.flowrate_m3hr)
        else:
            self.Q5.flowrate_m3hr = self.reactor.Q2.flowrate_m3hr

        self.Q5.density_kg_m3 = self.reactor.gac_slurry_density_kg_m3
        self.Q5.selenate_mgL = self.reactor.Q2.selenate_mgL
        self.Q5.selenite_mgL = self.reactor.Q2.selenite_mgL

        self.mixer.solve(p)
        self.reactor.Q1.flowrate_m3hr = self.mixer.Q1.flowrate_m3hr

        if self.include_recovery:
            # Q4 carries the dissolved oxyanions concentrated by the volume split.
            split = self.Q5.flowrate_m3hr / self.centrifuge.Q4.flowrate_m3hr
            self.centrifuge.Q4.selenate_mgL = self.Q5.selenate_mgL * split
            self.centrifuge.Q4.selenite_mgL = self.Q5.selenite_mgL * split

        for pump in (self.P1, self.P2, self.P3, self.P4, self.P5):
            if pump is not None:
                pump.solve(p)

        self.results = {
            "[" + unit.name + "] " + key: value
            for unit in self.units
            for key, value in unit.results.items()
        }
        self.power_kW = sum(u.power_kW for u in self.units)
        self._solved = True
        return self

    # -- reporting ---------------------------------------------------------
    def stream_table(self) -> pd.DataFrame:
        named = [("Qin", self.Qin), ("Qout", self.Qout), ("Q1", self.Q1),
                 ("Q2", self.Q2), ("Q5", self.Q5)]
        if self.include_recovery:
            named += [("Q3", self.Q3), ("Q4", self.Q4),
                      ("Q6", self.Q6), ("Q7", self.Q7)]

        rows = []
        for label, s in named:
            if s is None:
                continue
            if s.flowrate_m3hr is not None:
                flow, unit = s.flowrate_m3hr, "m3/hr"
            elif s.get("mass_flowrate_kghr") is not None:
                flow, unit = s.mass_flowrate_kghr, "kg/hr"
            else:
                flow, unit = float("nan"), ""
            rows.append({
                "stream": label,
                "description": s.descriptor,
                "flow": flow,
                "flow_unit": unit,
                "selenate_mgL": s.get("selenate_mgL"),
                "selenite_mgL": s.get("selenite_mgL"),
                "selenium_kghr": s.get("selenium_kghr"),
                "density_kg_m3": s.get("density_kg_m3"),
            })
        return pd.DataFrame(rows).set_index("stream")

    def power_table(self) -> pd.DataFrame:
        rows = [{"unit": u.name, "power_kW": u.power_kW} for u in self.units]
        frame = pd.DataFrame(rows).set_index("unit")
        frame["share_pct"] = 100 * frame["power_kW"] / frame["power_kW"].sum()
        return frame.sort_values("power_kW", ascending=False)

    def __repr__(self) -> str:
        tag = "with recovery" if self.include_recovery else "without recovery"
        state = f"{self.power_kW:.4g} kW" if self._solved else "unsolved"
        return "System(" + tag + ", " + state + ")"
