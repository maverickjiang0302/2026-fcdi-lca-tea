"""Turn a break-even areal rate into the current density it actually demands.

Section 3 establishes that levelized cost and life-cycle burden are governed by
one quantity, the areal selenium-reduction rate

    r_Se = J * FE * M_Se / (n_eff * F)                          (SI Eq. S54)

so a break-even against a benchmark can be stated as a single required value
r*. That is the right way to *find* the target, but it is not directly
actionable in a laboratory: nobody sets an areal rate, they set a cell voltage
and measure a current density and a Faradaic efficiency.

This module inverts the relation,

    J = r * n_eff * F / (FE * M_Se)                             (SI Eq. S57)

and reports the current density each break-even demands at a stated Faradaic
efficiency. Since Phase 3 the areal rate is the rate RETAINED at scale, r = J_eff FE M / (n F) with
J_eff = retention x J (SI Eq. S67), and the current densities are reported on the LABORATORY basis,

    J_lab* = r* n_eff F / (FE M_Se retention)

i.e. what the laboratory must demonstrate so that the derated stack meets the target; the retention applied is a
column of the table (current_density_retention_applied). With retention 1.0 both reduce exactly to Eq. S57. Because r depends only on the product J * FE, the two are exactly
exchangeable: every factor of two gained in FE halves the current density
required, and vice versa. Quoting J at FE = 100%, 75% and 50% therefore brackets
the whole design space with three numbers, and makes the thermodynamic floor
explicit -- the FE = 100% column is the least current density that can ever
deliver that rate.

A target can fail to produce a number for two opposite reasons, and the
difference matters, so it is reported rather than collapsed into a blank:

    met_at_base                 the base case already satisfies it; no improvement needed
    reachable                   a swept areal rate satisfies it, and is reported
    unreachable                 (impact targets) no swept areal rate satisfies it, and the response
                                has levelled off within the sweep, so the threshold lies below a
                                floor that reactor performance alone cannot cross (column
                                achievable_floor; SI Eq. S59)
    not_reached_in_swept_range  (cost targets) no swept areal rate satisfies it, but the response is
                                still falling at the edge of the sweep: the lowest swept value is a
                                limit of the sweep, not of the process (DESIGN_phase3 addendum A2;
                                fix round 2026-10-06)

The third case is the informative one for life-cycle impacts. Raising the areal
rate shrinks membrane and electrode area, so it removes the *material* burden,
but it does not touch pumping, mixing or cultivation energy; the impact responses
level off within the swept range, so their lowest swept value is close to the
floor that reactor performance alone cannot cross. For levelized cost at full
scale the response is still falling at the edge of the sweep, so a cost target
below the lowest swept value is labelled not_reached_in_swept_range instead (the
lowest swept value is a limit of the sweep, not an asymptote; DESIGN_phase3
addendum A2). Target.levels_off tells the two apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..params import Parameters
from .axes import SECONDS_PER_YEAR, areal_se_rate_kg_m2_yr
from .grid import break_even

#: Faradaic efficiencies at which each break-even current density is reported.
#: 1.0 is the thermodynamic floor; the base case sits near 0.28.
REPORTING_EFFICIENCIES = (1.00, 0.75, 0.50)

MET_AT_BASE = "met_at_base"
REACHABLE = "reachable"
UNREACHABLE = "unreachable"                      # impact targets: the response levels off in the sweep
NOT_REACHED = "not_reached_in_swept_range"       # cost targets: still falling at the edge of the sweep


@dataclass(frozen=True)
class Target:
    """A break-even threshold on one model response.

    `value` is an absolute threshold. `margin_above_floor` instead sets the
    threshold from the lowest value the response reaches in the sweep -- 0.10
    means "come within 10% of the best the swept range reaches" (close to the
    asymptote for a response that has levelled off) -- which is how the point of
    diminishing returns is located without inventing an external benchmark.

    `levels_off` says whether the response levels off within the swept range (the
    impact responses: a target below the lowest swept value is then unreachable by
    reactor performance alone) or is still falling at its edge (the cost responses:
    such a target is reported as not reached within the swept range).
    """

    name: str
    label: str
    response: str          # KPI column in the grid frame
    unit: str
    value: float | None = None
    margin_above_floor: float | None = None
    levels_off: bool = True

    def __post_init__(self) -> None:
        if (self.value is None) == (self.margin_above_floor is None):
            raise ValueError(
                f"target {self.name!r}: set exactly one of value or "
                "margin_above_floor")


def current_density_for_rate(rate_kg_m2_yr: float, faradaic_efficiency: float,
                             p: Parameters, n_electrons: float = 4.85) -> float:
    """LABORATORY current density delivering the at-scale rate `rate_kg_m2_yr` at a given FE (SI Eq. S57, S67).

    Inverse of ``axes.areal_se_rate_kg_m2_yr``: J_lab = r n F / (FE M retention). Units: A m-2.
    """
    if faradaic_efficiency <= 0:
        return float("nan")
    faraday = p.UC["Faraday constant"]
    molar_mass_kg = p.UC["Se"] / 1000.0
    retention = p.PROC["Scale-up"]["Current density retention"]
    return (rate_kg_m2_yr * n_electrons * faraday
            / (faradaic_efficiency * molar_mass_kg * SECONDS_PER_YEAR * retention))


def _subset(frame: pd.DataFrame, include_recovery: bool) -> pd.DataFrame:
    return frame[frame["include_recovery"] == include_recovery]


def response_floor(frame: pd.DataFrame, target: Target,
                   include_recovery: bool = True) -> float:
    """Lowest value the response reaches anywhere in the swept grid (column achievable_floor).

    Both cost and impact fall as the areal rate rises, so the minimum lies at the
    edge of the sweep. It approximates the asymptote only where the response has
    levelled off there (the impact responses); levelized cost at full scale is
    still falling at the edge, so its lowest swept value is a limit of the sweep,
    not of the process (SI Eq. S59; DESIGN_phase3 addendum A2).
    """
    values = _subset(frame, include_recovery)[target.response]
    values = values[np.isfinite(values)]
    return float(values.min()) if len(values) else float("nan")


def base_response(frame: pd.DataFrame, target: Target, base_flow: float,
                  include_recovery: bool = True) -> float:
    """The response at the base operating point: base areal rate, base flow."""
    subset = _subset(frame, include_recovery).copy()
    if subset.empty:
        return float("nan")
    subset["distance"] = ((np.log(subset["axis_relative"]) ** 2)
                          + (np.log(subset["flow_m3hr"] / base_flow) ** 2))
    return float(subset.loc[subset["distance"].idxmin(), target.response])


def threshold_for(frame: pd.DataFrame, target: Target,
                  include_recovery: bool = True) -> float:
    """Resolve a target to an absolute threshold on its response."""
    if target.value is not None:
        return target.value
    floor = response_floor(frame, target, include_recovery)
    # The floor may be negative (a net credit), so scale the magnitude.
    return floor + abs(floor) * target.margin_above_floor


def required_rate(frame: pd.DataFrame, target: Target, threshold: float,
                  flow_m3hr: float | None = None,
                  include_recovery: bool = True) -> tuple[float, float]:
    """Areal rate needed to meet `threshold`, and the flowrate where it is easiest.

    With `flow_m3hr`, the requirement is evaluated at the nearest swept
    flowrate. Without it, the easiest point in the whole sweep is returned --
    the largest plant, since scale helps a little even though it cannot carry
    the result on its own.

    Returns (rate, flow), both NaN when no swept rate meets the threshold.
    """
    crossings = break_even(frame, threshold, response=target.response,
                           include_recovery=include_recovery)
    crossings = crossings.dropna(subset=["axis_value_required"])
    if crossings.empty:
        return float("nan"), float("nan")

    if flow_m3hr is not None:
        row = crossings.iloc[(crossings["flow_m3hr"] - flow_m3hr).abs()
                             .argsort().iloc[0]]
    else:
        row = crossings.loc[crossings["axis_value_required"].idxmin()]
    return float(row["axis_value_required"]), float(row["flow_m3hr"])


def cost_targets(band: pd.DataFrame) -> list[Target]:
    """One cost break-even per benchmark technology, on the mean of its band."""
    targets = []
    for row in band.sort_values("mean_usd_m3").itertuples():
        if not np.isfinite(row.mean_usd_m3):
            continue
        targets.append(Target(
            name="cost_" + str(row.technology).lower().replace(" ", "_"),
            label=str(row.technology),
            response="lcot_usd_m3",
            value=float(row.mean_usd_m3),
            unit="USD/m3",
            levels_off=False,          # cost is still falling at the edge of the sweep (addendum A2)
        ))
    return targets


def impact_targets() -> list[Target]:
    """Life-cycle break-evens.

    There is no published life-cycle inventory for the benchmark technologies
    at this specification, so an external impact benchmark would have to be
    invented. The thresholds here are internal and unambiguous instead.

    Net zero asks whether the selenium recovery credit can be made to cancel
    the burden of building and running the plant. The floor-approach targets
    ask a different and more actionable question: at what areal rate does
    further reactor improvement stop buying anything, because what remains is
    not material burden at all.
    """
    return [
        Target(name="gwp_net_zero", label="Net-zero global warming",
               response="lca_per_m3::Global warming", value=0.0,
               unit="kg CO2 eq/m3"),
        Target(name="gwp_within_10pct_of_floor",
               label="Within 10% of the global warming floor",
               response="lca_per_m3::Global warming", margin_above_floor=0.10,
               unit="kg CO2 eq/m3"),
        Target(name="ecotox_net_zero", label="Net-zero ecotoxicity",
               response="lca_per_m3::Ecotoxicity", value=0.0, unit="CTUe/m3"),
        Target(name="ecotox_within_10pct_of_floor",
               label="Within 10% of the ecotoxicity floor",
               response="lca_per_m3::Ecotoxicity", margin_above_floor=0.10,
               unit="CTUe/m3"),
    ]


def decomposition(p: Parameters, frame: pd.DataFrame, targets: list[Target],
                  flow_m3hr: float | None = None,
                  include_recovery: bool = True,
                  efficiencies: tuple = REPORTING_EFFICIENCIES) -> pd.DataFrame:
    """Break-even table: required areal rate, and the J it implies at each FE.

    One row per target. `rate_factor_vs_base` and `j_factor_vs_base_at_fe_*`
    are multiples of the base case, which is what the discussion quotes.
    """
    base_rate = areal_se_rate_kg_m2_yr(p)
    base_j = p.PROC["FCDI-BES"]["Current Density"]
    base_fe = p.PROC["FCDI-BES"]["Faradaic Efficiency"]
    base_flow = p.INF["WW Flowrate Influent"]
    retention = p.PROC["Scale-up"]["Current density retention"]

    rows = []
    for target in targets:
        threshold = threshold_for(frame, target, include_recovery)
        floor = response_floor(frame, target, include_recovery)
        at_base = base_response(frame, target, base_flow, include_recovery)
        rate, flow = required_rate(frame, target, threshold, flow_m3hr,
                                   include_recovery)

        if at_base <= threshold:
            state = MET_AT_BASE
        elif np.isfinite(rate):
            state = REACHABLE
        else:
            state = UNREACHABLE if target.levels_off else NOT_REACHED

        row = {
            "target": target.name,
            "label": target.label,
            "response": target.response,
            "threshold": threshold,
            "threshold_unit": target.unit,
            "status": state,
            "value_at_base": at_base,
            "achievable_floor": floor,
            "flow_m3hr": flow,
            "required_areal_rate_kg_m2_yr": rate,
            "rate_factor_vs_base": rate / base_rate if np.isfinite(rate) else np.nan,
        }
        for efficiency in efficiencies:
            j = (current_density_for_rate(rate, efficiency, p)
                 if np.isfinite(rate) else np.nan)
            tag = f"{round(efficiency * 100)}"
            row[f"current_density_A_m2_at_fe_{tag}"] = j
            row[f"j_factor_vs_base_at_fe_{tag}"] = (
                j / base_j if np.isfinite(j) else np.nan)
        # Phase 3: the current densities above are on the laboratory basis, with this retention applied (SI Eq. S67)
        row["current_density_retention_applied"] = retention
        rows.append(row)

    table = pd.DataFrame(rows)
    table.attrs["base_areal_rate_kg_m2_yr"] = base_rate
    table.attrs["base_current_density_A_m2"] = base_j
    table.attrs["base_faradaic_efficiency"] = base_fe
    table.attrs["current_density_retention"] = retention
    return table


def verify(p: Parameters, table: pd.DataFrame,
           efficiencies: tuple = REPORTING_EFFICIENCIES) -> pd.DataFrame:
    """Round-trip each reported (J, FE) back through the forward relation.

    The decomposition is only useful if it is exact, so it is checked rather
    than asserted: recomputing the areal rate from the reported current density
    must return the requirement it was derived from.
    """
    checks = []
    for row in table.itertuples():
        if not np.isfinite(row.required_areal_rate_kg_m2_yr):
            continue
        for efficiency in efficiencies:
            tag = f"{round(efficiency * 100)}"
            j = getattr(row, f"current_density_A_m2_at_fe_{tag}")
            probe = p.replace({"PROC": {"FCDI-BES": {
                "Current Density": j, "Faradaic Efficiency": efficiency}}})
            checks.append({
                "target": row.target,
                "faradaic_efficiency": efficiency,
                "required": row.required_areal_rate_kg_m2_yr,
                "recovered": areal_se_rate_kg_m2_yr(probe),
            })
    frame = pd.DataFrame(checks)
    if not frame.empty:
        frame["relative_error"] = (
            (frame["recovered"] - frame["required"]).abs()
            / frame["required"].abs().clip(lower=1e-30))
    return frame
