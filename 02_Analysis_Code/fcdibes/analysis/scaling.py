"""One-dimensional flowrate sweep with Monte Carlo uncertainty bands.

Pilot scale (10 m3/hr) to a full-scale FGD wastewater plant (2600 m3/hr).
At each flowrate the model is solved once at the base parameters and again
across Monte Carlo draws, so the figures can carry a P5-P95 band rather than a
bare line.

The two-dimensional grid supersedes this for the main-text argument; this
sweep remains the clearest way to show where economies of scale run out.
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
from .uncertainty import _deep_update, _draw, _registry


@dataclass
class ScalingSpec:
    flow_min_m3hr: float = 10.0
    flow_max_m3hr: float = 2600.0
    # sweep size, draws and seed default to the SamplingSettings table (30, 100, 20260901)
    n_flow: int = field(default_factory=lambda: io.sampling_value("scaling_n_flow"))
    n_draws: int = field(default_factory=lambda: io.sampling_value("scaling_n_draws"))
    seed: int = field(default_factory=lambda: io.sampling_value("seed"))
    include_recovery: tuple = (True, False)
    n_jobs: int = -1
    kpis: tuple = field(default_factory=lambda: (
        "opex_per_m3", "capex_per_m3", "lcot_usd_m3", "total_energy_kWh_m3"))

    def flows(self) -> np.ndarray:
        return np.geomspace(self.flow_min_m3hr, self.flow_max_m3hr, self.n_flow)


def run(p: Parameters, spec: ScalingSpec | None = None) -> tuple:
    """Return (baseline curve, Monte Carlo draws) as two tidy frames."""
    spec = spec or ScalingSpec()
    scenario = p.scenario.get("name", "base")
    data_dir = str(p.data_dir) if p.data_dir else None
    entries = _registry(p)
    flows = spec.flows()

    # -- baseline curve -----------------------------------------------------
    base_jobs, base_index = [], []
    for recovery in spec.include_recovery:
        for flow in flows:
            base_jobs.append(delayed(evaluate)(
                scenario, data_dir, {"INF": {"WW Flowrate Influent": float(flow)}}, recovery))
            base_index.append((recovery, float(flow)))

    base_rows = []
    for (recovery, flow), kpis in zip(
            base_index, Parallel(n_jobs=spec.n_jobs, prefer="processes")(base_jobs)):
        row = {"include_recovery": recovery, "flow_m3hr": flow}
        row.update(kpis)
        base_rows.append(row)
    baseline = pd.DataFrame(base_rows)

    # -- Monte Carlo band ---------------------------------------------------
    mc_jobs, mc_index = [], []
    for recovery in spec.include_recovery:
        for flow in flows:
            for draw_id in range(spec.n_draws):
                rng = np.random.default_rng([spec.seed, draw_id])
                overrides = {"INF": {"WW Flowrate Influent": float(flow)}}
                for _label, build, spec_entry in entries:
                    _deep_update(overrides, build(_draw(rng, spec_entry, "triangular")))
                mc_jobs.append(delayed(evaluate)(scenario, data_dir, overrides, recovery))
                mc_index.append((recovery, float(flow), draw_id))

    mc_rows = []
    for (recovery, flow, draw_id), kpis in zip(
            mc_index, Parallel(n_jobs=spec.n_jobs, prefer="processes")(mc_jobs)):
        row = {"include_recovery": recovery, "flow_m3hr": flow, "draw": draw_id,
               "status": "error" if "error" in kpis else "ok"}
        row.update({k: kpis.get(k) for k in spec.kpis})
        mc_rows.append(row)

    return baseline, pd.DataFrame(mc_rows)


def bands(draws: pd.DataFrame, kpis: tuple | None = None) -> pd.DataFrame:
    """Collapse the draws to P5 / P50 / P95 at each flowrate."""
    good = draws[draws["status"] == "ok"]
    kpis = kpis or tuple(c for c in good.columns
                         if c not in ("include_recovery", "flow_m3hr", "draw", "status"))
    frames = []
    for kpi in kpis:
        grouped = (good.groupby(["include_recovery", "flow_m3hr"])[kpi]
                   .quantile([0.05, 0.50, 0.95]).unstack())
        grouped.columns = ["p5", "p50", "p95"]
        frames.append(grouped.assign(kpi=kpi).reset_index())
    return pd.concat(frames, ignore_index=True)


def saturation_flow(baseline: pd.DataFrame, kpi: str = "lcot_usd_m3",
                    include_recovery: bool = True, tolerance: float = 0.05) -> float:
    """The smallest swept flowrate at which (1 - tolerance) of the reduction of `kpi` over the swept range is
    achieved, i.e. kpi - kpi(largest flow) <= tolerance x (kpi(smallest flow) - kpi(largest flow)); with the default
    tolerance, the flow at which 95 % of the reduction is achieved (not 'within 5 % of the limit')."""
    line = (baseline[baseline["include_recovery"] == include_recovery]
            .sort_values("flow_m3hr").dropna(subset=[kpi]))
    if line.empty:
        return float("nan")
    limit = line[kpi].iloc[-1]
    spread = line[kpi].iloc[0] - limit
    if spread <= 0:
        return float(line["flow_m3hr"].iloc[0])
    within = line[(line[kpi] - limit) <= tolerance * spread]
    return float(within["flow_m3hr"].iloc[0]) if not within.empty else float("nan")


def write(baseline: pd.DataFrame, draws: pd.DataFrame,
          out_dir: Path | None = None) -> list:
    """Write the baseline curve and the P5-P95 bands of the base scenario under their
    registered names, into `out_dir` or, by default, the output folder of the current run."""
    def target(key: str) -> Path:
        return dl.out(key) if out_dir is None else Path(out_dir) / dl.out_name(key)

    return [dl.write_csv(baseline, target("scaling_baseline")),
            dl.write_csv(bands(draws), target("scaling_bands"))]
