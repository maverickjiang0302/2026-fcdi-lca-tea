"""Life-cycle assessment: foreground processes and system characterisation.

Functional unit: 1 m3 of FGD wastewater treated.
Method: TRACI 2.1, cradle to gate. Equations SI Eq. S48-S53.

Three materials are modelled as foreground processes rather than taken
straight from the background database, because no single ecoinvent activity
represents them: the ion-exchange membrane, sodium acetate, and the
biofilm-coated GAC produced on site. BioGAC in turn consumes the membrane
foreground process, so the two are computed in dependency order.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import IMPACT_CATEGORIES, normalization_key
from .inventory import Inventory
from .params import Parameters
from .system import System


class ForegroundProcess:
    """A material whose characterisation factors are built from an inventory.

    CF_process = sum over inputs of (quantity x CF_input)   (SI Eq. S48)

    Parameters
    ----------
    name        label, also the key other processes use to reference it
    inventory   {flow: (quantity, unit)} per functional unit of output
    """

    def __init__(self, name: str, inventory: dict, functional_unit: float = 1.0):
        self.name = name
        self.inventory = inventory
        self.functional_unit = functional_unit
        self.cf: dict = {}
        self._computed = False

    def compute(self, background: dict, extra: dict | None = None,
                categories: list | None = None) -> dict:
        categories = categories or IMPACT_CATEGORIES
        factors = dict(background)

        for name, process in (extra or {}).items():
            if not process._computed:
                raise RuntimeError(
                    "foreground process " + repr(process.name)
                    + " must be computed before " + repr(self.name) + " can use it")
            factors[name] = process.cf

        self.cf = {c: 0.0 for c in categories}
        missing = []
        for flow, (quantity, _unit) in self.inventory.items():
            if flow not in factors:
                missing.append(flow)
                continue
            for category in categories:
                self.cf[category] += quantity * factors[flow].get(category, 0.0)

        if missing:
            raise KeyError(
                "flows absent from the characterisation tables for "
                + repr(self.name) + ": " + repr(missing))

        if self.functional_unit != 1.0:
            self.cf = {c: v / self.functional_unit for c, v in self.cf.items()}

        self._computed = True
        return self.cf


@dataclass
class LCAResult:
    contributions: pd.DataFrame          # one row per inventory entry
    impacts: dict                        # absolute, per hour of operation
    impacts_per_m3: dict
    impacts_normalized_per_m3: dict      # cap.yr per m3
    foreground: dict                     # name -> ForegroundProcess
    unavailable_categories: list         # categories the LCI cannot support

    def summary_table(self) -> pd.DataFrame:
        rows = [{
            "impact_category": c,
            "per_m3": self.impacts_per_m3[c],
            "normalized_per_m3_cap_yr": self.impacts_normalized_per_m3.get(c),
        } for c in IMPACT_CATEGORIES]
        return pd.DataFrame(rows)


def _biogac_inventory(system: System, inv: Inventory) -> dict:
    """Cultivation inputs expressed per kg of biofilm-coated GAC produced."""
    cv = system.cultiv
    per_kg = cv.BioGAC.mass_flowrate_kghr
    f = inv.flows

    def rate(value: float) -> float:
        return value / per_kg

    return {
        "granular activated carbon": (rate(cv.GAC_feed.mass_flowrate_kghr), "kg"),
        "graphite electrode": (rate(f["Cult. electrode mass, kg/hr"]), "kg"),
        "polypropylene": (rate(f["Cult. PP shell mass, kg/hr"]), "kg"),
        "ultrapure water": (rate(cv.chemicals_kghr["DI water"]), "kg"),
        "sodium dihydrogen phosphate": (rate(cv.chemicals_kghr["Na2HPO4"]), "kg"),
        "sodium bicarbonate": (rate(cv.chemicals_kghr["NaHCO3"]), "kg"),
        "phosphoric acid": (rate(cv.chemicals_kghr["H3PO4"]), "kg"),
        "wastewater": (rate(cv.WW_cult.flowrate_m3hr), "m3"),
        "membrane": (rate(f["Cult. membrane mass, kg/hr"]), "kg"),
    }


def _system_inventory(system: System, inv: Inventory) -> list:
    """(label, quantity, unit, characterisation key, sign) per hour of operation."""
    f = inv.flows
    entries = [
        ("Graphite electrode - FCDI-BES", f["FCDI-BES electrode mass, kg/hr"], "kg",
         "graphite electrode", +1),
        ("Membrane - FCDI-BES", f["FCDI-BES membrane mass, kg/hr"], "kg",
         "membrane", +1),
        ("PMMA housing - FCDI-BES", f["FCDI-BES glass mass, kg/hr"], "kg",
         "polymethyl methacrylate", +1),
        ("BioGAC - FCDI-BES", f["Cult. BioGAC, kg/hr"], "kg", "BioGAC", +1),
        ("Sodium acetate - Slurry Mixer", f["Slurry Mixer CH3COONa, kg/hr"], "kg",
         "sodium acetate", +1),
        ("Sodium sulfate - Slurry Mixer", f["Slurry Mixer Na2SO4, kg/hr"], "kg",
         "sodium sulfate", +1),
    ]

    for unit in system.units:
        if unit.power_kW:
            entries.append(("Electricity - " + unit.name, unit.power_kW, "kWh",
                            "electricity", +1))

    se_kghr = system.se_recovered_kghr
    if system.include_recovery and se_kghr > 0:
        # Avoided burden from displacing primary selenium production.
        # NOTE: the original model scaled this one entry by the operating
        # factor while every other entry stayed on a raw per-operating-hour
        # basis. That asymmetry understated the credit by about 10%; all
        # entries are on the same basis here.
        entries.append(("Selenium credit - Centrifuge", se_kghr, "kg", "selenium", -1))

    return entries


def solve(system: System, inv: Inventory, p: Parameters) -> LCAResult:
    background = p.LCA_FACTORS

    # Some recovered LCI datasets are missing individual categories (see the
    # note in the superseded D01 factor-writer script). Those categories are reported as
    # None rather than zero, which would silently understate them, and every
    # other category is still characterised normally.
    gaps = getattr(p, "LCA_GAPS", {})
    unavailable = {c for cats in gaps.values() for c in cats}
    categories = [c for c in IMPACT_CATEGORIES if c not in unavailable]

    membrane = ForegroundProcess("membrane", p.FG_RAW["Membrane"])
    membrane.compute(background, categories=categories)

    sodium_acetate = ForegroundProcess("sodium acetate", p.FG_RAW["Sodium Acetate"])
    sodium_acetate.compute(background, categories=categories)

    biogac = ForegroundProcess("BioGAC", _biogac_inventory(system, inv))
    biogac.compute(background, extra={"membrane": membrane}, categories=categories)

    factors = dict(background)
    factors["membrane"] = membrane.cf
    factors["sodium acetate"] = sodium_acetate.cf
    factors["BioGAC"] = biogac.cf

    # System impact = sum over inventory of (sign x quantity x CF)  (SI Eq. S50)
    impacts = {c: 0.0 for c in categories}
    rows = []
    for label, quantity, unit, key, sign in _system_inventory(system, inv):
        if key not in factors:
            raise KeyError("no characterisation factors for " + repr(key))
        row = {"entry": label, "quantity": quantity, "unit": unit, "sign": sign}
        for category in categories:
            contribution = sign * quantity * factors[key].get(category, 0.0)
            impacts[category] += contribution
            row[category] = contribution
        rows.append(row)

    flow_m3hr = system.Qin.flowrate_m3hr
    per_m3 = {c: v / flow_m3hr for c, v in impacts.items()}         # SI Eq. S51
    normalized = {                                                   # SI Eq. S52
        c: per_m3[c] / p.NORM[normalization_key(c)]
        for c in categories
        if normalization_key(c) in p.NORM
    }
    # Categories the selected datasets cannot support are explicitly None.
    for category in unavailable:
        impacts[category] = None
        per_m3[category] = None
        normalized[category] = None

    return LCAResult(
        contributions=pd.DataFrame(rows).set_index("entry"),
        impacts=impacts,
        impacts_per_m3=per_m3,
        impacts_normalized_per_m3=normalized,
        foreground={"membrane": membrane, "sodium acetate": sodium_acetate,
                    "BioGAC": biogac},
        unavailable_categories=sorted(unavailable),
    )
