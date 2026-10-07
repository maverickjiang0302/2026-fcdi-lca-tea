#!/usr/bin/env python
"""Run the whole LCA-FCDI assessment and write every table and figure.

    python LCA-FCDI_D01-D08_RunAll.py             full run: results into 01_Data/02_Processed, figures into
                                                  04_Figures, then the manuscript and SI documents
    python LCA-FCDI_D01-D08_RunAll.py --quick     smoke run with the small sweeps of the SamplingSettings table;
                                                  outputs go to 01_Data/02_Processed/quick_run and
                                                  04_Figures/quick_run (never over the committed files)
    python LCA-FCDI_D01-D08_RunAll.py --verify    rebuilds everything into verify_run (results and figures) and
                                                  compares every regenerated file with the committed one, byte for
                                                  byte (CSV and JSON line endings ignored); the 24 superseded
                                                  tables that no code regenerates are checked to be present and
                                                  read-only here and, by the input validation that runs first,
                                                  against their recorded SHA-256. The manuscript and SI are not
                                                  part of --verify: they are built from the committed results only.
    --jobs N                                      worker processes: a non-zero whole number (default -1 = all
                                                  cores); 0 and anything that is not a number are rejected before
                                                  any file is touched
    --skip-documents                              a full run that writes every result and figure but does not
                                                  build the manuscript and SI (Phase 3: the committed outputs can
                                                  be regenerated before the prose of a new document version exists)
    LCA_FCDI_OUTDIR / LCA_FCDI_FIGOUT             explicit output folders. --quick, --verify and dummy runs refuse
                                                  a folder inside 01_Data/02_Processed or 04_Figures unless it lies
                                                  under a *_run folder (a folder outside the project is fine)

Without the licensed factor table set LCA_FCDI_FACTORS=dummy: the pipeline then runs end to end on the public
all-1.0 dummy table and writes to the dummy_run folders (the results are meaningless by design; the manuscript
and SI are not built and --verify is refused). LCA_FCDI_FACTORS=<path> uses another table and writes to the
altfactors_run folders. Sizes, seed and distributions come from the SamplingSettings reference table. The script
can be started from any working directory; input validation runs first and stops the run on any problem.

The same script runs in the public repository (the deposit built by LCA-FCDI_D01-D08_BuildDeposit.py, recognised by
DEPOSIT_MANIFEST.csv at its root), which carries the calculation code only:
  * a step whose code is not in the copy is skipped and named: the figures (step 7, package fcdibes/figures) and the
    manuscript and SI builders;
  * when the licensed factor table is absent and LCA_FCDI_FACTORS is not set, the run stops before any computation
    and says how to proceed (dummy table, or a table built from the public template);
  * --verify in a copy without committed outputs lists every regenerated file as not compared and exits 0.
The built deposit folder inside 00_Project_Docs is a frozen snapshot: the script refuses to run there.
In the project tree none of this changes what a run computes or writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from fcdibes import datalayer as dl
from fcdibes import io
from fcdibes.analysis import (benchmarks, conditions as conditions_mod, grid as grid_mod,
                              ledger as ledger_mod, scaling as scaling_mod, sensitivity,
                              targets as targets_mod, uncertainty)
from fcdibes.analysis.axes import areal_se_rate_kg_m2_yr
from fcdibes.model import run_scenario
from fcdibes.params import Parameters

DOCUMENT_STEPS = ("make_manuscript", "make_si")     # CODE registry keys, run in this order
RUN = {tag.split("_")[0]: tag for tag in dl.RUN_TAGS}   # run-folder tags of the data layer, by short name
MAX_JOBS = 1024                                     # largest |--jobs| accepted (joblib: -1 = all cores)


def _banner(text: str) -> None:
    print("\n" + "=" * 72 + "\n  " + text + "\n" + "=" * 72, flush=True)


def _stop_workers() -> None:
    """Shut the joblib worker pool down, so that a later run in the same process starts fresh workers
    that inherit that run's environment (run tag, factor table) instead of an earlier run's."""
    try:
        from joblib.externals.loky import get_reusable_executor
        get_reusable_executor().shutdown(wait=True)
    except Exception:                                            # noqa: BLE001
        pass


def _jobs(text: str) -> int:
    """argparse type of --jobs: a non-zero whole number (joblib: n > 0 = that many workers, -1 = all cores, -2 = all
    but one, ...). Zero is rejected here, before any file is touched: joblib rejects it only after the first pipeline
    steps have rewritten committed files."""
    if not re.fullmatch(r"[+-]?[0-9]+", text.strip()):
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number; use --jobs 4, or -1 for all cores")
    value = int(text)
    if value == 0 or abs(value) > MAX_JOBS:
        raise argparse.ArgumentTypeError(f"{text!r} is not usable: give a non-zero whole number between "
                                         f"-{MAX_JOBS} and {MAX_JOBS} (-1 = all cores)")
    return value


def _parse(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=Path(__file__).name, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="small sweeps, for a smoke run (writes to quick_run)")
    ap.add_argument("--verify", action="store_true",
                    help="rebuild into verify_run and compare with the committed outputs")
    ap.add_argument("--jobs", type=_jobs, default=-1,
                    help="worker processes: a non-zero whole number (default -1 = all cores)")
    ap.add_argument("--skip-documents", action="store_true",
                    help="do not build the manuscript and SI after a full run (results and figures are written)")
    return ap.parse_args(argv)


def _refuse_combinations(args: argparse.Namespace) -> None:
    """Stop before any work on a request that cannot be honoured."""
    if args.verify and args.quick:
        raise SystemExit("--verify rebuilds the full set of outputs; it cannot be combined with --quick")
    if args.verify and not dl.is_default_factors():
        raise SystemExit(f"--verify compares against the committed outputs, which were computed with the licensed "
                         f"factor table: it cannot run with {dl.ENV_FACTORS}={dl.factors_choice()!r}")
    try:
        dl.run_tag()                                             # rejects an invalid tag / factor combination
        dl.figout()
        dl.outdir()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


def _refuse_deposit_folder() -> None:
    """The deposit built inside the project's 00_Project_Docs is a frozen snapshot: a run there would write results into
    it. Clone or copy it elsewhere first."""
    if dl.is_deposit_folder_in_project():
        raise SystemExit(f"refusing to run in {dl.ROOT}: it is a built deposit inside a project's "
                         f"{dl.PROJDOC_DIR.name} folder (a frozen snapshot). Clone or copy it elsewhere and run it there")


def _require_factor_table() -> None:
    """Stop before any computation when the default (licensed) factor table is selected but absent from this copy."""
    if not dl.is_default_factors() or dl.ref("factors").is_file():
        return
    raise SystemExit(
        f"the licensed ecoinvent 3.7 factor table is not in this copy ({dl.rel_root(dl.ref('factors'))}).\n"
        "This public repository does not include the licensed ecoinvent 3.7 factor table (TRACI 2.1 scores of "
        "ecoinvent datasets). Either\n"
        f"  - run with {dl.ENV_FACTORS}=dummy: the public all-1.0 dummy table runs everything end to end; every LCA "
        "column is then meaningless, every TEA result is exact; or\n"
        f"  - build your own table from {dl.IN['factors_template']} (one value per row, from the ecoinvent activity "
        "the row names) and either place it at the registered path above (git-ignored by the *Ecoinvent37* "
        f"pattern) or pass its path in {dl.ENV_FACTORS}.\n"
        "Nothing was computed or written.")


def _refuse_committed_overwrite(args: argparse.Namespace) -> None:
    """A quick, verify or non-default-factor run must never write into the committed folders: not into
    01_Data/02_Processed or 04_Figures themselves, and not into a folder inside them unless that folder lies under
    a *_run folder (LCA_FCDI_OUTDIR and LCA_FCDI_FIGOUT may name any folder)."""
    if not (args.quick or args.verify or not dl.is_default_factors()):
        return
    for label, folder, committed in (("results", dl.outdir(), dl.PROC_DIR), ("figures", dl.figout(), dl.FIG_DIR)):
        if dl.inside_committed(folder, committed):
            raise SystemExit(f"refusing to write the {label} of a quick, verify or non-default-factor run into "
                             f"{dl.rel_root(folder)}: it is the committed folder {dl.rel_root(committed)} or lies "
                             f"inside it outside a *_run folder. Unset {dl.ENV_OUTDIR} / {dl.ENV_FIGOUT} or point "
                             "them at a *_run folder or a folder outside the project")


def _describe() -> None:
    info = dl.describe_run()
    print(f"  factor table : {info['mode']} ({dl.rel_root(info['factors_path'])})")
    print(f"  run folder   : {info['run_tag'] or '(committed folders)'}")
    print(f"  results  ->  {dl.rel_root(info['outdir'])}")
    print(f"  figures  ->  {dl.rel_root(info['figout'])}", flush=True)
    if dl.is_deposit():
        print(f"  repository   : public ({dl.DEPOSIT_MARKER} found); project-only steps are skipped by name", flush=True)


# ---------------------------------------------------------------------------------------------------------------
# The pipeline (order and computations are those of the pre-restructure run_all.py)
# ---------------------------------------------------------------------------------------------------------------
def _pipeline(args: argparse.Namespace) -> None:
    S = io.load_sampling()
    dl.outdir().mkdir(parents=True, exist_ok=True)
    if dl.figure_code_present():                     # a public clone has no figure code and gets no figure folder
        dl.figout().mkdir(parents=True, exist_ok=True)

    # -- 1. base case, every scenario, both configurations ------------------
    _banner("1. Scenario runs")
    kpi_rows, results = [], {}
    for scenario in dl.SCENARIOS:
        for recovery in (True, False):
            result = run_scenario(scenario, include_recovery=recovery)
            result.write_csv(dl.scenario_dir())
            kpi_rows.append(result.kpis)
            results[(scenario, recovery)] = result
            print(f"  {scenario:<26} recovery={str(recovery):<5} "
                  f"LCOT ${result.tea.lcot_usd_m3:>9,.2f}/m3   "
                  f"GWP {result.lca.impacts_per_m3['Global warming (kg CO2 eq)']:>8.4g}   "
                  f"Ecotox {result.lca.impacts_per_m3['Ecotoxicity (CTUe)']:>10.4g}")
    kpis = pd.DataFrame(kpi_rows)
    dl.write_csv(kpis, dl.out("scenario_kpis"))

    base = run_scenario("base", include_recovery=True)
    params = Parameters.load("base")

    # Golden values, so a later refactor cannot move a published number quietly.
    golden = {k: v for k, v in base.kpis.items() if isinstance(v, (int, float))}
    dl.out("golden_kpis").write_text(
        json.dumps(golden, indent=2, sort_keys=True), encoding="utf-8")

    # -- 1b. the four measured operating points (Phase 3) -----------------
    _banner("1b. Four measured operating points")
    condition_table = conditions_mod.table(results)
    conditions_mod.write(condition_table)
    shown = condition_table[condition_table["include_recovery"]][
        ["condition", "scenario", "voltage_V", "current_density_A_m2", "faradaic_efficiency_se",
         "removal_efficiency", "areal_rate_kg_m2_yr", "membrane_area_m2", "lcot_usd_m3", "gwp_kg_co2eq_m3"]]
    print(shown.to_string(index=False, float_format=lambda v: f"{v:,.5g}"))

    # -- 1c. the stack and scale-up loss ledger (Phase 3) -----------------
    _banner("1c. Stack and scale-up loss ledger")
    loss_ledger = ledger_mod.table(results)
    ledger_mod.write(loss_ledger)
    print(f"  {len(loss_ledger)} rows: {loss_ledger['mechanism'].nunique()} mechanisms and extrapolation items")

    # -- 2. benchmarks -------------------------------------------------------
    _banner("2. Benchmark technologies")
    band = benchmarks.total_cost_band(params)
    dl.write_csv(band, dl.out("benchmarks"))
    low, high = benchmarks.target_range(params)
    print(band.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    print(f"\n  target band: ${low:,.2f} - ${high:,.2f} /m3")
    print(f"  base case is {base.tea.lcot_usd_m3 / high:,.0f}x the most "
          f"expensive benchmark, {base.tea.lcot_usd_m3 / low:,.0f}x the cheapest")

    # -- 2b. published assessments, harmonized (Phase 3) -------------------
    _banner("2b. Literature benchmarks, harmonized")
    if dl.output_is_pending("literature_harmonized"):
        if dl.ref("literature_benchmarks").is_file():
            raise SystemExit(f"{dl.IN['literature_benchmarks']} is present but still listed in datalayer.PENDING_INPUTS: "
                             "remove it from the tuple (the integration step), then run again")
        print(f"  skipped: {dl.IN['literature_benchmarks']} is a pending input (datalayer.PENDING_INPUTS), delivered "
              "at integration; its harmonized table is not expected from this run", flush=True)
    else:
        harmonized = benchmarks.harmonize_literature(kpis, band)
        benchmarks.write_harmonized(harmonized)
        print(f"  {int((harmonized['row_source'] == 'literature').sum())} literature rows, "
              f"{int((harmonized['row_source'] == 'this work').sum())} rows of this work")

    # -- 3. sensitivity ------------------------------------------------------
    _banner("3. One-at-a-time sensitivity")
    for recovery in (True, False):
        frame = sensitivity.run(params, step=S["oat_step"], include_recovery=recovery, n_jobs=args.jobs)
        sensitivity.write(frame, recovery)
        if recovery:
            print(sensitivity.tornado(frame, "lcot_usd_m3", top_n=8)
                  .round(2).to_string(index=False))

    # -- 4. Monte Carlo ------------------------------------------------------
    _banner("4. Monte Carlo uncertainty")
    n_draws = S["n_draws_quick"] if args.quick else S["n_draws"]
    for distribution in S["distributions"]:
        for recovery in (True, False):
            spec = uncertainty.MCSpec(n_draws=n_draws, seed=S["seed"], distribution=distribution,
                                      include_recovery=recovery, n_jobs=args.jobs)
            draws = uncertainty.run(params, spec)
            uncertainty.write(draws, distribution, recovery)
            # the baseline column of both configurations (fix round 2026-10-06; it was blank without recovery)
            summary = uncertainty.summarize(draws, results[("base", recovery)].kpis)
            dl.write_csv(summary, dl.out("mc_summary", distribution, recovery))
            if distribution == "triangular" and recovery:
                print(summary.round(3).to_string(index=False))
        # Kept as in the pre-restructure run: `draws` is the last draw set of the loop above, the no-recovery draws.
        dl.write_csv(uncertainty.correlations(draws, "lcot_usd_m3"),
                     dl.out("mc_correlations", distribution))

    # -- 5. one-dimensional flowrate sweep -----------------------------------
    _banner("5. Flowrate scaling")
    scaling_spec = scaling_mod.ScalingSpec(
        n_flow=S["scaling_n_flow_quick"] if args.quick else S["scaling_n_flow"],
        n_draws=S["scaling_n_draws_quick"] if args.quick else S["scaling_n_draws"],
        seed=S["seed"],
        n_jobs=args.jobs)
    baseline_curve, draws = scaling_mod.run(params, scaling_spec)
    scaling_mod.write(baseline_curve, draws)
    for recovery in (True, False):
        saturation = scaling_mod.saturation_flow(baseline_curve,
                                                 include_recovery=recovery)
        line = baseline_curve[baseline_curve["include_recovery"] == recovery]
        line = line.sort_values("flow_m3hr")
        first, last = line["lcot_usd_m3"].iloc[0], line["lcot_usd_m3"].iloc[-1]
        print(f"  recovery={str(recovery):<5} LCOT ${first:,.2f} -> ${last:,.2f}/m3 "
              f"({100 * (1 - last / first):.1f}% reduction), "
              f"95% of the reduction achieved by {saturation:,.0f} m3/hr")

    # -- 6. two-dimensional grid --------------------------------------------
    _banner("6. Scale against operating performance")
    grid_spec = grid_mod.GridSpec(
        axis="areal_se_rate",
        n_flow=S["grid_n_flow_quick"] if args.quick else S["grid_n_flow"],
        n_axis=S["grid_n_axis_quick"] if args.quick else S["grid_n_axis"],
        n_jobs=args.jobs)
    frame = grid_mod.run(params, grid_spec)
    grid_mod.write(frame, "areal_se_rate")

    failures = frame["lcot_usd_m3"].isna().sum()
    if failures:
        print(f"  {failures} of {len(frame)} grid points failed to solve")

    base_rate = areal_se_rate_kg_m2_yr(params)
    base_flow = base.system.Qin.flowrate_m3hr
    for target, label, slug in ((high, "most expensive benchmark", "reverse_osmosis"),
                                (low, "cheapest benchmark", "biological_denitrification")):
        crossing = grid_mod.break_even(frame, target)
        dl.write_csv(crossing, dl.out("break_even", slug))
        reachable = crossing["axis_value_required"].dropna()
        if reachable.empty:
            print(f"  ${target:,.2f}/m3 ({label}) is not reached within the swept range")
        else:
            factor = reachable.min() / base_rate
            print(f"  to reach ${target:,.2f}/m3 ({label}): areal rate must rise "
                  f"{factor:,.1f}x above the base {base_rate:.4g} kg Se m-2 yr-1")

    # -- 6b. what each break-even demands of the reactor ---------------------
    #
    # Reported at both ends of the scale range, not just the easiest point.
    # If the required current density barely moves between a pilot and a
    # full-scale plant, that is the thesis of the paper stated as a number.
    _banner("6b. Break-even targets, resolved into current density")
    target_list = targets_mod.cost_targets(band) + targets_mod.impact_targets()

    bases = [("pilot", float(frame["flow_m3hr"].min())),
             ("full_scale", float(frame["flow_m3hr"].max()))]
    tables = []
    for basis, flow in bases:
        table = targets_mod.decomposition(params, frame, target_list,
                                          flow_m3hr=flow)
        table.insert(0, "basis", basis)
        table.insert(1, "basis_flow_m3hr", flow)
        tables.append(table)

    decomposition = pd.concat(tables, ignore_index=True)
    dl.write_csv(decomposition, dl.out("break_even_targets"))

    check = targets_mod.verify(params, decomposition)
    if not check.empty:
        worst = check["relative_error"].max()
        print(f"  round-trip check on {len(check)} (J, FE) pairs: "
              f"worst relative error {worst:.2e}")
        if worst > 1e-9:
            raise SystemExit("areal-rate decomposition failed to round-trip")

    print(f"  base areal rate {base_rate:.4g} kg Se m-2 yr-1 at "
          f"J = {params.PROC['FCDI-BES']['Current Density']:.4g} A m-2, "
          f"FE = {params.PROC['FCDI-BES']['Faradaic Efficiency']:.1%}\n")
    shown = decomposition[[
        "basis", "label", "status", "value_at_base", "threshold",
        "achievable_floor", "rate_factor_vs_base",
        "current_density_A_m2_at_fe_100",
        "current_density_A_m2_at_fe_75",
        "current_density_A_m2_at_fe_50"]]
    print(shown.to_string(index=False, float_format=lambda v: f"{v:,.4g}"))

    # How much of the requirement scale actually removes.
    pilot, full = tables
    merged = pilot.merge(full, on="target", suffixes=("_pilot", "_full"))
    both = merged[merged["rate_factor_vs_base_pilot"].notna()
                  & merged["rate_factor_vs_base_full"].notna()]
    if not both.empty:
        relief = (1 - both["rate_factor_vs_base_full"]
                  / both["rate_factor_vs_base_pilot"])
        print(f"\n  going from {bases[0][1]:,.0f} to {bases[1][1]:,.0f} m3/hr cuts the "
              f"required areal rate by {relief.min():.1%} to {relief.max():.1%} "
              f"across {len(both)} reachable targets")

    # Alternate axes, so the axis choice is made against data rather than guessed.
    if not args.quick:
        for axis_name in dl.ALT_GRID_AXES:
            alt = grid_mod.run(params, grid_mod.GridSpec(
                axis=axis_name, n_flow=S["alt_grid_n_flow"], n_axis=S["alt_grid_n_axis"],
                n_jobs=args.jobs))
            grid_mod.write(alt, axis_name)

    # -- 7. figures ----------------------------------------------------------
    _banner("7. Figures")
    if not dl.figure_code_present():
        print(f"  not in this copy, skipped: {dl.FIGURES_PACKAGE}/ (step 7, the figures; project-only)", flush=True)
        return
    # imported here, not at the top: the figure package is project-only and absent from the public repository
    from fcdibes.figures import flowsheet as flow_fig
    from fcdibes.figures import framework as framework_fig
    from fcdibes.figures import grid as grid_fig
    from fcdibes.figures import results as res_fig
    axis_label = frame.attrs["axis_label"]
    written = list(framework_fig.save(framework_fig.figure(), "fig1_framework"))
    written += flow_fig.save(flow_fig.figure(), "fig2_flowsheet")

    com = dl.read_csv(dl.scenario_file("base", True, "tea_com"))
    reactor_capex = dl.read_csv(dl.scenario_file("base", True, "reactor_capex"))
    sa_frame = dl.read_csv(dl.out("sensitivity", True))
    contrib = dl.read_csv(dl.scenario_file("base", True, "lca_contributions"))
    written += res_fig.save(res_fig.lca_figure(kpis, contrib), "fig3_lca_normalized")
    written += res_fig.save(res_fig.tea_figure(kpis, com, reactor_capex, band), "fig4_cost_structure")
    # Phase 3: the four measured operating points, at their own flow (the pilot base case) and their areal rate at
    # scale, both read from the condition table of step 1b; the base case (3V) is the open circle
    markers = conditions_mod.markers(condition_table, include_recovery=True)
    written += grid_fig.save(
        grid_fig.gwp_figure(frame, axis_label=axis_label, markers=markers),
        "fig5_scale_vs_performance_gwp")
    written += grid_fig.save(
        grid_fig.cost_figure(frame, band, axis_label=axis_label, markers=markers),
        "fig6_scale_vs_performance_lcot")
    written += res_fig.save(res_fig.tornado_figure(sa_frame), "fig7_sensitivity")
    for path in written:
        print("  " + dl.rel_root(path))


def _check_written(args: argparse.Namespace) -> list:
    """Names that a run of this size must have written but that are missing from the run folders."""
    missing = [n for n in dl.expected_outputs(include_alt_grids=not args.quick)
               if not (dl.outdir() / n).is_file()]
    if dl.figure_code_present():                                 # no figure code (public repository): no figures
        missing += [n for n in dl.expected_figures() if not (dl.figout() / n).is_file()]
    return missing


# ---------------------------------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------------------------------
def _build_documents() -> None:
    """Manuscript and SI, built from the committed results. A step whose script is not in this copy is skipped."""
    for key in DOCUMENT_STEPS:
        script = dl.code_file(key)
        if not script.is_file():
            print(f"  not in this copy, skipped: {script.name}", flush=True)
            continue
        started = time.time()
        _banner("8. " + script.name)
        done = subprocess.run([sys.executable, str(script)], cwd=str(dl.CODE_DIR),
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if done.returncode:
            raise SystemExit(f"FAILED: {script.name} (exit {done.returncode})")
        print(f"  ok ({time.time() - started:.0f} s)", flush=True)


# ---------------------------------------------------------------------------------------------------------------
# --verify
# ---------------------------------------------------------------------------------------------------------------
def _same(new: Path, committed: Path) -> bool:
    """Byte-for-byte equality; CSV and JSON files ignore the line-ending style (CRLF or LF)."""
    a, b = new.read_bytes(), committed.read_bytes()
    if new.suffix in (".csv", ".json"):
        a, b = a.replace(b"\r\n", b"\n"), b.replace(b"\r\n", b"\n")
    return a == b


def _is_read_only(path: Path) -> bool:
    return not (path.stat().st_mode & stat.S_IWRITE)


def _verify(started: float) -> int:
    """Compare every regenerated file (this run's folders) with the committed one; returns the exit code."""
    if not dl.PROC_DIR.is_dir():
        print("VERIFY: this copy has no committed results folder (" + dl.rel_root(dl.PROC_DIR) + "); nothing to "
              "compare with. Compare the regenerated files with the article's tables instead.")
        return 0
    deposit, figures_here = dl.is_deposit(), dl.figure_code_present()
    pairs = [(dl.outdir() / n, dl.PROC_DIR / n) for n in dl.expected_outputs()]
    if figures_here:
        pairs += [(dl.figout() / n, dl.FIG_DIR / n) for n in dl.expected_figures()]

    identical, differ, not_regenerated, no_committed = [], [], [], []
    for new, committed in pairs:
        if not new.is_file() or new.stat().st_mtime < started - 5:
            not_regenerated.append(dl.rel_root(new))
        elif not committed.is_file():
            no_committed.append(dl.rel_root(committed))
        elif _same(new, committed):
            identical.append(dl.rel_root(committed))
        else:
            differ.append(dl.rel_root(committed))
    not_compared = []
    if deposit:            # a public repository commits no outputs: a file without a committed copy is listed, not failed
        not_compared, no_committed = no_committed, []
    if not deposit and not identical and not differ and len(no_committed) == len(pairs):
        print("VERIFY FAILED: none of the " + str(len(pairs)) + " committed outputs exists under "
              + dl.rel_root(dl.PROC_DIR) + " / " + dl.rel_root(dl.FIG_DIR) + "; run the full pipeline first")
        return 1

    # Committed tables that no code regenerates (scenario 'lci_graphite_low'): carried over, checked here for
    # presence and the read-only attribute; their content is checked against the recorded SHA-256 by the input
    # validation that main() runs before the pipeline.
    orphans_missing, orphans_writable = [], []
    for name in (() if deposit else dl.ORPHANS["files"]):         # the superseded tables are project-only
        path = dl.orphan_file(name)
        if not path.is_file():
            orphans_missing.append(dl.rel_root(path))
        elif not _is_read_only(path):
            orphans_writable.append(dl.rel_root(path))

    # Anything else committed under the processed / figure folders is unknown to the registry: report, do not fail.
    known = {(dl.PROC_DIR / n).resolve() for n in dl.expected_outputs()}
    known |= {dl.orphan_file(n).resolve() for n in dl.ORPHANS["files"]}
    known |= {(dl.FIG_DIR / n).resolve() for n in dl.expected_figures()}
    unregistered = []
    for base in (dl.PROC_DIR, dl.FIG_DIR):
        for path in sorted(base.rglob("*")):
            rel_parts = path.relative_to(base).parts
            if path.is_file() and path.resolve() not in known and not set(rel_parts) & set(dl.RUN_TAGS) \
                    and dl.run_tag() not in rel_parts:
                unregistered.append(dl.rel_root(path))

    failures = ([f"{p}: differs from the committed file" for p in differ]
                + [f"{p}: not regenerated by this run" for p in not_regenerated]
                + [f"{p}: no committed copy to compare with" for p in no_committed]
                + [f"{p}: committed orphan is missing" for p in orphans_missing]
                + [f"{p}: committed orphan is writable (mark it read-only)" for p in orphans_writable])
    if deposit:
        print(f"\nVERIFY: {len(identical)} of {len(pairs)} regenerated files identical to the committed outputs; "
              f"{len(not_compared)} not compared (no committed copy in this public repository)")
        if not_compared:
            print("  not compared: " + ", ".join(not_compared[:6])
                  + (f" ... and {len(not_compared) - 6} more" if len(not_compared) > 6 else ""))
    else:
        print(f"\nVERIFY: {len(identical)} of {len(pairs)} regenerated files identical to the committed outputs; "
              f"{len(dl.ORPHANS['files']) - len(orphans_missing)} of {len(dl.ORPHANS['files'])} superseded tables "
              f"carried over in {dl.ORPHANS['container']} (present"
              f"{'' if orphans_writable else ', read-only'})")
    if not figures_here:
        print(f"  not compared: the {len(dl.expected_figures())} figure files (the figure code is not in this copy)")
    if unregistered:
        print(f"  not in the registry ({len(unregistered)} committed files, not compared): "
              + ", ".join(unregistered))
    if failures:
        shown = failures[:40]
        print("VERIFY FAILED (" + str(len(failures)) + " problems):\n  " + "\n  ".join(shown)
              + (f"\n  ... and {len(failures) - len(shown)} more" if len(failures) > len(shown) else ""))
        return 1
    print(f"VERIFY PASSED: {len(identical)} regenerated files identical to the committed outputs"
          + (f"; {len(not_compared)} not compared" if not_compared else ""))
    return 0


# ---------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    args = _parse(argv)
    _refuse_deposit_folder()
    _refuse_combinations(args)
    _require_factor_table()                                      # before any computation or file is touched
    _stop_workers()
    # A dummy or alternative factor table gets its own folder from the data layer (dummy_run / altfactors_run).
    tag = RUN["verify"] if args.verify else (RUN["quick"] if args.quick and dl.is_default_factors() else None)
    try:
        with dl.run_context(tag=tag):
            _refuse_committed_overwrite(args)
            started = time.time()
            _banner("0. Validation and run settings")
            from fcdibes import validation
            validation.validate_all(strict=True)
            _describe()

            _pipeline(args)

            missing = _check_written(args)
            if missing:
                raise SystemExit("the run did not write " + str(len(missing)) + " expected files: "
                                 + ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else ""))

            if args.verify:
                return _verify(started)
            if args.skip_documents:
                print("\n  --skip-documents: the manuscript and SI were not built (results and figures are written).")
            elif dl.is_committed_run() and dl.is_default_factors():
                _build_documents()
            else:
                print("\n  manuscript and SI are built only by a full run with the licensed factor table into the "
                      "committed folders: skipped.")
                absent = [dl.code_file(key).name for key in DOCUMENT_STEPS if not dl.code_file(key).is_file()]
                if absent:
                    print("  not in this copy, skipped: " + ", ".join(absent), flush=True)
            figures = f", figures in {dl.rel_root(dl.figout())}" if dl.figure_code_present() else ""
            print(f"\nDone in {time.time() - started:.0f}s. Results in {dl.rel_root(dl.outdir())}{figures}.")
            return 0
    finally:
        _stop_workers()


if __name__ == "__main__":
    raise SystemExit(main())
