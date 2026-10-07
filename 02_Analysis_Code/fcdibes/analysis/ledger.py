"""The stack and scale-up loss ledger with its computed quantities (Phase 3; SI Table S27).

The ledger itself is an input (D09: one row per loss mechanism of the scale-up module spec, with its treatment:
sampled, computed, design basis, modelled or not modelled, and the evidence). This module joins every D09 row with the
quantities the model computes for it at the base case, in a long table (section, mechanism, configuration, quantity,
value, unit, basis):

    pumping                 diluate and slurry channel pressure drops (SI Eq. S63-S64) in Pa, in metres of water and
                            as a share of the 1.2 m static-head allowance of the P1 / P2 heads; the friction margin
                            those heads carry (head safety margin); the slurry drop at a tenfold viscosity; channel
                            Reynolds numbers (laminar check)
    in-plane IR             the drop (SI Eq. S65) in V and as a share of the cell voltage, at the base case and at the
                            highest current density of the four measured conditions
    limiting current        the reference limiting current density of an FCDI cell (LIMITING_CURRENT_REFERENCE_A_M2)
                            over the base case's laboratory and effective current densities, and over the highest
                            laboratory current density of the four conditions
    shunt (two rows)        the shunt fraction, the supply-to-Faradaic current ratio 1/(1 - s), and the effect of the
                            shunt alone on reactor power, energy, LCOT and GWP (base against base with s = 0)
    retention (two rows)    the retention factor, the membrane-area ratio base / lab_ideal (= 1/retention), and the
                            effect of retention alone on LCOT and GWP (base against base with retention 1); and the
                            combined effect base against lab_ideal, both configurations
    replacement, make-up    the service lives the model uses (membranes, GAC)
    extrapolation           the ratios the TEA spec asks to print: throughput (laboratory middle-chamber flow, pilot,
                            full scale), membrane area (laboratory, base and lab-ideal at pilot scale, base at full
                            scale) and membrane area per cell, all on the basis of Eq. S5 (both membranes of a cell;
                            the laboratory cell is two faces of its 'Membrane Area'), and observed against assumed
                            time (41 d against the membrane, electrode and plant lives)

Every number is computed here from the registered inputs and model runs; none is typed. Written by RunAll (step 1c) to
datalayer OUT['loss_ledger'].
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import datalayer as dl
from .. import io
from ..model import run_model, run_scenario
from ..params import Parameters
from .grid import GridSpec

# Reference plateau current density of an FCDI cell: the midpoint of the 140-160 A/m2 (1.4-1.6 A on 100 cm2 of active
# membrane area) reached at 60 g/L NaCl with 15 wt% activated carbon by Rommerskirchen et al. 2020 (Desalination 490,
# 114453, p.8; scale-up module spec section 2.2): a feed-limited plateau that the paper attributes to the salt available
# in the feed, not an electrochemical limit. Confirmed from the full text on 2026-10-06 (D09 v02; the literature table
# v02 holds the current and the area as reported).
LIMITING_CURRENT_REFERENCE_A_M2 = 150.0
# Eq. S5, A_mem = 2 I / J, counts the two membranes of every cell (fcdibes/unitops.py FCDIBESReactor), whereas the
# laboratory 'Membrane Area' is ONE membrane face (the area the laboratory current density is referred to). The
# extrapolation ratios put the laboratory cell on the model's basis, two faces (DESIGN_phase3 addendum A7).
MEMBRANES_PER_CELL = 2
VISCOSITY_SENSITIVITY = 10.0             # the slurry drop is also reported at ten times the Newtonian estimate
GWP = "lca_per_m3::Global warming"
CONFIG = {True: "with_recovery", False: "no_recovery"}


def _row(section, mechanism, configuration, quantity, value, unit, basis, ledger=None) -> dict:
    ledger = ledger or {}
    return {"section": section, "mechanism": mechanism,
            "treatment": ledger.get("treatment", ""), "derating_target": ledger.get("derating_target", ""),
            "parameter": ledger.get("parameter", ""), "evidence": ledger.get("evidence", ""),
            "configuration": configuration, "quantity": quantity, "value": value, "unit": unit, "basis": basis}


def _run(results: dict, scenario: str, recovery: bool, data_dir):
    result = (results or {}).get((scenario, recovery))
    return result if result is not None else run_scenario(scenario, include_recovery=recovery, data_dir=data_dir)


def table(results: dict | None = None, data_dir: Path | None = None) -> pd.DataFrame:
    """The long ledger table (see the module docstring). `results` maps (scenario, include_recovery) to the Results
    of RunAll step 1; missing runs are solved here."""
    d09 = io.load_ledger(data_dir)
    by_mechanism = {r["mechanism"]: r for r in d09.to_dict("records")}
    mechanisms = list(by_mechanism)
    base = {rec: _run(results, "base", rec, data_dir) for rec in (True, False)}
    ideal = {rec: _run(results, "lab_ideal", rec, data_dir) for rec in (True, False)}
    p: Parameters = base[True].params
    fcdi, scale_up, uc = p.PROC["FCDI-BES"], p.PROC["Scale-up"], p.UC
    rx = base[True].system.reactor.results
    rho_g = fcdi["Water Density"] * uc["gravity constant"]
    allowance = rx["Pressure-drop allowance, Pa"]
    rows = []

    def find(fragment: str) -> str:
        hits = [m for m in mechanisms if fragment.lower() in m.lower()]
        if len(hits) != 1:
            raise KeyError(f"the ledger must have exactly one mechanism containing {fragment!r}; found {hits}")
        return hits[0]

    def add(fragment, configuration, quantity, value, unit, basis):
        name = find(fragment)
        rows.append(_row("mechanism", name, configuration, quantity, float(value), unit, basis, by_mechanism[name]))

    # -- pumping (Eq. S63-S64): the reactor is the same in both configurations ------------------------------------
    dp_mid, dp_side = rx["Diluate channel pressure drop, Pa"], rx["Slurry channel pressure drop, Pa"]
    basis = "base case, Eq. S63-S64"
    add("pumping", "both", "diluate channel pressure drop", dp_mid, "Pa", basis)
    add("pumping", "both", "diluate channel pressure drop", dp_mid / rho_g, "m water", basis)
    add("pumping", "both", "diluate drop as a share of the static-head allowance", 100 * dp_mid / allowance, "%", basis)
    add("pumping", "both", "slurry channel pressure drop", dp_side, "Pa", basis)
    add("pumping", "both", "slurry channel pressure drop", dp_side / rho_g, "m water", basis)
    add("pumping", "both", "slurry drop as a share of the static-head allowance", 100 * dp_side / allowance, "%", basis)
    add("pumping", "both", f"slurry channel pressure drop at {VISCOSITY_SENSITIVITY:g} x the slurry viscosity",
        VISCOSITY_SENSITIVITY * dp_side / rho_g, "m water", basis + " (laminar: the drop is linear in viscosity)")
    add("pumping", "both", "static-head allowance of the P1 / P2 heads (Static Head - FCDI-BES Modules)",
        allowance / rho_g, "m water", "ProcessAssumptions group Pump")
    # The friction and dynamic-loss margin the P1 / P2 heads already carry: each head is (gravitational + static head)
    # x Head Safety Margin ('covering friction and dynamic head losses'); the margin is the head minus the two heads.
    pump = p.PROC["Pump"]
    margin_m = (min(pump["Influent Head"], pump["Recirc Head"])
                - (pump["Gravitational Head"] + pump["Static Head - FCDI-BES Modules"]))
    add("pumping", "both", "friction and dynamic-loss margin of the P1 / P2 heads (head safety margin)", margin_m,
        "m water", "ProcessAssumptions group Pump: Influent / Recirc Head - (Gravitational Head + Static Head - "
        "FCDI-BES Modules)")
    gap = fcdi["Middle Chamber Thickness"] / uc["1 m"]
    path = fcdi["Cell Height"]
    v_mid = path / (fcdi["Middle Chamber HRT"] * uc["1 hr"])
    v_side = path / (fcdi["Side chamber HRT"] * uc["1 hr"])
    diameter = scale_up["Slurry channel diameter"] / 1000
    mu_w = scale_up["Water viscosity"]
    add("pumping", "both", "diluate channel Reynolds number (hydraulic diameter 2 x gap)",
        fcdi["Water Density"] * v_mid * 2 * gap / mu_w, "-", "laminar below about 2000")
    add("pumping", "both", "slurry channel Reynolds number",
        fcdi["Water Density"] * v_side * diameter / (mu_w * scale_up["Slurry viscosity ratio"]), "-",
        "laminar below about 2000")

    # -- in-plane IR (Eq. S65) ----------------------------------------------------------------------------------
    ir = rx["In-plane IR drop, V"]
    add("in-plane", "both", "in-plane IR drop", ir, "V", "base case, Eq. S65")
    add("in-plane", "both", "in-plane IR drop as a share of the cell voltage", 100 * ir / fcdi["Voltage Applied"], "%",
        "base case; the scale-up spec flags 5 %")
    conds = {label: _run(results, scenario, True, data_dir) for label, scenario in dl.CONDITIONS}
    top = max(conds, key=lambda k: conds[k].params.PROC["FCDI-BES"]["Current Density"])
    top_rx = conds[top].system.reactor.results
    add("in-plane", "both", f"in-plane IR drop at the highest current density of the four conditions ({top})",
        top_rx["In-plane IR drop, V"], "V", f"condition {top}, Eq. S65")

    # -- limiting current ------------------------------------------------------------------------------------------
    j_lab, j_eff = fcdi["Current Density"], rx["Effective current density, A/m2"]
    reference = (f"{LIMITING_CURRENT_REFERENCE_A_M2:g} A/m2 (Rommerskirchen et al. 2020: feed-limited plateau of 1.4-1.6 A "
                 "on 100 cm2 at 60 g/L NaCl, i.e. 140-160 A/m2; full text p.8)")
    add("limiting", "both", "reference limiting current density over the base laboratory current density",
        LIMITING_CURRENT_REFERENCE_A_M2 / j_lab, "-", reference)
    add("limiting", "both", "reference limiting current density over the base effective current density",
        LIMITING_CURRENT_REFERENCE_A_M2 / j_eff, "-", reference)
    j_top = conds[top].params.PROC["FCDI-BES"]["Current Density"]
    add("limiting", "both", f"reference limiting current density over the highest laboratory current density ({top})",
        LIMITING_CURRENT_REFERENCE_A_M2 / j_top, "-", reference)

    # -- shunt (Eq. S66) and retention (Eq. S67): alone and combined, both configurations --------------------------
    shunt, retention = scale_up["Shunt current fraction"], scale_up["Current density retention"]
    for fragment in ("feed and diluate manifolds", "slurry manifolds"):
        add(fragment, "both", "shunt current fraction (central value)", shunt, "-", "ProcessAssumptions Scale-up")
        add(fragment, "both", "stack supply current over Faradaic current, 1/(1 - s)",
            rx["Stack supply current, A"] / rx["Current, A"], "-", "base case, Eq. S66")
    for rec in (True, False):
        b = base[rec]
        no_shunt = run_model(b.params, include_recovery=rec,
                             overrides={"PROC": {"Scale-up": {"Shunt current fraction": 0.0}}})
        full_retention = run_model(b.params, include_recovery=rec,
                                   overrides={"PROC": {"Scale-up": {"Current density retention": 1.0}}})
        cfg = CONFIG[rec]
        name = "feed and diluate manifolds"
        add(name, cfg, "reactor power, base over base without shunt",
            b.system.reactor.power_kW / no_shunt.system.reactor.power_kW, "-", "base case, s = 0 counterfactual")
        add(name, cfg, "energy intensity change from the shunt alone",
            100 * (b.tea.total_energy_kWh_m3 / no_shunt.tea.total_energy_kWh_m3 - 1), "%", "base against s = 0")
        add(name, cfg, "LCOT change from the shunt alone",
            100 * (b.tea.lcot_usd_m3 / no_shunt.tea.lcot_usd_m3 - 1), "%", "base against s = 0")
        add(name, cfg, "GWP change from the shunt alone",
            100 * (b.kpis[GWP] / no_shunt.kpis[GWP] - 1), "%", "base against s = 0")
        name = "anode resistance growth"
        add(name, cfg, "membrane area, base over base with full retention",
            b.kpis["membrane_area_m2"] / full_retention.kpis["membrane_area_m2"], "-", "= 1 / retention, Eq. S67")
        add(name, cfg, "LCOT change from retention alone",
            100 * (b.tea.lcot_usd_m3 / full_retention.tea.lcot_usd_m3 - 1), "%", "base against retention 1")
        add(name, cfg, "GWP change from retention alone",
            100 * (b.kpis[GWP] / full_retention.kpis[GWP] - 1), "%", "base against retention 1")
        i = ideal[rec]
        add(name, cfg, "LCOT, base case (with losses)", b.tea.lcot_usd_m3, "USD/m3", "scenario base")
        add(name, cfg, "LCOT, laboratory-ideal", i.tea.lcot_usd_m3, "USD/m3", "scenario lab_ideal")
        add(name, cfg, "GWP, base case (with losses)", b.kpis[GWP], "kg CO2 eq/m3", "scenario base")
        add(name, cfg, "GWP, laboratory-ideal", i.kpis[GWP], "kg CO2 eq/m3", "scenario lab_ideal")
        add(name, cfg, "membrane area, base over lab_ideal",
            b.kpis["membrane_area_m2"] / i.kpis["membrane_area_m2"], "-", "base against lab_ideal")
    add("anode resistance growth", "both", "current density retention (central value)", retention, "-",
        "ProcessAssumptions Scale-up")
    # the ends of the sampled retention range (UncertaintyDistributions): LCOT and GWP of the base case there
    bounds = next((spec for label, spec in p.MC_DIST.items() if label.startswith("Current Density Retention")), None)
    if bounds is not None:
        for end in ("min", "max"):
            at_end = run_model(p, include_recovery=True,
                               overrides={"PROC": {"Scale-up": {"Current density retention": bounds[end]}}})
            add("anode resistance growth", "with_recovery", f"current density retention, {end} of the sampled range",
                bounds[end], "-", "UncertaintyDistributions")
            add("anode resistance growth", "with_recovery", f"LCOT at the retention {end} of the sampled range",
                at_end.tea.lcot_usd_m3, "USD/m3", "base case with that retention")
            add("anode resistance growth", "with_recovery", f"GWP at the retention {end} of the sampled range",
                at_end.kpis[GWP], "kg CO2 eq/m3", "base case with that retention")
    add("maldistribution", "both", "current density retention (carries maldistribution)", retention, "-",
        "ProcessAssumptions Scale-up")

    # -- replacement and make-up: the lives the model uses ---------------------------------------------------------
    add("replacement", "both", "membrane service life", p.MAT["Membranes"]["Lifespan"], "yr", "Materials Membranes")
    add("attrition", "both", "GAC (biofilm carbon) replacement interval",
        p.MAT["Granular Activated Carbon (GAC)"]["Biofilm GAC lifespan"], "month", "Materials GAC")

    # -- mechanisms without a computed quantity --------------------------------------------------------------------
    done = {r["mechanism"] for r in rows}
    for name in mechanisms:
        if name not in done:
            rows.append(_row("mechanism", name, "both", "", float("nan"), "", "no computed quantity (see treatment)",
                             by_mechanism[name]))

    rows += _extrapolation(p, base, ideal, data_dir)
    order = {name: i for i, name in enumerate(mechanisms)}
    frame = pd.DataFrame(rows)
    frame["_order"] = [order.get(m, len(order)) if s == "mechanism" else len(order) + 1
                       for s, m in zip(frame["section"], frame["mechanism"])]
    frame = frame.sort_values("_order", kind="stable").drop(columns="_order").reset_index(drop=True)
    return frame


def _extrapolation(p: Parameters, base: dict, ideal: dict, data_dir) -> list:
    """The extrapolation ratios of the TEA revision spec section 5, computed (laboratory -> pilot -> full scale)."""
    fcdi, uc = p.PROC["FCDI-BES"], p.UC
    spec = GridSpec()
    full = run_model(p, include_recovery=True, overrides={"INF": {"WW Flowrate Influent": float(spec.flow_max_m3hr)}})
    # ml/min -> m3/h: x 60 min/h (1 hr / 1 min in seconds), / (1000 ml/L x 1000 L/m3)
    lab_flow = fcdi["Middle Chamber Flowrate"] * (uc["1 hr"] / uc["1 min"]) / (uc["1 L"] * uc["1 m3"])
    pilot, top = float(p.INF["WW Flowrate Influent"]), float(spec.flow_max_m3hr)
    lab_area = MEMBRANES_PER_CELL * fcdi["Membrane Area"]       # the laboratory cell on the Eq. S5 basis (two faces)
    rows = []

    def add(item, quantity, value, unit, basis):
        rows.append(_row("extrapolation", item, "with_recovery", quantity, float(value), unit, basis))

    add("throughput", "laboratory middle-chamber flow", lab_flow, "m3/h", "ProcessAssumptions Middle Chamber Flowrate")
    add("throughput", "pilot base case", pilot, "m3/h", "Influent WW Flowrate Influent")
    add("throughput", "full scale", top, "m3/h", "upper end of the capacity sweep")
    add("throughput", "pilot over laboratory", pilot / lab_flow, "-", "ratio")
    add("throughput", "full scale over laboratory", top / lab_flow, "-", "ratio")
    area_base, area_ideal = base[True].kpis["membrane_area_m2"], ideal[True].kpis["membrane_area_m2"]
    add("membrane area", "laboratory cell (two membranes, each of the membrane area facing the channels)", lab_area,
        "m2", "ProcessAssumptions Membrane Area x 2 (both membranes, the basis of Eq. S5)")
    add("membrane area", "pilot, laboratory-ideal", area_ideal, "m2", "scenario lab_ideal, 10 m3/h")
    add("membrane area", "pilot, base case with losses", area_base, "m2", "scenario base, 10 m3/h")
    add("membrane area", "full scale, base case with losses", full.kpis["membrane_area_m2"], "m2",
        f"scenario base at {top:g} m3/h")
    add("membrane area", "pilot base over laboratory", area_base / lab_area, "-", "ratio")
    add("membrane area", "full scale over laboratory", full.kpis["membrane_area_m2"] / lab_area, "-", "ratio")
    cells = base[True].system.reactor.results["Number of cells"]
    add("membrane area per cell", "laboratory cell (two membranes)", lab_area, "m2",
        "ProcessAssumptions Membrane Area x 2 (both membranes, the basis of Eq. S5)")
    add("membrane area per cell", "pilot, laboratory-ideal (two membranes per cell)",
        area_ideal / ideal[True].system.reactor.results["Number of cells"], "m2", "area / number of cells (Eq. S8)")
    add("membrane area per cell", "pilot, base case with losses (two membranes per cell)", area_base / cells, "m2",
        "area / number of cells (Eq. S8)")
    d05 = io.load_electron_partitioning(data_dir)
    observed = float(d05["duration_days"].max())
    add("time", "observed operation (longest D05 window)", observed, "d", "D05 duration_days")
    add("time", "membrane service life assumed", p.MAT["Membranes"]["Lifespan"], "yr", "Materials")
    add("time", "electrode service life assumed", p.MAT["Graphite Electrodes"]["Lifespan"], "yr", "Materials")
    add("time", "plant life assumed", p.TEA["Plant lifetime"], "yr", "TEAInputs")
    add("time", "plant life over observed operation", p.TEA["Plant lifetime"] * uc["1 year to day"] / observed, "-",
        "ratio")
    return rows


def write(frame: pd.DataFrame, out_dir: Path | None = None) -> Path:
    path = dl.out("loss_ledger") if out_dir is None else Path(out_dir) / dl.out_name("loss_ledger")
    return dl.write_csv(frame, path)
