"""Monte Carlo uncertainty propagation.

All uncertain parameters are sampled simultaneously from the distributions of the
UncertaintyDistributions reference table, plus a triangular multiplier on each
equipment power draw (SI Eq. S61). Seed and draw counts come from the
SamplingSettings reference table.

Sampling uses numpy Generator objects seeded per draw, not the legacy global
RNG, so a run is reproducible regardless of how the work is distributed across
worker processes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from .. import datalayer as dl
from .. import io
from ..params import Parameters
from ._runner import evaluate
from .sensitivity import PARAMETERS as SA_PARAMETERS

# Equipment power draws are not in the uncertainty table; the original model
# applied a plus/minus 20% triangular multiplier to each. Kept.
POWER_MULTIPLIER_UNITS = ("P1", "P2", "P3", "P4", "P5",
                          "Screen", "Centrifuge", "Slurry Mixer")
POWER_MULTIPLIER_BOUNDS = (0.8, 1.0, 1.2)

# The uncertainty table labels differ slightly from the sensitivity registry.
_LABEL_TO_SA = {
    "Faradaic Efficiency": "Faradaic efficiency",
    "Removal Efficiency": "Removal efficiency",
    "Current Density (A/m2)": "Current density",
    "Membrane Price ($/m2)": "Membrane price",
    "Electrode Price ($/kg)": "Electrode price",
    "Membrane Lifetime (yr)": "Membrane lifetime",
    "Electrode Lifetime (yr)": "Electrode lifetime",
    "Site Preparation (% C_TBM)": "Site preparation",
    "Contingency Fee (% C_DPI)": "Contingency fee",
    "Contractor Fee (% C_DPI)": "Contractors fee",
    "Land Estimation (% C_TDC)": "Land",
    "Startup Estimation (% C_TDC)": "Startup",
    "No. of Shift Operators": "Number of shift operators",
    "Operator Wage ($/hr)": "Operator wage",
    "Operating Overhead (%)": "Operating overhead",
    "Taxes & Insurance (% C_TDC)": "Taxes and insurance",
    "Selenium Price ($/kg)": "Selenium price",
    # Phase 3 (UncertaintyDistributions v02)
    "Reactor Bare-Module Factor (-)": "Reactor bare-module factor",
    "Current Density Retention (-)": "Current density retention",
    "Shunt Current Fraction (-)": "Shunt current fraction",
}

# Parameters that are physically bounded fractions; rebasing must not push a
# bound past 1.0.
_FRACTIONAL = {"Faradaic Efficiency", "Removal Efficiency", "Current density retention", "Shunt current fraction"}


@dataclass
class MCSpec:
    # n_draws and seed default to the SamplingSettings table (1000 draws, seed 20260901).
    n_draws: int = field(default_factory=lambda: io.sampling_value("n_draws"))
    seed: int = field(default_factory=lambda: io.sampling_value("seed"))
    distribution: str = "triangular"   # or "uniform", for the distribution check
    include_recovery: bool = True
    n_jobs: int = -1
    kpis: tuple = field(default_factory=lambda: (
        "opex_per_m3", "capex_per_m3", "lcot_usd_m3", "total_energy_kWh_m3",
        "se_recovery_cost_usd_kg", "specific_energy_kWh_kg_se"))


def rebase(spec_entry: dict, scenario_base: float, fractional: bool = False) -> dict:
    """Recentre a distribution on the active scenario base value.

    The uncertainty table was written against the superseded 2 V operating
    point, so its absolute bounds for Faradaic efficiency, removal efficiency
    and current density belong to a reactor this model no longer describes.
    Its bounds are relative statements though (high uncertainty, varied by
    plus/minus 50 percent; min and max from quotes), so the relative spread is
    preserved and reapplied around whatever base the scenario sets.

    Without this the Monte Carlo silently samples around the old operating
    point and reports a cost distribution that excludes the base case.
    """
    table_base = spec_entry["base"]
    if table_base == 0 or scenario_base == table_base:
        return spec_entry

    ratio = scenario_base / table_base
    low = spec_entry["min"] * ratio
    high = spec_entry["max"] * ratio
    if fractional:
        low = max(0.0, min(low, 1.0))
        high = max(0.0, min(high, 1.0))

    return {**spec_entry, "base": scenario_base, "min": low, "max": high,
            "rebased_from": table_base}


def _draw(rng: np.random.Generator, spec_entry: dict, distribution: str) -> float:
    low, mode, high = spec_entry["min"], spec_entry["base"], spec_entry["max"]
    kind = spec_entry["dist"]

    if kind == "discrete":
        return float(rng.integers(int(round(low)), int(round(high)) + 1))
    if high == low:
        return float(mode)
    if distribution == "uniform":
        return float(rng.uniform(low, high))
    mode = min(max(mode, low), high)
    return float(rng.triangular(low, mode, high))


def _registry(p: Parameters) -> list:
    """(label, override_builder, distribution spec) for every sampled input."""
    by_label = {sa.label: sa for sa in SA_PARAMETERS}
    entries = []

    for raw_label, spec_entry in p.MC_DIST.items():
        sa_label = _LABEL_TO_SA.get(raw_label)
        if sa_label is None or sa_label not in by_label:
            continue
        parameter = by_label[sa_label]
        spec_entry = rebase(spec_entry, parameter.base_value(p),
                            fractional=parameter.name in _FRACTIONAL)
        entries.append((raw_label, parameter.override, spec_entry))

    for unit in POWER_MULTIPLIER_UNITS:
        low, mode, high = POWER_MULTIPLIER_BOUNDS
        entries.append((
            "Power - " + unit,
            lambda value, u=unit: {"POWER_MULT": {u: value}},
            {"dist": "triangular", "base": mode, "min": low, "max": high},
        ))
    return entries


def run(p: Parameters, spec: MCSpec | None = None) -> pd.DataFrame:
    spec = spec or MCSpec()
    scenario = p.scenario.get("name", "base")
    data_dir = str(p.data_dir) if p.data_dir else None
    entries = _registry(p)

    samples, overrides_list = [], []
    for draw_id in range(spec.n_draws):
        rng = np.random.default_rng([spec.seed, draw_id])
        sample, overrides = {}, {}
        for label, build, spec_entry in entries:
            value = _draw(rng, spec_entry, spec.distribution)
            sample[label] = value
            _deep_update(overrides, build(value))
        samples.append(sample)
        overrides_list.append(overrides)

    outcomes = Parallel(n_jobs=spec.n_jobs, prefer="processes")(
        delayed(evaluate)(scenario, data_dir, ov, spec.include_recovery)
        for ov in overrides_list
    )

    rows = []
    for draw_id, (sample, kpis) in enumerate(zip(samples, outcomes)):
        row = {"draw": draw_id, "distribution": spec.distribution,
               "status": "error" if "error" in kpis else "ok"}
        row.update({"input::" + k: v for k, v in sample.items()})
        row.update({k: kpis.get(k) for k in spec.kpis})
        if "error" in kpis:
            row["error"] = kpis["error"]
        rows.append(row)
    return pd.DataFrame(rows)


def _deep_update(target: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict):
            _deep_update(target.setdefault(key, {}), value)
        else:
            target[key] = value


def summarize(frame: pd.DataFrame, baseline: dict | None = None) -> pd.DataFrame:
    """P5 / P50 / P95, mean, standard deviation and coefficient of variation."""
    good = frame[frame["status"] == "ok"]
    metrics = [c for c in frame.columns
               if not c.startswith("input::")
               and c not in ("draw", "status", "distribution", "error")]

    rows = []
    for metric in metrics:
        series = pd.to_numeric(good[metric], errors="coerce").dropna()
        if series.empty:
            continue
        mean = series.mean()
        rows.append({
            "kpi": metric,
            "baseline": (baseline or {}).get(metric),
            "mean": mean,
            "median": series.median(),
            "std": series.std(),
            "cov_pct": 100.0 * series.std() / abs(mean) if mean else float("nan"),
            "p5": series.quantile(0.05),
            "p95": series.quantile(0.95),
            "n": int(series.size),
        })
    return pd.DataFrame(rows)


def correlations(frame: pd.DataFrame, kpi: str) -> pd.DataFrame:
    """Pearson correlation between each sampled input and one KPI."""
    good = frame[frame["status"] == "ok"]
    target = pd.to_numeric(good[kpi], errors="coerce")
    rows = []
    for column in [c for c in good.columns if c.startswith("input::")]:
        series = pd.to_numeric(good[column], errors="coerce")
        if series.std() == 0:
            continue
        rows.append({"input": column.removeprefix("input::"),
                     "kpi": kpi, "pearson_r": series.corr(target)})
    return (pd.DataFrame(rows)
            .assign(abs_r=lambda d: d["pearson_r"].abs())
            .sort_values("abs_r", ascending=False)
            .drop(columns="abs_r")
            .reset_index(drop=True))


def write(frame: pd.DataFrame, distribution: str, include_recovery: bool,
          out_dir: Path | None = None) -> Path:
    """Write the draws of one distribution and configuration under their registered
    name, into `out_dir` or, by default, the output folder of the current run."""
    path = dl.out("monte_carlo", distribution, include_recovery) if out_dir is None \
        else Path(out_dir) / dl.out_name("monte_carlo", distribution, include_recovery)
    return dl.write_csv(frame, path)
