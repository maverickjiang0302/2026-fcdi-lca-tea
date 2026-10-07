"""Two-dimensional sweep: plant scale against an operating parameter.

This is the manuscript Section 3. It answers the question the earlier
one-dimensional flowrate sweep could not: how much of the cost and burden is
addressable by building bigger, versus by improving the reactor.

Output is one tidy CSV per run, from which both figures render - one with
global warming potential as the response, one with levelized cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from .. import datalayer as dl
from ..params import Parameters
from . import axes as axes_mod
from ._runner import evaluate


@dataclass
class GridSpec:
    axis: str = "areal_se_rate"
    flow_min_m3hr: float = 10.0
    flow_max_m3hr: float = 2600.0
    # Constructor defaults are the pre-restructure constants (24 x 24). The pipeline never relies on them:
    # RunAll passes the sizes of the SamplingSettings table explicitly (28 x 28, 8 x 8 for --quick, 16 x 16 for
    # the alternate axes), so a bare GridSpec() / grid.run(params) still gives exactly what it gave before.
    n_flow: int = 24
    axis_min_factor: float = 0.25      # relative to the scenario base value
    axis_max_factor: float = 40.0
    n_axis: int = 24
    include_recovery: tuple = (True, False)
    n_jobs: int = -1

    def flows(self) -> np.ndarray:
        return np.geomspace(self.flow_min_m3hr, self.flow_max_m3hr, self.n_flow)

    def axis_values(self, base: float) -> np.ndarray:
        return np.geomspace(base * self.axis_min_factor,
                            base * self.axis_max_factor, self.n_axis)


def run(p: Parameters, spec: GridSpec | None = None) -> pd.DataFrame:
    spec = spec or GridSpec()
    axis = axes_mod.registry(p)[spec.axis]
    base_value = axis.base(p)

    flows = spec.flows()
    values = spec.axis_values(base_value)

    scenario = p.scenario.get("name", "base")
    data_dir = str(p.data_dir) if p.data_dir else None

    jobs, index = [], []
    for recovery in spec.include_recovery:
        for flow in flows:
            for value in values:
                overrides = {"INF": {"WW Flowrate Influent": float(flow)}}
                _deep_update(overrides, axis.build(float(value)))
                jobs.append(delayed(evaluate)(scenario, data_dir,
                                              overrides, recovery))
                index.append((recovery, float(flow), float(value)))

    outcomes = Parallel(n_jobs=spec.n_jobs, prefer="processes")(jobs)

    rows = []
    for (recovery, flow, value), kpis in zip(index, outcomes):
        row = {
            "include_recovery": recovery,
            "flow_m3hr": flow,
            "axis": spec.axis,
            "axis_value": value,
            "axis_relative": value / base_value,
        }
        row.update(kpis or {"error": "no result"})
        rows.append(row)

    frame = pd.DataFrame(rows)
    frame.attrs["axis_label"] = axis.label
    frame.attrs["axis_base"] = base_value
    frame.attrs["axis_log"] = axis.log_scale
    return frame


def _deep_update(target: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict):
            _deep_update(target.setdefault(key, {}), value)
        else:
            target[key] = value


def break_even(frame: pd.DataFrame, target_usd_m3: float,
               response: str = "lcot_usd_m3",
               include_recovery: bool = True) -> pd.DataFrame:
    """For each flowrate, the axis value at which the response meets a target.

    Interpolates on log(axis value) along each flowrate column. Returns NaN
    where the target is unreachable inside the swept range, which is itself a
    result worth reporting. (SI Eq. S56)
    """
    subset = frame[frame["include_recovery"] == include_recovery]
    rows = []
    for flow, group in subset.groupby("flow_m3hr"):
        group = group.sort_values("axis_value")
        x = np.log(group["axis_value"].to_numpy(dtype=float))
        y = group[response].to_numpy(dtype=float)
        good = np.isfinite(y)
        x, y = x[good], y[good]

        crossing = np.nan
        if len(y) >= 2 and y.min() <= target_usd_m3 <= y.max():
            # The response falls monotonically with the axis, so flip for interp.
            order = np.argsort(y)
            crossing = float(np.exp(np.interp(target_usd_m3, y[order], x[order])))
        rows.append({"flow_m3hr": flow, "target": target_usd_m3,
                     "axis_value_required": crossing})
    return pd.DataFrame(rows)


def saturation_flow(frame: pd.DataFrame, response: str = "lcot_usd_m3",
                    include_recovery: bool = True,
                    tolerance: float = 0.05) -> float:
    """Smallest flowrate beyond which the response is within `tolerance` of its
    large-scale limit, at the base axis value. Quantifies where economies of
    scale stop paying.
    """
    subset = frame[(frame["include_recovery"] == include_recovery)].copy()
    at_base = subset.iloc[(subset["axis_relative"] - 1.0).abs().argsort()]
    base_value = at_base["axis_value"].iloc[0]
    line = (subset[subset["axis_value"] == base_value]
            .sort_values("flow_m3hr").dropna(subset=[response]))
    if line.empty:
        return float("nan")

    limit = line[response].iloc[-1]
    spread = line[response].iloc[0] - limit
    if spread <= 0:
        return float(line["flow_m3hr"].iloc[0])
    within = line[(line[response] - limit) <= tolerance * spread]
    return float(within["flow_m3hr"].iloc[0]) if not within.empty else float("nan")


def write(frame: pd.DataFrame, axis: str, out_dir: Path | None = None) -> Path:
    """Write the grid of one axis under its registered name, into `out_dir` or, by
    default, the output folder of the current run."""
    path = dl.out("grid", axis) if out_dir is None \
        else Path(out_dir) / dl.out_name("grid", axis)
    return dl.write_csv(frame, path)
