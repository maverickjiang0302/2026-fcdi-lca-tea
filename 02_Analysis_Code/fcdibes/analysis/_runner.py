"""Shared worker plumbing for the parallel sweeps.

Worker processes load the scenario once and cache it, rather than having the
whole parameter set (including four catalogue DataFrames) pickled and shipped
for every one of the thousands of evaluations a sweep performs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .. import datalayer as dl
from ..model import run_model
from ..params import Parameters


@lru_cache(maxsize=8)
def _load(scenario: str, data_dir: str | None, factors: str) -> Parameters:
    """`factors` is only part of the cache key: the factor table that Parameters.load
    reads is chosen by LCA_FCDI_FACTORS at call time, so a reused worker must not
    hand back parameters loaded with another table."""
    return Parameters.load(scenario, data_dir=Path(data_dir) if data_dir else None)


def evaluate(scenario: str, data_dir: str | None, overrides: dict,
             include_recovery: bool) -> dict:
    """Run one model evaluation in a worker. Never raises.

    A failed combination returns an error marker instead of killing the sweep;
    infeasible corners of a grid are expected, and the caller reports how many
    were dropped rather than silently thinning the surface.
    """
    try:
        params = _load(scenario, data_dir, str(dl.factors_path(data_dir)))
        return run_model(params, include_recovery=include_recovery,
                         overrides=overrides).kpis
    except Exception as exc:                      # noqa: BLE001
        return {"error": type(exc).__name__ + ": " + str(exc)}
