"""The single entry point: parameters in, results out.

run_model() is a pure function. It reads nothing global and writes nothing
global, so it is safe to call concurrently -- which is what makes the Monte
Carlo and the two-dimensional scale grid tractable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import datalayer as dl
from .config import IMPACT_CATEGORIES, normalization_key
from .inventory import Inventory
from .inventory import build as build_inventory
from .lca import LCAResult
from .lca import solve as solve_lca
from .params import Parameters
from .system import System
from .tea import TEAResult
from .tea import solve as solve_tea


@dataclass
class Results:
    params: Parameters
    system: System
    inventory: Inventory
    tea: TEAResult
    lca: LCAResult
    include_recovery: bool

    @property
    def scenario(self) -> str:
        return self.params.scenario.get("name", "?")

    @property
    def kpis(self) -> dict:
        """Flat scalars, for sensitivity, Monte Carlo and grid sweeps."""
        t = self.tea
        out = {
            "scenario": self.scenario,
            "include_recovery": self.include_recovery,
            "flow_m3hr": self.system.Qin.flowrate_m3hr,
            "C_TBM": t.C_TBM,
            "C_TCI": t.C_TCI,
            "COM": t.COM,
            "annualized_capex": t.annualized_capex,
            "total_annualized_costs": t.total_annualized_costs,
            "opex_per_m3": t.opex_per_m3,
            "capex_per_m3": t.capex_per_m3,
            "lcot_usd_m3": t.lcot_usd_m3,
            "total_energy_kWh_m3": t.total_energy_kWh_m3,
            "se_recovery_cost_usd_kg": t.se_recovery_cost_usd_kg,
            "specific_energy_kWh_kg_se": t.specific_energy_kWh_kg_se,
            "se_recovered_kghr": self.system.se_recovered_kghr,
            "total_power_kW": self.system.power_kW,
            "membrane_area_m2": self.system.reactor.results["Total membrane area, m2"],
            "electrode_mass_kg": self.system.reactor.results["Total electrode mass, kg"],
            "current_A": self.system.reactor.results["Current, A"],
            "recirc_bound_binding": self.system.reactor.results["Recirc bound binding"],
            # Phase 3: stack and scale-up losses (SI Eq. S65-S67), inserted here, after recirc_bound_binding and
            # before the LCA keys; the earlier keys keep their relative order (readers use names)
            "effective_current_density_A_m2": self.system.reactor.results["Effective current density, A/m2"],
            "stack_supply_current_A": self.system.reactor.results["Stack supply current, A"],
            "ir_drop_V": self.system.reactor.results["In-plane IR drop, V"],
        }
        for category in IMPACT_CATEGORIES:
            short = normalization_key(category)
            out["lca_per_m3::" + short] = self.lca.impacts_per_m3[category]
            if category in self.lca.impacts_normalized_per_m3:
                out["lca_norm_per_m3::" + short] = \
                    self.lca.impacts_normalized_per_m3[category]
        return out

    def write_csv(self, out_dir: Path | None = None) -> list[Path]:
        """Write every table behind a figure, so results stay traceable.

        The files land in `out_dir`, by default the scenario-results folder of
        the current run (datalayer.scenario_dir). File names come from the data
        layer's registry: <scenario>_<with|no>_recovery__<table>.csv.
        """
        out_dir = dl.scenario_dir() if out_dir is None else Path(out_dir)

        # The reactor is ~99.8% of capital, so its internal split is the only
        # informative capital breakdown; expose it as its own table.
        reactor_capex = pd.DataFrame([
            {"component": name,
             "installed_cost_usd": self.inventory.flows["CAPEX FCDI-BES " + key + ", $"]}
            for name, key in (("Ion-exchange membranes", "membrane"),
                              ("Graphite electrodes", "electrode"),
                              ("PMMA housing", "housing"))
        ])
        reactor_capex["share_pct"] = (100 * reactor_capex["installed_cost_usd"]
                                      / reactor_capex["installed_cost_usd"].sum())

        written = []
        tables = {
            "capex": self.inventory.capex,
            "reactor_capex": reactor_capex,
            "opex": self.inventory.opex,
            "lca_inventory": self.inventory.lca,
            "lca_contributions": self.lca.contributions.reset_index(),
            "lca_impacts": self.lca.summary_table(),
            "tea_capital": self.tea.capital_table(),
            "tea_com": self.tea.com_table(),
            "tea_metrics": self.tea.metrics_table(),
            "streams": self.system.stream_table().reset_index(),
            "power": self.system.power_table().reset_index(),
        }
        for name, frame in tables.items():
            path = out_dir / dl.scenario_file_name(self.scenario, self.include_recovery, name)
            written.append(dl.write_csv(frame, path))

        kpi_frame = pd.DataFrame([self.kpis])
        path = out_dir / dl.scenario_file_name(self.scenario, self.include_recovery, "kpis")
        written.append(dl.write_csv(kpi_frame, path))
        return written


def run_model(p: Parameters, include_recovery: bool = True,
              overrides: dict | None = None) -> Results:
    """Solve the flowsheet, inventory, TEA and LCA for one parameter set.

    `overrides` is applied on top of `p` via Parameters.replace(), leaving the
    caller's object untouched.
    """
    if overrides:
        p = p.replace(overrides)

    system = System(p, include_recovery=include_recovery).solve()
    inventory = build_inventory(system, p)
    tea = solve_tea(system, inventory, p)
    lca = solve_lca(system, inventory, p)

    return Results(params=p, system=system, inventory=inventory,
                   tea=tea, lca=lca, include_recovery=include_recovery)


def run_scenario(scenario: str = "base", include_recovery: bool = True,
                 overrides: dict | None = None,
                 data_dir: Path | None = None) -> Results:
    """Convenience wrapper: load a named scenario and solve it."""
    return run_model(Parameters.load(scenario, data_dir=data_dir),
                     include_recovery=include_recovery, overrides=overrides)
