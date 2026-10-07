"""One-at-a-time sensitivity, for tornado plots.

Each parameter is moved by a fixed relative step with everything else held at
the base case, and the resulting swing in each KPI is recorded (SI Eq. S60).
Cheap, transparent, and the standard presentation for a TEA, but it cannot see
interactions, which is why the Monte Carlo and the two-dimensional grid exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from joblib import Parallel, delayed

from .. import datalayer as dl
from .. import io
from ..params import Parameters
from ._runner import evaluate


@dataclass(frozen=True)
class SAParameter:
    """One entry in the sensitivity registry.

    section  which lookup the parameter lives in (PROC, MAT, EQUIP, TEA,
             PRICE, INF) or the pseudo-section POWER_MULT
    group    the group within a nested lookup, or None for a flat one
    affects  both / tea / lca, used to filter tornado panels
    """

    label: str
    section: str
    group: str | None
    name: str
    affects: str = "both"

    def override(self, value: float) -> dict:
        if self.section == "POWER_MULT":
            return {"POWER_MULT": {self.name: value}}
        if self.group is not None:
            return {self.section: {self.group: {self.name: value}}}
        return {self.section: {self.name: value}}

    def base_value(self, p: Parameters) -> float:
        if self.section == "POWER_MULT":
            return 1.0
        section = getattr(p, self.section)
        return section[self.group][self.name] if self.group else section[self.name]


PARAMETERS: list = [
    # -- reactor performance ------------------------------------------------
    SAParameter("Faradaic efficiency", "PROC", "FCDI-BES", "Faradaic Efficiency"),
    SAParameter("Removal efficiency", "PROC", "FCDI-BES", "Removal Efficiency"),
    SAParameter("Current density", "PROC", "FCDI-BES", "Current Density"),
    SAParameter("Applied voltage", "PROC", "FCDI-BES", "Voltage Applied"),
    SAParameter("Se recovery performance", "EQUIP", "Disc Stack Centrifuge",
                "Se recovery performance"),
    # -- stack and scale-up losses (Phase 3, SI Eq. S66-S67) ----------------
    SAParameter("Current density retention", "PROC", "Scale-up", "Current density retention"),
    SAParameter("Shunt current fraction", "PROC", "Scale-up", "Shunt current fraction"),
    # -- materials ----------------------------------------------------------
    SAParameter("Membrane price", "MAT", "Membranes", "Cost"),
    SAParameter("Membrane lifetime", "MAT", "Membranes", "Lifespan"),
    SAParameter("Electrode price", "MAT", "Graphite Electrodes", "Cost"),
    SAParameter("Electrode lifetime", "MAT", "Graphite Electrodes", "Lifespan"),
    SAParameter("PMMA price", "MAT", "Acrylic Glass (PMMA)", "Cost"),
    # -- equipment power ----------------------------------------------------
    SAParameter("Power - influent pump", "POWER_MULT", None, "P1"),
    SAParameter("Power - recirculation pump", "POWER_MULT", None, "P2"),
    SAParameter("Power - GAC screen", "POWER_MULT", None, "Screen"),
    SAParameter("Power - centrifuge", "POWER_MULT", None, "Centrifuge"),
    SAParameter("Power - slurry mixer", "POWER_MULT", None, "Slurry Mixer"),
    # -- capital factors (TEA only) ----------------------------------------
    # Phase 3: the FCDI-BES reactor's bare-module (installation) factor only; the cultivation reactor keeps its own.
    SAParameter("Reactor bare-module factor", "EQUIP", "FCDI-BES Reactor", "Bare-module factor", "tea"),
    SAParameter("Site preparation", "TEA", None, "Site Preparation Estimation", "tea"),
    SAParameter("Contingency fee", "TEA", None, "Contingency Fee Estimation", "tea"),
    SAParameter("Contractors fee", "TEA", None, "Contractor's Fee Estimation", "tea"),
    SAParameter("Land", "TEA", None, "Land Estimation", "tea"),
    SAParameter("Startup", "TEA", None, "Startup Estimation", "tea"),
    SAParameter("Interest rate", "TEA", None, "Interest Rate", "tea"),
    SAParameter("Plant lifetime", "TEA", None, "Plant lifetime", "tea"),
    # -- operating factors (TEA only) --------------------------------------
    SAParameter("Number of shift operators", "TEA", None, "No. of Shift Operators", "tea"),
    SAParameter("Operator wage", "TEA", None,
                "Wage for Water and Wastewater Treatment Plant Operators", "tea"),
    SAParameter("Operating overhead", "TEA", None, "operating overhead estimation", "tea"),
    SAParameter("Taxes and insurance", "TEA", None, "Taxes & Insurance Estimation", "tea"),
    SAParameter("Electricity price", "PRICE", None, "Electricity Price", "tea"),
    SAParameter("Selenium price", "PRICE", None, "Selenium", "tea"),
]


def run(p: Parameters, step: float | None = None, include_recovery: bool = True,
        n_jobs: int = -1) -> pd.DataFrame:
    """Perturb each parameter by plus and minus `step` and record KPI swings.

    `step` defaults to `oat_step` of the SamplingSettings table (0.10).
    """
    if step is None:
        step = io.sampling_value("oat_step", p.data_dir)
    scenario = p.scenario.get("name", "base")
    data_dir = str(p.data_dir) if p.data_dir else None

    baseline = evaluate(scenario, data_dir, {}, include_recovery)

    jobs, index = [], []
    for parameter in PARAMETERS:
        base_value = parameter.base_value(p)
        for direction in (-1, +1):
            value = base_value * (1 + direction * step)
            jobs.append(delayed(evaluate)(scenario, data_dir,
                                          parameter.override(value), include_recovery))
            index.append((parameter, direction, value))

    outcomes = Parallel(n_jobs=n_jobs, prefer="processes")(jobs)

    numeric_kpis = [k for k, v in baseline.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]

    rows = []
    for (parameter, direction, value), kpis in zip(index, outcomes):
        if "error" in kpis:
            continue
        for kpi in numeric_kpis:
            base_kpi = baseline.get(kpi)
            new_kpi = kpis.get(kpi)
            if base_kpi in (None, 0) or new_kpi is None:
                continue
            rows.append({
                "parameter": parameter.label,
                "affects": parameter.affects,
                "direction": "low" if direction < 0 else "high",
                "step": step,
                "parameter_value": value,
                "kpi": kpi,
                "value": new_kpi,
                "baseline": base_kpi,
                "pct_change": 100.0 * (new_kpi - base_kpi) / abs(base_kpi),
            })
    return pd.DataFrame(rows)


def tornado(frame: pd.DataFrame, kpi: str, top_n: int = 12) -> pd.DataFrame:
    """Rank parameters by total swing in one KPI."""
    subset = frame[frame["kpi"] == kpi]
    wide = subset.pivot_table(index="parameter", columns="direction",
                              values="pct_change", aggfunc="first")
    wide = wide.reindex(columns=["low", "high"]).fillna(0.0)
    wide["swing"] = (wide["high"] - wide["low"]).abs()
    return wide.sort_values("swing", ascending=False).head(top_n).reset_index()


def write(frame: pd.DataFrame, include_recovery: bool, out_dir: Path | None = None) -> Path:
    """Write the sensitivity table of one configuration under its registered name,
    into `out_dir` or, by default, the output folder of the current run."""
    path = dl.out("sensitivity", include_recovery) if out_dir is None \
        else Path(out_dir) / dl.out_name("sensitivity", include_recovery)
    return dl.write_csv(frame, path)
