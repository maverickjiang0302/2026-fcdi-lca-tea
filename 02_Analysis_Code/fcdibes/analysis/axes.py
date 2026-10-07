"""Operating-parameter axes for the scale sweeps.

An axis is a named way of turning a scalar into a Parameters override, plus
the label a figure should use for it.

The most useful axis is the areal selenium-reduction rate. Membrane area, and
therefore most of the capital cost, follows

    A_membrane  proportional to  RE / (FE * J)

so for cost, current density and Faradaic efficiency are not independent
levers: only their product matters. Collapsing them onto one axis makes the
improvement target a single number rather than a two-dimensional region.

That collapse is exact for cost and only approximate for life-cycle burden.
Current demand is

    I  proportional to  RE / FE

with no dependence on current density at all, so raising FE cuts operating
electricity as well as area while raising J cuts area alone. Doubling FE and
doubling J give identical membrane area and levelized cost, but the FE route
gives roughly 20% lower global warming potential. This module realises a target
rate by scaling current density at fixed FE, which is the conservative choice:
impact surfaces built on it understate what a Faradaic-efficiency improvement
would deliver, and never overstate it.
(SI Eq. S54-S55)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..params import Parameters

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


@dataclass(frozen=True)
class Axis:
    name: str
    label: str
    build: Callable[[float], dict]
    base: Callable[[Parameters], float]
    log_scale: bool = False


def _nested(section: str, group: str, parameter: str) -> Callable[[float], dict]:
    def build(value: float) -> dict:
        return {section: {group: {parameter: value}}}
    return build


def areal_se_rate_kg_m2_yr(p: Parameters, n_electrons: float = 4.85) -> float:
    """Selenium reduced per square metre of membrane per year at scale (SI Eq. S54, S67).

    r = J_eff * FE / (n * F)  in mol Se m-2 s-1, converted to kg Se m-2 yr-1, with J_eff = retention x J the
    current density the stack retains after the scale-up derating (ProcessAssumptions group 'Scale-up'; Phase 3).
    With retention 1.0 this is exactly the laboratory rate J * FE / (n F) (J * 1.0 == J).

    n is the weighted average electron count for selenium reduction measured
    on the electron-partitioning sheet (n_eff, about 4.85 across conditions).
    """
    fcdi = p.PROC["FCDI-BES"]
    faraday = p.UC["Faraday constant"]
    molar_mass_g = p.UC["Se"]
    j_eff = fcdi["Current Density"] * p.PROC["Scale-up"]["Current density retention"]
    mol_per_m2_s = (j_eff * fcdi["Faradaic Efficiency"]
                    / (n_electrons * faraday))
    return mol_per_m2_s * (molar_mass_g / 1000.0) * SECONDS_PER_YEAR


def _areal_rate_build(p: Parameters) -> Callable[[float], dict]:
    """Realise a target (effective, at-scale) areal rate by scaling the LABORATORY current density at fixed FE and
    fixed retention: the rate is linear in J, so J_lab * target / base_rate retains exactly the target rate.

    Any (J, FE) pair with the same product gives the same membrane area, so
    scaling one of them is sufficient for cost and keeps the mapping
    single-valued. Current density is the one scaled because it is unbounded,
    whereas Faradaic efficiency cannot exceed 1.0 -- from the base 27.96% that
    caps the FE route at 3.58x, well short of the improvement the cost
    break-evens require. See the module docstring for what this choice costs
    on the impact side.
    """
    base_rate = areal_se_rate_kg_m2_yr(p)
    base_j = p.PROC["FCDI-BES"]["Current Density"]

    def build(target: float) -> dict:
        return {"PROC": {"FCDI-BES": {"Current Density": base_j * target / base_rate}}}

    return build


def registry(p: Parameters) -> dict:
    """Axes available to the grid sweep, bound to a reference parameter set."""
    return {
        "areal_se_rate": Axis(
            name="areal_se_rate",
            label="Areal Se reduction rate at scale (kg Se m$^{-2}$ yr$^{-1}$)",
            build=_areal_rate_build(p),
            base=areal_se_rate_kg_m2_yr,
            log_scale=True,
        ),
        "current_density": Axis(
            name="current_density",
            label="Current density (A m$^{-2}$)",
            build=_nested("PROC", "FCDI-BES", "Current Density"),
            base=lambda q: q.PROC["FCDI-BES"]["Current Density"],
            log_scale=True,
        ),
        "faradaic_efficiency": Axis(
            name="faradaic_efficiency",
            label="Faradaic efficiency (-)",
            build=_nested("PROC", "FCDI-BES", "Faradaic Efficiency"),
            base=lambda q: q.PROC["FCDI-BES"]["Faradaic Efficiency"],
        ),
        "removal_efficiency": Axis(
            name="removal_efficiency",
            label="Removal efficiency (-)",
            build=_nested("PROC", "FCDI-BES", "Removal Efficiency"),
            base=lambda q: q.PROC["FCDI-BES"]["Removal Efficiency"],
        ),
        "membrane_price": Axis(
            name="membrane_price",
            label="Membrane price (USD m$^{-2}$)",
            build=_nested("MAT", "Membranes", "Cost"),
            base=lambda q: q.MAT["Membranes"]["Cost"],
            log_scale=True,
        ),
        "electrode_price": Axis(
            name="electrode_price",
            label="Electrode price (USD kg$^{-1}$)",
            build=_nested("MAT", "Graphite Electrodes", "Cost"),
            base=lambda q: q.MAT["Graphite Electrodes"]["Cost"],
            log_scale=True,
        ),
    }
