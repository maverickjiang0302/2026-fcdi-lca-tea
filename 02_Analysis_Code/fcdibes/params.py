"""The parameter set, passed explicitly instead of mutated globally.

The original spreadsheet-derived notebook kept UC / INF / PROC / MAT / EQUIP /
TEA / PRICE in module-level dicts and ran sensitivity and Monte Carlo by
mutating them inside a try/finally that restored the originals afterwards.
That works exactly once, serially, and only if nothing raises in between.

Parameters is immutable by convention instead: replace() returns a brand new
object with the overrides applied to a deep copy, so runs cannot interfere.
That is what makes the Monte Carlo and the two-dimensional grid parallelisable.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import io

# Override keys that address a nested {group: {parameter: value}} lookup.
_NESTED = ("PROC", "MAT", "EQUIP")
# Override keys that address a flat {parameter: value} lookup.
_FLAT = ("TEA", "PRICE", "INF", "UC", "NORM")


class Parameters:
    """A complete, self-contained parameter set for one model run."""

    __slots__ = ("_raw", "power_mult", "data_dir")

    def __init__(self, raw: dict, power_mult: dict | None = None,
                 data_dir: Path | None = None):
        self._raw = raw
        self.power_mult = dict(power_mult or {})
        self.data_dir = data_dir

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, scenario: str = "base", data_dir: Path | None = None) -> "Parameters":
        """Load the base tables, then apply the scenario's overrides.

        Scenario overrides go through the same replace() path that sensitivity
        and Monte Carlo use, so there is one override code path to get right.
        """
        raw = io.load_raw(data_dir=data_dir, scenario=scenario)
        return cls(raw, data_dir=data_dir).replace(
            raw["scenario"].get("parameter_overrides"))

    def replace(self, overrides: dict | None = None) -> "Parameters":
        """Return a new Parameters with `overrides` applied to a deep copy.

        Override shape:

            {"PROC": {"FCDI-BES": {"Faradaic Efficiency": 0.30}},
             "TEA":  {"Interest Rate": 0.08},
             "POWER_MULT": {"P1": 1.1}}

        An unknown parameter raises. The original notebook mutated plain dicts,
        so a misspelled key in a sensitivity registry silently created a new
        entry and changed nothing.
        """
        if not overrides:
            return Parameters(io.deep_copy(self._raw), self.power_mult, self.data_dir)

        raw = io.deep_copy(self._raw)
        power_mult = dict(self.power_mult)

        for key, payload in overrides.items():
            if key == "POWER_MULT":
                power_mult.update(payload)
            elif key in _NESTED:
                for group, params in payload.items():
                    for name, value in params.items():
                        if name not in raw[key][group]:
                            raise KeyError(
                                "override targets unknown parameter "
                                + repr(group) + " / " + repr(name))
                        raw[key][group][name] = value
            elif key in _FLAT:
                for name, value in payload.items():
                    if name not in raw[key]:
                        raise KeyError(
                            "override targets unknown " + key + " parameter " + repr(name))
                    raw[key][name] = value
            else:
                raise KeyError("unsupported override key " + repr(key))

        return Parameters(raw, power_mult, self.data_dir)

    # -- lookups -----------------------------------------------------------
    @property
    def scenario(self) -> dict:
        return self._raw["scenario"]

    def __getattr__(self, name: str):
        """Expose UC, INF, PRICE, TEA, NORM, PROC, MAT, EQUIP, EC_INV,
        FG_RAW, LCA_FACTORS, LCA_GAPS, CATALOG and MC_DIST as attributes.

        Private names are refused outright. Without that guard, unpickling
        recurses forever: the fresh object has no _raw yet, so looking up
        __setstate__ falls through to here, which reads self._raw, which
        falls through to here again.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return object.__getattribute__(self, "_raw")[name]
        except (KeyError, AttributeError) as exc:
            raise AttributeError(name) from exc

    # -- pickling (joblib ships these to worker processes) -----------------
    def __getstate__(self) -> dict:
        return {"raw": self._raw, "power_mult": self.power_mult,
                "data_dir": self.data_dir}

    def __setstate__(self, state: dict) -> None:
        object.__setattr__(self, "_raw", state["raw"])
        object.__setattr__(self, "power_mult", state["power_mult"])
        object.__setattr__(self, "data_dir", state["data_dir"])

    def power_multiplier(self, unit_id: str) -> float:
        """Sensitivity/Monte Carlo multiplier on one unit's power draw."""
        return float(self.power_mult.get(unit_id, 1.0))

    # -- equipment selection ----------------------------------------------
    def select_from_catalog(self, equip_type: str, required_value: float,
                            max_col: str = "max_flowrate_m3hr") -> dict:
        """Pick the smallest catalog model that meets `required_value`.

        If the requirement exceeds every model, take the largest and
        parallelise, which is how pilot-to-plant scale-up is realised for
        pumps, screens and centrifuges. Discrete selection is why the cost
        surfaces show small steps rather than being perfectly smooth.
        """
        frame = self.CATALOG[equip_type]
        fits = frame[frame[max_col] >= required_value]

        if not fits.empty:
            row, n_units = fits.iloc[0], 1
        else:
            row = frame.iloc[-1]
            n_units = math.ceil(required_value / row[max_col])

        cost = float(row["purchase_cost_usd"])
        power = float("nan")
        for column in ("power_kW", "nominal_power_kW"):
            if column in row.index and _is_number(row[column]):
                power = float(row[column])
                break

        return {
            "n_units": n_units,
            "model_name": str(row["model_name"]),
            "cost_per_unit": cost,
            "power_per_unit": power,
            "total_cost": cost * n_units,
            "total_power": power * n_units if not math.isnan(power) else float("nan"),
        }

    def __repr__(self) -> str:
        name = self.scenario.get("name", "?")
        mult = f", power_mult={self.power_mult}" if self.power_mult else ""
        return "Parameters(scenario=" + repr(name) + mult + ")"


def _is_number(value) -> bool:
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False
