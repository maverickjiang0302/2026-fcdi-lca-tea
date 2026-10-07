"""The four measured operating points (Phase 3): one row per condition and configuration.

Riveros et al. 2026 (Water Res. 303, 126244) ran the FCDI-BES reactor continuously for 41 days at 2 V and 3 V, each
with and without nitrate. The scenario file D06 v03 carries the four steady-state operating points (days 19-41) as the
scenarios of datalayer.CONDITIONS: 3V = base, 2V = cond_2v, 2V+N = cond_2v_n, 3V+N = cond_3v_n, derived from D05 by a
script (I = Q/t, J = I/A_mem,lab, FE = FE_Se, V; removal efficiency: the integral estimator of the source workbook for
the selenium-only runs, the molar balance of D05 for the nitrate runs). This module joins each condition's scenario
run (the same run whose tables RunAll writes: `results`, or a fresh run_scenario when none is given) with the D05 row
it came from:

    operating point   voltage, lab current and current density, retention and effective current density (Eq. S67),
                      Faradaic efficiency (selenium and nitrate), removal efficiency used and its estimator, both
                      estimators where both exist, n_eff
    derived           areal selenium-reduction rate retained at scale (Eq. S54, S67: the axis of Figs 5-6),
                      selenium-specific current I*FE and current density J*FE (laboratory basis)
    results           membrane area, reactor power, total power, energy intensity, LCOT and its capital and operating
                      parts, global warming, ecotoxicity, selenium recovered
    ratios            each result divided by the base case (3V) of the same configuration

Written by RunAll (step 1b) to datalayer OUT['conditions']; read by the manuscript (Table 3) and the SI (Table S26).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import datalayer as dl
from .. import io
from ..model import run_scenario
from .axes import areal_se_rate_kg_m2_yr

D05_WINDOW = "steady_state_19_41d"
D05_CONDITION = {"3V": "3V Se", "2V": "2V Se", "2V+N": "2V Se+N", "3V+N": "3V Se+N"}   # condition -> D05 condition
# Removal-efficiency estimator of each condition (DESIGN_phase3 section 3.2): the integral estimator exists for the
# selenium-only runs only. For the molar-balance conditions the scenario value is checked to equal the D05 formula.
RE_ESTIMATOR = {"3V": "integral", "2V": "integral", "2V+N": "molar balance", "3V+N": "molar balance"}
# Molar masses of the molar-balance estimator, as DESIGN_phase3 section 3.2 states it (and the derivation script uses)
D05_MOLAR_MASS = {"SeO4": 142.96, "SeO3": 127}
GWP, ECOTOX = "lca_per_m3::Global warming", "lca_per_m3::Ecotoxicity"
RATIO_COLUMNS = ("current_A", "se_specific_current_A", "areal_rate_kg_m2_yr", "membrane_area_m2", "reactor_power_kW",
                 "total_energy_kWh_m3", "lcot_usd_m3", "gwp_kg_co2eq_m3")


def re_molar_balance(row: pd.Series) -> float:
    """The molar-balance removal efficiency of one D05 row (DESIGN_phase3 section 3.2)."""
    mol_in = ((row["SeO4_in_mgL"] * row["V_total_L"] / D05_MOLAR_MASS["SeO4"]
               + row["SeO3_in_mgL"] * row["V_total_L"] / D05_MOLAR_MASS["SeO3"]) / 1000)
    return (row["mol_SeO4_removed"] + row["mol_SeO3_removed"]) / mol_in


def table(results: dict | None = None, data_dir: Path | None = None) -> pd.DataFrame:
    """The condition comparison. `results` maps (scenario, include_recovery) to a model Results (RunAll passes the
    runs of step 1); missing entries are solved here with run_scenario (same inputs, same numbers)."""
    d05 = io.load_electron_partitioning(data_dir)
    d05 = d05[d05["window"] == D05_WINDOW].set_index("condition")
    rows = []
    for label, scenario in dl.CONDITIONS:
        source = d05.loc[D05_CONDITION[label]]
        for recovery in (True, False):
            result = (results or {}).get((scenario, recovery))
            if result is None:
                result = run_scenario(scenario, include_recovery=recovery, data_dir=data_dir)
            p, kpis = result.params, result.kpis
            fcdi, scale_up = p.PROC["FCDI-BES"], p.PROC["Scale-up"]
            molar = float(re_molar_balance(source))
            used = fcdi["Removal Efficiency"]
            estimator = RE_ESTIMATOR[label]
            if estimator == "molar balance" and used != molar:
                raise ValueError(f"condition {label}: the scenario's removal efficiency {used!r} is not the D05 molar "
                                 f"balance {molar!r}, which the scenario file says it is")
            rows.append({
                "condition": label,
                "is_base": label == dl.BASE_CONDITION,
                "scenario": scenario,
                "include_recovery": recovery,
                "flow_m3hr": kpis["flow_m3hr"],
                "d05_condition": D05_CONDITION[label],
                "d05_window": D05_WINDOW,
                "voltage_V": fcdi["Voltage Applied"],
                "current_A": fcdi["Current"],
                "current_density_A_m2": fcdi["Current Density"],
                "current_density_retention": scale_up["Current density retention"],
                "effective_current_density_A_m2": kpis["effective_current_density_A_m2"],
                "faradaic_efficiency_se": fcdi["Faradaic Efficiency"],
                "faradaic_efficiency_se_d05": float(source["FE_Se"]),
                "faradaic_efficiency_no3_d05": float(source["FE_NO3"]),
                "parasitic_fraction_d05": float(source["parasitic_pct"]) / 100,
                "removal_efficiency": used,
                "removal_efficiency_estimator": estimator,
                "removal_efficiency_integral": used if estimator == "integral" else float("nan"),
                "removal_efficiency_molar_balance": molar,
                "n_eff_d05": float(source["n_eff"]),
                "areal_rate_kg_m2_yr": areal_se_rate_kg_m2_yr(p),
                "se_specific_current_A": fcdi["Current"] * fcdi["Faradaic Efficiency"],
                "se_specific_current_density_A_m2": fcdi["Current Density"] * fcdi["Faradaic Efficiency"],
                "membrane_area_m2": kpis["membrane_area_m2"],
                "reactor_power_kW": result.system.reactor.power_kW,
                "total_power_kW": kpis["total_power_kW"],
                "total_energy_kWh_m3": kpis["total_energy_kWh_m3"],
                "lcot_usd_m3": kpis["lcot_usd_m3"],
                "capex_per_m3": kpis["capex_per_m3"],
                "opex_per_m3": kpis["opex_per_m3"],
                "gwp_kg_co2eq_m3": kpis[GWP],
                "ecotoxicity_ctue_m3": kpis[ECOTOX],
                "se_recovered_kghr": kpis["se_recovered_kghr"],
            })
    frame = pd.DataFrame(rows)
    for column in RATIO_COLUMNS:
        base = frame[frame["is_base"]].set_index("include_recovery")[column]
        frame[column + "_ratio_to_base"] = [value / base[recovery]
                                            for value, recovery in zip(frame[column], frame["include_recovery"])]
    return frame


def write(frame: pd.DataFrame, out_dir: Path | None = None) -> Path:
    path = dl.out("conditions") if out_dir is None else Path(out_dir) / dl.out_name("conditions")
    return dl.write_csv(frame, path)


def markers(frame: pd.DataFrame, include_recovery: bool = True) -> list:
    """(condition label, flow (m3/h), areal rate at scale, is_base) per condition, for the Fig 5-6 markers: the
    scenario's own influent flow (the pilot base case) and the rate column of this table."""
    subset = frame[frame["include_recovery"] == include_recovery]
    return [(r.condition, float(r.flow_m3hr), float(r.areal_rate_kg_m2_yr), bool(r.is_base))
            for r in subset.itertuples()]
