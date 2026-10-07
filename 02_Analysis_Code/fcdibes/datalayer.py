"""LCA-FCDI data layer: the single registry of folders, file names and versions, plus BOM-safe CSV I/O.

Every folder, file name and version of the project is defined in this module and nowhere else. A version
bump is a one-line change here; no other module types a file name, a folder name or root arithmetic
(`parents[..]`). Import has NO side effects: no folder is created, no environment variable is read and no
file is touched. Run folders and the factor-table choice are resolved by functions at call time.

    ROOT/                              = CODE_DIR.parent (the project root, same layout in the deposit)
      00_Project_Docs/                 PROJDOC_DIR   reports, plans, guides, generated data dictionary
      01_Data/                         DATA_DIR      Dataset_ID_Legend.md
        01_Raw/SourceWorkbooks/        RAW_DIR       the two authoring workbooks (read-only copies)
        02_Processed/                  PROC_DIR      every output written by code (+ *_run/ run folders)
        03_Reference/                  REF_DIR       every input (authored tables, catalogues, factors)
      02_Analysis_Code/                CODE_DIR      package fcdibes/, tests/, entry scripts, tools
      03_Manuscript/                   MANUSCRIPT_DIR  Data_Figure_Crosswalk.csv
        01_Drafts/                     DOC_DIR       manuscript and SI .docx (versioned)
      04_Figures/                      FIG_DIR       figures (+ *_run/ run folders)
      05_Presentations/  06_Meeting_Notes/           (empty-by-design folders)

CODE_DIR = Path(__file__).resolve().parents[1] is the ONLY `parents[..]` expression of the project;
ROOT = CODE_DIR.parent. Other modules import ROOT / CODE_DIR from here.

REQUIRED_ENV: none. All environment variables are optional overrides, read at call time (never at import):

    LCA_FCDI_FACTORS   '' (default) = the licensed factor table; 'dummy' = the public all-1.0 dummy table;
                       anything else = a path to an alternative table (absolute, or relative to the current
                       directory, ROOT or CODE_DIR). A non-default choice routes every output to
                       dummy_run/ or altfactors_run/ so committed results can never be overwritten.
    LCA_FCDI_RUN       run tag (must end in '_run', e.g. verify_run, quick_run). Outputs go to
                       01_Data/02_Processed/<tag>/... and 04_Figures/<tag>/... (docout() resolves 01_Drafts/<tag>/, but the two document
                       builders refuse to run under a run tag, so nothing is ever written there).
                       An explicit tag beats the tag implied by the factor choice; 'verify_run' with a
                       non-default factor table is refused (verify compares against committed results).
    LCA_FCDI_OUTDIR    explicit output folder for processed files (used as given; no tag level is added).
    LCA_FCDI_FIGOUT    explicit output folder for figures.
                       With a non-default factor table an explicit folder inside the committed PROC_DIR /
                       FIG_DIR (i.e. not inside a *_run folder) is refused.
    Workers (joblib/loky) and subprocesses inherit the variables; use set_run()/run_context()/run_env()
    rather than module globals so that every process resolves the same folders.

API (all paths are pathlib.Path unless stated; all names are str)
------------------------------------------------------------------------------------------------------
Constants
  PROJECT ('LCA-FCDI'); CODE_DIR, ROOT, DATA_DIR, RAW_DIR, PROC_DIR, REF_DIR, FIG_DIR, MANUSCRIPT_DIR, DOC_DIR,
  PROJDOC_DIR, SUBMISSION_DIR, REVISION_DIR, PRESENTATIONS_DIR, MEETING_DIR, LAYOUT_DIRS (tuple of all);
  ENV_FACTORS, ENV_RUN, ENV_OUTDIR, ENV_FIGOUT (variable names); REQUIRED_ENV (); OPTIONAL_ENV {name: text};
  RUN_TAGS ('verify_run','quick_run','dummy_run','altfactors_run'); DATASET_IDS ('D01'..'D09').
  SCENARIOS (the nine run scenario ids; Phase 3 added cond_2v, cond_2v_n, cond_3v_n and lab_ideal),
  CONDITIONS ((label, scenario) of the four measured operating points, labels 3V, 2V, 2V+N, 3V+N; 3V = base),
  ORPHAN_SCENARIOS ('lci_graphite_low',), SCENARIO_TABLES (the 12 per-scenario tables), DISTRIBUTIONS
  ('triangular','uniform'), GRID_AXES / ALT_GRID_AXES, BREAK_EVEN_SLUGS ('reverse_osmosis','biological_denitrification').
  MANUSCRIPT_VERSION = 'v06', SI_VERSION = 'v06' (document revision of 2026-10-06; v05 = Phase 3).

Registries (dict key -> file name; the names equal the approved mapping's new names)
  IN        every input in 01_Data/03_Reference. Keys: unit_conversions influent process_assumptions materials
            equipment_general tea_inputs prices pumps centrifuges screens ec_mixers electrocultivation
            foreground_processes normalization uncertainty benchmarks factors (LICENSED, matched by
            '**/*Ecoinvent37*') factors_template factors_dummy flows (FlowDictionary) sampling
            (SamplingSettings) scenarios (YAML) electron_partitioning; Phase 3 (2026-10-05): cpi (CPI-U price
            index, D08), literature_benchmarks (published LCA/TEA values compared, D08), scaleup_ledger (D09, the
            stack and scale-up loss ledger: documentation, the model reads none of it). Literature confirmation
            (2026-10-06): process_assumptions v03, uncertainty v03, literature_benchmarks v02, scaleup_ledger v02
            (text cells restated from the full texts of the cited papers; every value identical).
            IN_ALIASES maps the OLD file stems (background_cfs, normalization_traci_us2008, uncertainty_inputs,
            se_treatment_benchmarks) and a few synonyms (flow_dictionary, sampling_settings,
            scenario_definitions, distributions) to the keys; ref() accepts both.
  PENDING_INPUTS  IN keys whose file is delivered by a later step of the current revision (Phase 3: the literature
            table, written by the prose writer and merged at integration). While a key is listed and its file is
            absent, validation reports it as pending instead of missing and the runner skips the one step that reads
            it; expected_outputs() leaves that step's output out. The integrator empties the tuple when the file is
            in place, which makes every check strict again. A listed file that is present is validated in full.
  SUPERSEDED  superseded input versions, key -> file name in 03_Reference (scenarios_v01: the D06 scenario file v01,
            replaced by v02 on 2026-10-05 because its notes quoted two ecoinvent scores; Phase 3, 2026-10-05:
            equipment_general_v01, process_assumptions_v01, scenarios_v02, uncertainty_v01, benchmarks_v01; literature
            confirmation of 2026-10-06: process_assumptions_v02, uncertainty_v02, literature_benchmarks_v01,
            scaleup_ledger_v01, whose text cells were restated from the full texts, every value identical). They
            stay on disk, are never edited and are NEVER loaded (no IN key names them); SUPERSEDED_BY[key] is the IN
            key that replaced each; superseded(key) is the path.
  RAW_IN    keys 'inputs' and 'metafile' -> the renamed workbook copies in 01_Raw/SourceWorkbooks
            (aliases inputs_v1, metafile_corrected, inputs_workbook, metafile_workbook are accepted).
  RAW_LABEL same keys -> the OLD workbook file names. These are the values of the licensed table's
            source_workbook column and must never change.
  OUT       every processed output. Static names: scenario_dir (container FOLDER of the per-scenario tables),
            scenario_kpis, golden_kpis (.json), benchmarks, scaling_baseline, scaling_bands,
            break_even_targets; Phase 3: conditions (the four measured operating points), loss_ledger (the D09
            ledger with the computed loss quantities), literature_harmonized (published values harmonised to this
            work's units, with this work's rows). Functions: sensitivity(recovery), monte_carlo(dist, recovery),
            mc_summary(dist, recovery), mc_correlations(dist), grid(axis), break_even(slug).
            recovery is a bool (or 'with_recovery'/'no_recovery'); dist in DISTRIBUTIONS; axis in GRID_AXES;
            slug in BREAK_EVEN_SLUGS. Unknown values raise KeyError.
  ORPHANS   dict(container=<folder name>, files=<the 24 superseded file names>): read-only copies that no code
            regenerates (scenario 'lci_graphite_low'). Never written, never in a run folder.
  FIG       key -> (figure id, dataset range or None, description) for fig1_flowsheet, fig2_lca_normalized,
            fig3_cost_structure, fig4_scale_vs_performance, fig5_sensitivity (the manuscript figures) and
            figS1_framework (Fig. S1 of the Supplementary Material). FIG_VERSION[key] = 'v01'. FIG_EXTS = png, pdf.
            SUPERSEDED_FIGURES: the seven figures of v06, kept on disk read-only (documents v07).
  DOC       key -> file name: manuscript, si (versioned by the constants above), reference_list,
            data_dictionary, model_notes, publishing, discovery_report, verification_report, data_sharing,
            science_revision, documents_v06, legend, crosswalk. DOC_LOCATION[key] is the folder of each; DOC_VERSIONS
            lists the registered versions of manuscript and si (v01..v06; the last is the current one). Phase 3 registers
            reference_list v02, data_dictionary v02 (D01-D09) and model_notes v02 (D01-D09); their v01 files stay on
            disk unchanged (DOC_SUPERSEDED); the literature confirmation of 2026-10-06 registers reference_list v03
            (the DOI of yu2016 added; v02 superseded).
  CODE      key -> entry-script / tool file names in CODE_DIR (run_all, make_manuscript, make_si,
            manuscript_text, si_equations, extract_factors, write_factors_superseded, build_data_dictionary,
            migrate_inputs, refactor_gate, compare_trees, build_deposit). Their names are hyphenated, so load them
            with import_script(key) (importlib) or code_file(key).
  REPO      key -> repo files in CODE_DIR (readme, license, citation, gitignore, gitattributes, requirements,
            pyproject, pytest_ini, environment, declared_substitutions).
  DEPOSIT   the public repository (guide v02 section 11): an explicit ALLOW-LIST of what is shared.
            DEPOSIT['inputs'] = IN keys (every registered input EXCEPT the licensed factor table 'factors');
            DEPOSIT['package'] = package files relative to CODE_DIR (fcdibes/ without fcdibes/figures/);
            DEPOSIT['code'] = CODE keys (run_all only). Everything else is project-only: the licensed table, the
            superseded inputs, 01_Raw, every file of 02_Processed (result files can be combined with the public
            inventory to recover licensed factors), 03_Manuscript, 04_Figures, 00_Project_Docs, the figure package,
            the document builders, every other tool, tests/ and the project's repo files.
            DEPOSIT_VERSION ('v06'), deposit_name(version) / deposit_dir(version) (00_Project_Docs/
            LCA-FCDI_ZenodoDeposit_vNN, built by CODE['build_deposit'], never run in place), deposit_files() ->
            [(published path, source path, registry source)], DEPOSIT_MARKER (DEPOSIT_MANIFEST.csv at the deposit
            root): is_deposit() is True in a public clone (validation then uses its 'deposit' scope).
            is_deposit_folder_in_project() is True for the built copy inside 00_Project_Docs, where nothing may run.

Paths
  ref(key, data_dir=None)        input file; data_dir points at a copied reference folder (tests): the
                                 registered file name is resolved inside it. ref_dir(data_dir=None) = folder.
  raw(key) / raw_label(key)      raw workbook path / its OLD file name.
  proc(key=None, *args)          COMMITTED output path (always the real PROC_DIR); proc() = PROC_DIR.
  out(key=None, *args)           THIS RUN's output path (run-folder aware); out() = outdir().
                                 Both take an OUT key plus the arguments of its function, e.g.
                                 out('sensitivity', True), out('grid', 'areal_se_rate'), out('scenario_dir').
  out_name(key, *args)           the bare file name (str).
  scenario_prefix(scenario, recovery) -> '<scenario>_<with|no>_recovery'
  scenario_file_name(scenario, recovery, table) -> '<scenario>_<with|no>_recovery__<table>.csv'
  scenario_dir(committed=False)  the container folder (this run's, or the committed one).
  scenario_file(scenario, recovery, table, committed=False)   file inside the container.
  orphan_dir() / orphan_file(name)   the read-only superseded container and a file in it.
  fig_stem(key) / fig_name(key, ext='png')   registered figure stem / file name.
  fig_file(key, ext='png')       figure path in THIS RUN's figure folder; fig_committed(key, ext) = FIG_DIR.
  doc_file(key, version=None)    committed document path (version only for manuscript / si);
  doc_out(key, version=None)     path in this run's document folder (01_Drafts/<tag>/ for run tags).
  code_file(key), repo_file(key), import_script(key)
  superseded(key)                path of a superseded input (registry SUPERSEDED; never loaded by the model).
  rel_root(path) -> str          POSIX path relative to ROOT (falls back to str(path) outside ROOT).
  inside_committed(path, base)   True when path is base or lies inside it without a *_run folder on the way.
  is_run_location(path, is_file=True)   True for a scratch target: outside ROOT, or under a *_run folder of ROOT
                                 (the rule of the tools that write a table or a folder on request).
  expected_outputs(scenarios=SCENARIOS, include_alt_grids=True) -> list of names relative to a processed root
                                 (the output of a step whose input is still in PENDING_INPUTS is left out)
  expected_figures() -> list of figure file names (png + pdf)

Run resolution (call time)
  factors_choice() -> str; factors_mode() -> 'default'|'dummy'|'custom'; is_default_factors() -> bool
  factors_path(data_dir=None)    the factor table to read: licensed file, dummy file, or the custom path.
  run_tag() -> str               '' for a committed run, else the folder tag (see LCA_FCDI_RUN).
  outdir() / figout() / docout() folders of this run.       is_committed_run() -> bool
  describe_run() -> dict         everything above in one dict, for logging.
  set_run(tag) -> previous       sets/clears LCA_FCDI_RUN in os.environ (inherited by workers).
  run_context(tag=None, factors=None, outdir=None, figout=None)   context manager; None leaves a variable
                                 unchanged, '' clears it; restores everything on exit (also on error).
  run_env(tag=None, factors=None, outdir=None, figout=None, base=None) -> dict   environment copy for subprocess.

CSV / text I/O (UTF-8 with BOM everywhere; csv_bom_safe note below)
  write_csv(df, path, **kw)      ALWAYS writes utf-8-sig, index=False by default, creates parent folders,
                                 returns the Path; refuses raw workbooks, the orphan container, the
                                 licensed factor file and the superseded input versions (is_protected(path)),
                                 also under another name (a hard link, same_file); rejects the mode= and
                                 compression= keywords (plain new file only). Line terminator = pandas default.
  is_protected(path) -> bool     True for the raw folder, the orphan container, the licensed table, the superseded
                                 input versions (SUPERSEDED, Phase 3: they never change) and any existing file
                                 that is a hard link to one of the protected registered files.
  same_file(a, b) -> bool        two names of one existing file (os.path.samefile; False if either is missing).
  read_csv(path, **kw)           pandas.read_csv with encoding='utf-8-sig' and DEFAULT float parsing
                                 (never pass number tokens through float(): the pandas default parser and float()
                                 differ by 1 ULP in 98 of 1,197 input tokens, which would move KPIs).
  csv_rows(path) -> list[dict]   stdlib csv reader, string values, BOM accepted.
  read_csv_text(path) -> str     decoded text without BOM (line endings normalised to \\n).
  strip_bom(text), require_file(path, what='input file')
  csv_bom_safe: every reader of a project CSV must open it with encoding 'utf-8-sig' (accepts files with and
  without BOM); csv.DictReader on a plain 'utf-8' handle would see the key '\\ufeffflow' instead of 'flow'.
  pandas.read_csv is BOM-safe by itself; read_csv() just makes the encoding explicit.

Registry hygiene
  parse_name(name) -> dict|None  splits a registered name (kind, dataset range, type, description, version, ext).
  is_licensed_name(name)         True for names matching '*Ecoinvent37*' (excluded from every deposit).
  registry_problems() -> list    empty when names are unique, follow the naming pattern and are consistent.
  python -m fcdibes.datalayer    prints describe_run(), registry problems and missing registered files.
"""
from __future__ import annotations

import contextlib
import csv
import fnmatch
import importlib
import os
import re
import sys
from pathlib import Path

PROJECT = "LCA-FCDI"

# ---------------------------------------------------------------------------------------------- layout
CODE_DIR = Path(__file__).resolve().parents[1]          # the ONLY parents[..] expression of the project
ROOT = CODE_DIR.parent
DATA_DIR = ROOT / "01_Data"
RAW_DIR = DATA_DIR / "01_Raw" / "SourceWorkbooks"
PROC_DIR = DATA_DIR / "02_Processed"
REF_DIR = DATA_DIR / "03_Reference"
FIG_DIR = ROOT / "04_Figures"
MANUSCRIPT_DIR = ROOT / "03_Manuscript"
DOC_DIR = MANUSCRIPT_DIR / "01_Drafts"
SUBMISSION_DIR = MANUSCRIPT_DIR / "02_Submission"
REVISION_DIR = MANUSCRIPT_DIR / "03_Revision"
PROJDOC_DIR = ROOT / "00_Project_Docs"
PRESENTATIONS_DIR = ROOT / "05_Presentations"
MEETING_DIR = ROOT / "06_Meeting_Notes"
LAYOUT_DIRS = (PROJDOC_DIR, RAW_DIR, PROC_DIR, REF_DIR, CODE_DIR, DOC_DIR, SUBMISSION_DIR, REVISION_DIR,
               FIG_DIR, PRESENTATIONS_DIR, MEETING_DIR)

DATASET_IDS = tuple(f"D{i:02d}" for i in range(1, 10))     # D09 (Phase 3): the stack and scale-up loss ledger

# ---------------------------------------------------------------------------------------------- environment
ENV_FACTORS = "LCA_FCDI_FACTORS"
ENV_RUN = "LCA_FCDI_RUN"
ENV_OUTDIR = "LCA_FCDI_OUTDIR"
ENV_FIGOUT = "LCA_FCDI_FIGOUT"
REQUIRED_ENV = ()
OPTIONAL_ENV = {
    ENV_FACTORS: "'' = licensed factor table | 'dummy' = public all-1.0 table | path of an alternative table",
    ENV_RUN: "run tag ending in '_run' (verify_run, quick_run, ...): outputs go to <PROC|FIG|DOC>/<tag>/",
    ENV_OUTDIR: "explicit processed-output folder (used as given)",
    ENV_FIGOUT: "explicit figure-output folder (used as given)",
}
RUN_TAGS = ("verify_run", "quick_run", "dummy_run", "altfactors_run")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,22}_run$")

# ---------------------------------------------------------------------------------------------- vocabularies
SCENARIOS = ("base", "cond_2v", "cond_2v_n", "cond_3v_n", "lab_ideal", "lci_graphite_battery_anode",
             "nitrate_competition", "window_full_41d", "legacy_2v")
# The four measured operating points of Riveros et al. 2026 (Water Res. 303, 126244), as (label, scenario id). The
# labels are the owner's, verbatim; 3V is the manuscript base case (DESIGN_phase3 section 3.2).
CONDITIONS = (("3V", "base"), ("2V", "cond_2v"), ("2V+N", "cond_2v_n"), ("3V+N", "cond_3v_n"))
BASE_CONDITION = "3V"
ORPHAN_SCENARIOS = ("lci_graphite_low",)
SCENARIO_TABLES = ("capex", "kpis", "lca_contributions", "lca_impacts", "lca_inventory", "opex", "power",
                   "reactor_capex", "streams", "tea_capital", "tea_com", "tea_metrics")
DISTRIBUTIONS = ("triangular", "uniform")
GRID_AXES = ("areal_se_rate", "current_density", "faradaic_efficiency", "membrane_price")
ALT_GRID_AXES = ("current_density", "faradaic_efficiency", "membrane_price")   # skipped by --quick
BREAK_EVEN_SLUGS = ("reverse_osmosis", "biological_denitrification")

_DIST_CAMEL = {"triangular": "Triangular", "uniform": "Uniform"}
_AXIS_CAMEL = {"areal_se_rate": "ArealSERate", "current_density": "CurrentDensity",
               "faradaic_efficiency": "FaradaicEfficiency", "membrane_price": "MembranePrice"}
_SLUG_CAMEL = {"reverse_osmosis": "ReverseOsmosis", "biological_denitrification": "BiologicalDenitrification"}
_SCENARIO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _camel(table: dict, value, what: str) -> str:
    try:
        return table[value]
    except (KeyError, TypeError):
        raise KeyError(f"unknown {what} {value!r}; known: {sorted(table)}") from None


def _recovery(recovery) -> bool:
    """bool, or the old tags 'with_recovery' / 'no_recovery' (also 'with' / 'no')."""
    if isinstance(recovery, str):
        if recovery in ("with_recovery", "with"):
            return True
        if recovery in ("no_recovery", "no"):
            return False
        raise KeyError(f"unknown recovery flag {recovery!r}; use True/False, 'with_recovery' or 'no_recovery'")
    if recovery is None:
        raise TypeError("recovery must be a bool (or 'with_recovery' / 'no_recovery'), not None")
    return bool(recovery)


def _with_no(recovery) -> str:
    return "With" if _recovery(recovery) else "No"


# ---------------------------------------------------------------------------------------------- inputs
IN = dict(
    unit_conversions="LCA-FCDI_D02_Reference_UnitConversions_v01.csv",
    influent="LCA-FCDI_D02_Reference_Influent_v01.csv",
    process_assumptions="LCA-FCDI_D02_Reference_ProcessAssumptions_v03.csv",    # v03 2026-10-06: Scale-up texts confirmed
    materials="LCA-FCDI_D02_Reference_Materials_v01.csv",
    equipment_general="LCA-FCDI_D02_Reference_EquipmentGeneral_v02.csv",        # v02 2026-10-05: reactor f_BM 1.12 (H2A)
    tea_inputs="LCA-FCDI_D02_Reference_TEAInputs_v01.csv",
    prices="LCA-FCDI_D02_Reference_Prices_v01.csv",
    pumps="LCA-FCDI_D03_Reference_PumpCatalogue_v01.csv",
    centrifuges="LCA-FCDI_D03_Reference_CentrifugeCatalogue_v01.csv",
    screens="LCA-FCDI_D03_Reference_ScreenCatalogue_v01.csv",
    ec_mixers="LCA-FCDI_D03_Reference_ECMixerCatalogue_v01.csv",
    electrocultivation="LCA-FCDI_D04_Reference_ElectrocultivationInventory_v01.csv",
    foreground_processes="LCA-FCDI_D04_Reference_ForegroundProcesses_v01.csv",
    normalization="LCA-FCDI_D04_Reference_TRACINormalizationUS2008_v01.csv",
    uncertainty="LCA-FCDI_D07_Reference_UncertaintyDistributions_v03.csv",      # v03 2026-10-06: two rows' texts confirmed
    benchmarks="LCA-FCDI_D08_Reference_SeTreatmentBenchmarks_v02.csv",          # v02 2026-10-05: as reported (2010 USD)
    factors="LCA-FCDI_D01_Reference_TRACIFactorsEcoinvent37_v01.csv",            # LICENSED (never in a deposit)
    scenarios="LCA-FCDI_D06_Reference_ScenarioDefinitions_v03.yaml",             # v03 2026-10-05: four conditions, lab_ideal
    electron_partitioning="LCA-FCDI_D05_Reference_ElectronPartitioning_v01.csv",   # transcription of workbook sheets
    factors_template="LCA-FCDI_D01_Reference_TRACIFactorsTemplate_v01.csv",      # public, 270 rows, factor blank
    factors_dummy="LCA-FCDI_D01_Reference_TRACIFactorsDummy_v01.csv",            # public, 270 rows, factor 1.0
    flows="LCA-FCDI_D01_Reference_FlowDictionary_v01.csv",                       # validation only
    sampling="LCA-FCDI_D07_Reference_SamplingSettings_v01.csv",
    cpi="LCA-FCDI_D08_Reference_CPIU_v01.csv",                                   # Phase 3: CPI-U (BLS CUUR0000SA0)
    literature_benchmarks="LCA-FCDI_D08_Reference_LiteratureBenchmarks_v02.csv",  # v02 2026-10-06: full texts confirmed
    scaleup_ledger="LCA-FCDI_D09_Reference_ScaleUpLossLedger_v02.csv",           # v02 2026-10-06: evidence texts confirmed
)
# Registered inputs that a later step of the current revision delivers (see the module doc). Phase 3: the literature
# table is written by the prose writer and merged at integration; the integrator empties this tuple.
PENDING_INPUTS = ()                                                # emptied at integration (Phase 3)
IN_ALIASES = dict(
    background_cfs="factors",
    normalization_traci_us2008="normalization",
    uncertainty_inputs="uncertainty",
    se_treatment_benchmarks="benchmarks",
    flow_dictionary="flows",
    sampling_settings="sampling",
    scenario_definitions="scenarios",
    distributions="uncertainty",
)
LICENSED_PATTERN = "*Ecoinvent37*"          # the deposit / .gitignore rule is '**/*Ecoinvent37*'

# Superseded input versions: kept in 03_Reference (inputs are never edited in place or deleted), never loaded (no IN
# key names them), never deposited. key -> file name; SUPERSEDED_BY[key] = the IN key whose newer version replaced it.
SUPERSEDED = dict(
    # v01 notes and comments quoted two ecoinvent scores; v02 (2026-10-05) has the same keys, values, order and
    # parameters, with the notes of 'base' and 'lci_graphite_battery_anode', the comments and two meta paths reworded.
    scenarios_v01="LCA-FCDI_D06_Reference_ScenarioDefinitions_v01.yaml",
    # Phase 3 (2026-10-05, science revision): v02 of each adds or changes rows; every other row is byte-identical.
    equipment_general_v01="LCA-FCDI_D02_Reference_EquipmentGeneral_v01.csv",          # reactor f_BM 2 (assumption)
    process_assumptions_v01="LCA-FCDI_D02_Reference_ProcessAssumptions_v01.csv",      # no group Scale-up
    scenarios_v02="LCA-FCDI_D06_Reference_ScenarioDefinitions_v02.yaml",              # five scenarios
    uncertainty_v01="LCA-FCDI_D07_Reference_UncertaintyDistributions_v01.csv",        # 17 sampled inputs
    benchmarks_v01="LCA-FCDI_D08_Reference_SeTreatmentBenchmarks_v01.csv",            # escalated with 1.07^16 by hand
    # Literature confirmation (2026-10-06): the text cells of these four were restated from the full texts of the cited
    # papers in the next version (every numeric value identical; the four Jensen rows of the literature table name the
    # SI v06 benchmark table); hashed by the validation, never loaded.
    process_assumptions_v02="LCA-FCDI_D02_Reference_ProcessAssumptions_v02.csv",      # Scale-up rows: sweep statements
    uncertainty_v02="LCA-FCDI_D07_Reference_UncertaintyDistributions_v02.csv",        # retention / shunt rows: sweep statements
    literature_benchmarks_v01="LCA-FCDI_D08_Reference_LiteratureBenchmarks_v01.csv",  # abstracts only; [UNCONFIRMED] rows
    scaleup_ledger_v01="LCA-FCDI_D09_Reference_ScaleUpLossLedger_v01.csv",            # five rows with [UNCONFIRMED] evidence
)
SUPERSEDED_BY = dict(scenarios_v01="scenarios", equipment_general_v01="equipment_general",
                     process_assumptions_v01="process_assumptions", scenarios_v02="scenarios",
                     uncertainty_v01="uncertainty", benchmarks_v01="benchmarks",
                     process_assumptions_v02="process_assumptions", uncertainty_v02="uncertainty",
                     literature_benchmarks_v01="literature_benchmarks", scaleup_ledger_v01="scaleup_ledger")

RAW_IN = dict(
    inputs="LCA-FCDI_D01-D08_2026-04-10_InputsWorkbook.xlsx",
    metafile="LCA-FCDI_D01-D08_2026-07-07_CorrectedMetafileScaleUp.xlsx",
)
RAW_LABEL = dict(
    inputs="FCDI-BES inputs (1).xlsx",
    metafile="07-07-26 Corrected Metafile Scale-up.xlsx",
)
RAW_ALIASES = dict(inputs_v1="inputs", metafile_corrected="metafile",
                   inputs_workbook="inputs", metafile_workbook="metafile")

# ---------------------------------------------------------------------------------------------- outputs
OUT = dict(
    scenario_dir="LCA-FCDI_D01-D06_Processed_ScenarioResults_v01",               # container FOLDER
    scenario_kpis="LCA-FCDI_D01-D06_Processed_ScenarioKPIs_v01.csv",
    golden_kpis="LCA-FCDI_D01-D06_Processed_GoldenKPIs_v01.json",
    benchmarks="LCA-FCDI_D02-D08_Processed_Benchmarks_v01.csv",
    sensitivity=lambda recovery: f"LCA-FCDI_D01-D06_Processed_Sensitivity{_with_no(recovery)}Recovery_v01.csv",
    monte_carlo=lambda dist, recovery: ("LCA-FCDI_D02-D07_Processed_MonteCarlo"
                                        f"{_camel(_DIST_CAMEL, dist, 'distribution')}{_with_no(recovery)}Recovery_v01.csv"),
    mc_summary=lambda dist, recovery: ("LCA-FCDI_D02-D07_Processed_MCSummary"
                                       f"{_camel(_DIST_CAMEL, dist, 'distribution')}{_with_no(recovery)}Recovery_v01.csv"),
    mc_correlations=lambda dist: ("LCA-FCDI_D02-D07_Processed_MCCorrelations"
                                  f"{_camel(_DIST_CAMEL, dist, 'distribution')}_v01.csv"),   # no-recovery draws (kept quirk)
    scaling_baseline="LCA-FCDI_D01-D06_Processed_ScalingBaselineBase_v01.csv",
    scaling_bands="LCA-FCDI_D02-D07_Processed_ScalingBandsBase_v01.csv",
    grid=lambda axis: f"LCA-FCDI_D01-D06_Processed_Grid{_camel(_AXIS_CAMEL, axis, 'grid axis')}_v01.csv",
    break_even=lambda slug: ("LCA-FCDI_D02-D08_Processed_BreakEven"
                             f"{_camel(_SLUG_CAMEL, slug, 'break-even slug')}_v01.csv"),
    break_even_targets="LCA-FCDI_D01-D08_Processed_BreakEvenTargets_v01.csv",
    # Phase 3 (2026-10-05)
    conditions="LCA-FCDI_D01-D09_Processed_ConditionComparison_v01.csv",           # the four measured operating points
    loss_ledger="LCA-FCDI_D02-D09_Processed_ScaleUpLossLedger_v01.csv",            # D09 rows + computed loss quantities
    literature_harmonized="LCA-FCDI_D02-D08_Processed_LiteratureBenchmarksHarmonized_v01.csv",
)
# Outputs whose step reads a pending input (PENDING_INPUTS): OUT key -> IN key.
OUT_NEEDS_INPUT = dict(literature_harmonized="literature_benchmarks")


def scenario_prefix(scenario: str, recovery) -> str:
    """'<scenario>_<with|no>_recovery' (the tag the per-scenario tables are written under)."""
    if not isinstance(scenario, str) or not _SCENARIO_ID_RE.match(scenario):
        raise ValueError(f"invalid scenario id {scenario!r}")
    return f"{scenario}_{'with' if _recovery(recovery) else 'no'}_recovery"


def scenario_file_name(scenario: str, recovery, table: str) -> str:
    """'<scenario>_<with|no>_recovery__<table>.csv' (native name; scenario ids keep their underscores)."""
    if table not in SCENARIO_TABLES:
        raise KeyError(f"unknown scenario table {table!r}; known: {list(SCENARIO_TABLES)}")
    return f"{scenario_prefix(scenario, recovery)}__{table}.csv"


ORPHANS = dict(
    container="LCA-FCDI_D01-D06_Processed_ScenarioResultsSuperseded_v01",
    files=tuple(scenario_file_name(s, r, t) for s in ORPHAN_SCENARIOS for r in (True, False)
                for t in SCENARIO_TABLES),
)

# figure key -> (figure id, dataset range or None, description); the key is the old figure stem
# Documents v07 (2026-10-06, the owner's revision and comment C1): the conceptual framework is Fig. S1 of the
# Supplementary Material (figure id FigS01; embedded by the SI builder), the GWP and LCOT surfaces are one four-panel
# figure, and the manuscript holds five figures. The seven v06 figures stay on disk as superseded figures
# (SUPERSEDED_FIGURES), never regenerated or embedded. manuscript_figure_keys() / si_figure_keys() split the two.
FIG = dict(
    fig1_flowsheet=("Fig01", None, "Flowsheet"),                                   # Fig02 until v06 (content identical)
    fig2_lca_normalized=("Fig02", "D01-D06", "LCANormalized"),                     # Fig03 until v06 (one label)
    fig3_cost_structure=("Fig03", "D02-D08", "CostStructure"),                     # Fig04 until v06 (two labels)
    fig4_scale_vs_performance=("Fig04", "D01-D08", "ScaleVsPerformance"),          # Fig05 + Fig06 of v06, four panels
    fig5_sensitivity=("Fig05", "D01-D06", "Sensitivity"),                          # Fig07 until v06 (content identical)
    figS1_framework=("FigS01", None, "Framework"),                                 # Fig01 until v06 (three labels)
)
FIG_VERSION = {key: "v01" for key in FIG}
FIG_EXTS = ("png", "pdf")
SI_FIGURE_PREFIX = "FigS"
# Superseded figures (documents v07, 2026-10-06): the seven figure files of v06. They stay in 04_Figures, read-only,
# are hashed by the validation (validation.SUPERSEDED_FIGURE_SHA256), never regenerated, never loaded or embedded, and
# are declared to the RefactorGate (every file of 04_Figures is registered). Same layout as FIG.
SUPERSEDED_FIGURES = dict(
    fig1_framework=("Fig01", None, "Framework"),                                  # FigS01 since v07
    fig2_flowsheet=("Fig02", None, "Flowsheet"),                                  # Fig01 since v07
    fig3_lca_normalized=("Fig03", "D01-D06", "LCANormalized"),                    # Fig02 since v07
    fig4_cost_structure=("Fig04", "D02-D08", "CostStructure"),                    # Fig03 since v07
    fig5_scale_vs_performance_gwp=("Fig05", "D01-D06", "ScaleVsPerformanceGWP"),    # panels a-b of Fig04 since v07
    fig6_scale_vs_performance_lcot=("Fig06", "D02-D08", "ScaleVsPerformanceLCOT"),  # panels c-d of Fig04 since v07
    fig7_sensitivity=("Fig07", "D01-D06", "Sensitivity"),                           # Fig05 since v07
)
SUPERSEDED_FIGURE_VERSION = {key: "v01" for key in SUPERSEDED_FIGURES}


def manuscript_figure_keys() -> list:
    """The FIG keys embedded in the manuscript, in figure order (every key whose id is not an S figure)."""
    return [key for key, (fig_id, _r, _d) in FIG.items() if not fig_id.startswith(SI_FIGURE_PREFIX)]


def si_figure_keys() -> list:
    """The FIG keys embedded in the Supplementary Material (figure ids FigS01, ...)."""
    return [key for key, (fig_id, _r, _d) in FIG.items() if fig_id.startswith(SI_FIGURE_PREFIX)]

# ---------------------------------------------------------------------------------------------- documents
MANUSCRIPT_VERSION = "v08"                  # v08 (2026-10-06): the owner's answers to the v07 queries (abstract wording,
                                            # citations, CRediT statement, Zenodo DOI); v07 editorial revision (Figs. 5-6
                                            # merged, Table 3 moved to SI Table S26, Water Research references, US spelling)
SI_VERSION = "v07"                          # unchanged in v08; v06 (document revision), v05 (Phase 3) and v01-v04 stay
_DOC_VERSIONED = dict(manuscript="LCA-FCDI_Manuscript_{v}.docx", si="LCA-FCDI_SI_{v}.docx")
DOC_VERSIONS = dict(manuscript=("v01", "v02", "v03", "v04", "v05", "v06", "v07", MANUSCRIPT_VERSION),
                    si=("v01", "v02", "v03", "v04", "v05", "v06", SI_VERSION))
DOC = dict(
    manuscript=_DOC_VERSIONED["manuscript"].format(v=MANUSCRIPT_VERSION),
    si=_DOC_VERSIONED["si"].format(v=SI_VERSION),
    reference_list="LCA-FCDI_Manuscript_ReferenceList_v05.json",                 # v05 (manuscript v08): cited_in updated;
                                                                                 # v04 2026-10-06: structured entries, Water
                                                                                 # Research (Elsevier Harvard) style; v03: ACS
    data_dictionary="LCA-FCDI_D01-D09_Guide_DataDictionary_v02.md",              # Phase 3: D09 and the new inputs
    model_notes="LCA-FCDI_D01-D09_Report_ModelNotes_v02.md",                     # Phase 3: what changed and why
    publishing="LCA-FCDI_Plan_Publishing_v01.md",
    discovery_report="LCA-FCDI_Report_RestructureDiscovery_v01.md",
    verification_report="LCA-FCDI_Report_RestructureVerification_v01.md",
    data_sharing="LCA-FCDI_Report_DataSharing_v01.md",                           # Phase 2 report
    science_revision="LCA-FCDI_Report_ScienceRevision_v01.md",                   # Phase 3 report (written last)
    documents_v06="LCA-FCDI_Report_DocumentsV06_v01.md",                        # document revision v06 (2026-10-06)
    documents_v07="LCA-FCDI_Report_DocumentsV07_v01.md",                        # editorial revision v07 (2026-10-06)
    documents_v08="LCA-FCDI_Report_DocumentsV08_v01.md",                        # manuscript v08 (2026-10-06)
    highlights="LCA-FCDI_Highlights_v01.docx",                                  # v08: written by the manuscript builder
                                                                                 # from the prose module's HIGHLIGHTS
    graphical_abstract="LCA-FCDI_GraphicalAbstract_v01.png",                     # owner-made asset (2026-10-06), not
                                                                                 # generated by any script, not embedded
    legend="Dataset_ID_Legend.md",
    crosswalk="Data_Figure_Crosswalk.csv",
)
DOC_LOCATION = dict(
    manuscript=DOC_DIR, si=DOC_DIR, reference_list=DOC_DIR, graphical_abstract=DOC_DIR, highlights=DOC_DIR,
    data_dictionary=PROJDOC_DIR, model_notes=PROJDOC_DIR, publishing=PROJDOC_DIR,
    discovery_report=PROJDOC_DIR, verification_report=PROJDOC_DIR, data_sharing=PROJDOC_DIR,
    science_revision=PROJDOC_DIR, documents_v06=PROJDOC_DIR, documents_v07=PROJDOC_DIR, documents_v08=PROJDOC_DIR,
    legend=DATA_DIR,
    crosswalk=MANUSCRIPT_DIR,
)
# Earlier versions of the unversioned-key documents, kept on disk unchanged (documents are new versions, never edited
# in place): key -> (DOC key whose folder holds it, file name).
DOC_SUPERSEDED = dict(
    reference_list_v01=("reference_list", "LCA-FCDI_Manuscript_ReferenceList_v01.json"),
    reference_list_v02=("reference_list", "LCA-FCDI_Manuscript_ReferenceList_v02.json"),   # Phase 3 list; v03 adds one DOI
    reference_list_v03=("reference_list", "LCA-FCDI_Manuscript_ReferenceList_v03.json"),   # ACS strings; v04 restructures
    reference_list_v04=("reference_list", "LCA-FCDI_Manuscript_ReferenceList_v04.json"),   # v07 citations; v05 for v08
    data_dictionary_v01=("data_dictionary", "LCA-FCDI_D01-D08_Guide_DataDictionary_v01.md"),
    model_notes_v01=("model_notes", "LCA-FCDI_D01-D08_Report_ModelNotes_v01.md"),
)

# ---------------------------------------------------------------------------------------------- code and repo files
CODE = dict(
    run_all="LCA-FCDI_D01-D08_RunAll.py",
    make_manuscript="LCA-FCDI_D01-D08_MakeManuscript.py",
    make_si="LCA-FCDI_D01-D08_MakeSIDocx.py",
    manuscript_text="LCA-FCDI_D01-D08_ManuscriptText.py",
    si_equations="LCA-FCDI_D01-D08_SIEquations.py",
    references="LCA-FCDI_D01-D08_References.py",                                   # v07: citation and reference rendering
    extract_factors="LCA-FCDI_D01_ExtractFactorsFromWorkbooks.py",
    write_factors_superseded="LCA-FCDI_D01_WriteFactorsEcoinvent37.py",           # DISARMED provenance script
    build_data_dictionary="LCA-FCDI_D01-D08_BuildDataDictionary.py",
    migrate_inputs="LCA-FCDI_D01-D08_MigrateInputs.py",
    refactor_gate="LCA-FCDI_D01-D08_RefactorGate.py",
    compare_trees="LCA-FCDI_D01-D08_CompareTrees.py",
    build_deposit="LCA-FCDI_D01-D08_BuildDeposit.py",                              # project-only (not deposited)
)
REPO = dict(
    readme="README.md",
    license="LICENSE",
    citation="CITATION.cff",
    gitignore=".gitignore",
    gitattributes=".gitattributes",
    requirements="requirements.txt",
    pyproject="pyproject.toml",
    pytest_ini="pytest.ini",
    environment="environment.yml",
    declared_substitutions="declared_text_substitutions.csv",
)

# ---------------------------------------------------------------------------------------------- public repository
# The ALLOW-LIST of the public repository (data-code guide v02, section 11). Only what is listed here is ever copied
# into a deposit; everything else stays in the project. Keys are registry keys (IN, CODE) or package files relative to
# CODE_DIR. NOT public, deliberately: IN['factors'] (licensed, ecoinvent-derived) and every *Ecoinvent37* file; the
# superseded inputs; 01_Raw; EVERY file of 02_Processed (flow-level contributions, scenario, Monte Carlo, grid and
# sensitivity results can be combined with the public inventory to recover licensed factors; the TEA results need no
# licence and are reproduced exactly by a dummy-table run); 03_Manuscript; 04_Figures; 00_Project_Docs; fcdibes/figures/;
# the document builders (make_manuscript, make_si, manuscript_text, si_equations) and every other CODE tool; tests/;
# declared_text_substitutions.csv and the project's README, pyproject, pytest.ini and environment.yml (the deposit
# builder writes the public repository's own README, LICENSE-DATA, CITATION.cff, requirements.txt, .gitignore and
# .gitattributes; LICENSE is the project's MIT text unchanged).
DEPOSIT_VERSION = "v06"                   # v06 (2026-10-06, manuscript v08): CITATION.cff loadable by Zenodo (one license
                                          # string, repository-code, abstract), README with the repository URL and the
                                          # revised title, the registry constants of v08 (GitHub release v1.0.1).
                                          # v05 (2026-10-06, documents v07): the registry constants of datalayer.py (figures
                                          # renumbered and superseded, documents v07, reference list v04), the superseded-
                                          # figure hashes of validation.py and the figure step of RunAll.py; inputs and
                                          # calculations as in v04. v04 (literature confirmation; GitHub release v1.0.0),
                                          # v01 (Phase 2), v02 (Phase 3) and v03 (document revision) stay on disk
DEPOSIT_MARKER = "DEPOSIT_MANIFEST.csv"            # at the deposit root: path, bytes, sha256, registry_source
DEPOSIT = dict(
    # Phase 3 (2026-10-05): the IN keys are unchanged where a newer version was registered (process_assumptions,
    # equipment_general, scenarios, uncertainty, benchmarks now name v02 / v03); cpi, literature_benchmarks and
    # scaleup_ledger are new public inputs; conditions.py and ledger.py are new calculation modules.
    inputs=("unit_conversions", "influent", "process_assumptions", "materials", "equipment_general", "tea_inputs",
            "prices", "pumps", "centrifuges", "screens", "ec_mixers", "electrocultivation", "foreground_processes",
            "normalization", "electron_partitioning", "scenarios", "uncertainty", "sampling", "benchmarks",
            "factors_template", "factors_dummy", "flows", "cpi", "literature_benchmarks", "scaleup_ledger"),
    package=("fcdibes/__init__.py", "fcdibes/config.py", "fcdibes/correlations.py", "fcdibes/datalayer.py",
             "fcdibes/inventory.py", "fcdibes/io.py", "fcdibes/lca.py", "fcdibes/model.py", "fcdibes/params.py",
             "fcdibes/streams.py", "fcdibes/system.py", "fcdibes/tea.py", "fcdibes/unitops.py", "fcdibes/validation.py",
             "fcdibes/analysis/__init__.py", "fcdibes/analysis/_runner.py", "fcdibes/analysis/axes.py",
             "fcdibes/analysis/benchmarks.py", "fcdibes/analysis/conditions.py", "fcdibes/analysis/grid.py",
             "fcdibes/analysis/ledger.py", "fcdibes/analysis/scaling.py",
             "fcdibes/analysis/sensitivity.py", "fcdibes/analysis/targets.py", "fcdibes/analysis/uncertainty.py"),
    code=("run_all",),
)
FIGURES_PACKAGE = "fcdibes/figures"                 # project-only; RunAll skips the figure step when it is absent


# ---------------------------------------------------------------------------------------------- key resolution
def _key(registry: dict, aliases: dict, key: str, what: str) -> str:
    key = aliases.get(key, key)
    if key not in registry:
        raise KeyError(f"unknown {what} key {key!r}; known: {sorted(registry)}")
    return key


def rel_root(path) -> str:
    """POSIX path relative to ROOT; str(path) for anything outside ROOT (scratch folders, explicit OUTDIR)."""
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return str(p)


def require_file(path, what: str = "input file") -> Path:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"missing {what}: {p}")
    return p


# ---------------------------------------------------------------------------------------------- inputs, raw
def ref_dir(data_dir=None) -> Path:
    """The reference folder: REF_DIR, or the folder a test copied it to."""
    return Path(data_dir) if data_dir is not None else REF_DIR


def ref(key: str, data_dir=None) -> Path:
    """Path of a registered input. With data_dir the registered file NAME is resolved inside that folder."""
    return ref_dir(data_dir) / IN[_key(IN, IN_ALIASES, key, "IN")]


def superseded(key: str) -> Path:
    """Path of a superseded input version in 03_Reference (kept on disk, never loaded by the model)."""
    return REF_DIR / SUPERSEDED[_key(SUPERSEDED, {}, key, "SUPERSEDED")]


def raw(key: str) -> Path:
    return RAW_DIR / RAW_IN[_key(RAW_IN, RAW_ALIASES, key, "RAW_IN")]


def raw_label(key: str) -> str:
    """The OLD workbook file name (the value of the source_workbook column). Never changes."""
    return RAW_LABEL[_key(RAW_LABEL, RAW_ALIASES, key, "RAW_LABEL")]


# ---------------------------------------------------------------------------------------------- factor choice
def factors_choice() -> str:
    """The raw LCA_FCDI_FACTORS value ('' when unset)."""
    return os.environ.get(ENV_FACTORS, "").strip()


def factors_mode() -> str:
    choice = factors_choice()
    if not choice:
        return "default"
    return "dummy" if choice.lower() == "dummy" else "custom"


def is_default_factors() -> bool:
    return factors_mode() == "default"


def factors_path(data_dir=None) -> Path:
    """The factor table to read. default -> the licensed file; dummy -> the public dummy file (both resolved
    in data_dir when given); custom -> the path as given (absolute, or found relative to cwd, ROOT, CODE_DIR).
    No existence check here: the loader reports a missing file (see require_file)."""
    mode = factors_mode()
    if mode == "default":
        return ref("factors", data_dir)
    if mode == "dummy":
        return ref("factors_dummy", data_dir)
    given = Path(factors_choice()).expanduser()
    if given.is_absolute():
        return given
    for base in (Path.cwd(), ROOT, CODE_DIR):
        if (base / given).exists():
            return (base / given).resolve()
    return (Path.cwd() / given).resolve()


# ---------------------------------------------------------------------------------------------- run folders
def _check_tag(tag: str) -> str:
    if not _TAG_RE.match(tag):
        raise ValueError(f"invalid run tag {tag!r}: it must end in '_run' and be a plain folder name "
                         f"(letters, digits, '_' and '-'; at most 25 characters), e.g. {list(RUN_TAGS)}")
    return tag


def run_tag() -> str:
    """'' for a committed run, else the run-folder tag: LCA_FCDI_RUN if set, else dummy_run / altfactors_run
    for a non-default factor table. verify_run with a non-default factor table is refused."""
    explicit = os.environ.get(ENV_RUN, "").strip()
    mode = factors_mode()
    if explicit:
        tag = _check_tag(explicit)
        if tag == "verify_run" and mode != "default":
            raise ValueError("verify_run compares against the committed results and needs the default "
                             f"factor table, but {ENV_FACTORS}={factors_choice()!r}")
        return tag
    if mode == "dummy":
        return "dummy_run"
    if mode == "custom":
        return "altfactors_run"
    return ""


def inside_committed(path, base) -> bool:
    """True when path is base or lies inside it without passing through a *_run folder."""
    try:
        parts = Path(path).resolve().relative_to(Path(base).resolve()).parts
    except ValueError:
        return False
    return not any(part.endswith("_run") for part in parts)


def is_run_location(path, is_file: bool = True) -> bool:
    """True when path is an acceptable scratch target for a tool that must never touch the committed project:
    it lies outside ROOT, or inside ROOT under a folder whose name ends in '_run'. With is_file=True the last
    component is a file name and does not count as a run folder (a file called 'x_run' is not a run folder)."""
    p = Path(path).expanduser().resolve()
    try:
        parts = p.relative_to(ROOT.resolve()).parts
    except ValueError:
        return True
    if is_file:
        parts = parts[:-1]
    return any(part.endswith("_run") for part in parts)


def _explicit_dir(var: str, committed: Path) -> Path | None:
    value = os.environ.get(var, "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not is_default_factors() and inside_committed(path, committed):
        raise ValueError(f"{var}={value!r} points into the committed folder {committed} while a non-default "
                         f"factor table is selected ({ENV_FACTORS}={factors_choice()!r}); use a *_run folder")
    return path


def _run_folder(var: str, committed: Path) -> Path:
    explicit = _explicit_dir(var, committed)
    if explicit is not None:
        return explicit
    tag = run_tag()
    return committed / tag if tag else committed


def outdir() -> Path:
    """Folder of this run's processed outputs: PROC_DIR (committed run), PROC_DIR/<tag>, or LCA_FCDI_OUTDIR."""
    return _run_folder(ENV_OUTDIR, PROC_DIR)


def figout() -> Path:
    """Folder of this run's figures: FIG_DIR, FIG_DIR/<tag>, or LCA_FCDI_FIGOUT."""
    return _run_folder(ENV_FIGOUT, FIG_DIR)


def docout() -> Path:
    """Folder of this run's generated documents: DOC_DIR, or DOC_DIR/<tag>."""
    tag = run_tag()
    return DOC_DIR / tag if tag else DOC_DIR


def is_committed_run() -> bool:
    """True when outputs go to the committed folders (no run tag, no explicit output folder)."""
    return (not run_tag()) and not os.environ.get(ENV_OUTDIR, "").strip() \
        and not os.environ.get(ENV_FIGOUT, "").strip()


def describe_run() -> dict:
    """Everything resolved at call time, for logging and tests."""
    return dict(mode=factors_mode(), factors_choice=factors_choice(), factors_path=str(factors_path()),
                run_tag=run_tag(), outdir=str(outdir()), figout=str(figout()), docout=str(docout()),
                committed=is_committed_run(), root=str(ROOT), code_dir=str(CODE_DIR))


_ENV_NAMES = (ENV_FACTORS, ENV_RUN, ENV_OUTDIR, ENV_FIGOUT)


def _changes(tag, factors, outdir_, figout_) -> dict:
    changes = {}
    for name, value in ((ENV_RUN, tag), (ENV_FACTORS, factors), (ENV_OUTDIR, outdir_), (ENV_FIGOUT, figout_)):
        if value is not None:
            changes[name] = str(value)
    if changes.get(ENV_RUN):
        _check_tag(changes[ENV_RUN])
    return changes


def _apply(env, changes: dict) -> None:
    for name, value in changes.items():
        if value == "":
            env.pop(name, None)
        else:
            env[name] = value


def set_run(tag) -> str | None:
    """Set (or, with None / '', clear) LCA_FCDI_RUN in os.environ; returns the previous value.
    The variable is process-global and inherited by joblib workers and subprocesses started afterwards."""
    previous = os.environ.get(ENV_RUN)
    _apply(os.environ, _changes("" if tag is None else tag, None, None, None))
    return previous


@contextlib.contextmanager
def run_context(tag=None, factors=None, outdir=None, figout=None):
    """Temporarily set run variables (None = leave unchanged, '' = clear) and restore them on exit."""
    saved = {name: os.environ.get(name) for name in _ENV_NAMES}
    try:
        _apply(os.environ, _changes(tag, factors, outdir, figout))
        run_tag()                                   # fail early on an invalid combination
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_env(tag=None, factors=None, outdir=None, figout=None, base=None) -> dict:
    """A copy of the environment (base or os.environ) with the given run variables, for subprocess.run(env=...)."""
    env = dict(os.environ if base is None else base)
    _apply(env, _changes(tag, factors, outdir, figout))
    return env


# ---------------------------------------------------------------------------------------------- output paths
def out_name(key: str, *args) -> str:
    """The bare file name (or container folder name) of an OUT entry; args feed the function entries."""
    try:
        entry = OUT[key]
    except KeyError:
        raise KeyError(f"unknown OUT key {key!r}; known: {sorted(OUT)}") from None
    if callable(entry):
        return entry(*args)
    if args:
        raise TypeError(f"OUT[{key!r}] is a fixed name and takes no arguments")
    return entry


def _at(base: Path, key, args) -> Path:
    return base if key is None else base / out_name(key, *args)


def proc(key: str | None = None, *args) -> Path:
    """COMMITTED output path (always PROC_DIR). proc() is PROC_DIR itself."""
    return _at(PROC_DIR, key, args)


def out(key: str | None = None, *args) -> Path:
    """THIS RUN's output path (run-folder aware). out() is outdir() itself."""
    return _at(outdir(), key, args)


def scenario_dir(committed: bool = False) -> Path:
    return (PROC_DIR if committed else outdir()) / OUT["scenario_dir"]


def scenario_file(scenario: str, recovery, table: str, committed: bool = False) -> Path:
    return scenario_dir(committed) / scenario_file_name(scenario, recovery, table)


def orphan_dir() -> Path:
    return PROC_DIR / ORPHANS["container"]


def orphan_file(name: str) -> Path:
    if name not in ORPHANS["files"]:
        raise KeyError(f"{name!r} is not a registered orphan file")
    return orphan_dir() / name


# ---------------------------------------------------------------------------------------------- figures, documents, code
def _figure_stem(registry: dict, versions: dict, key: str, what: str) -> str:
    try:
        fig_id, drange, desc = registry[key]
    except KeyError:
        raise KeyError(f"unknown {what} key {key!r}; known: {sorted(registry)}") from None
    middle = f"_{drange}" if drange else ""
    return f"{PROJECT}_{fig_id}{middle}_{desc}_{versions[key]}"


def _figure_ext(ext: str) -> str:
    if ext.lstrip(".") not in FIG_EXTS:
        raise ValueError(f"unknown figure extension {ext!r}; use one of {FIG_EXTS}")
    return ext.lstrip(".")


def fig_stem(key: str) -> str:
    return _figure_stem(FIG, FIG_VERSION, key, "figure")


def fig_name(key: str, ext: str = "png") -> str:
    return f"{fig_stem(key)}.{_figure_ext(ext)}"


def superseded_figure_name(key: str, ext: str = "png") -> str:
    """File name of a superseded figure (SUPERSEDED_FIGURES; kept in 04_Figures, never regenerated)."""
    return f"{_figure_stem(SUPERSEDED_FIGURES, SUPERSEDED_FIGURE_VERSION, key, 'superseded figure')}.{_figure_ext(ext)}"


def superseded_figure(key: str, ext: str = "png") -> Path:
    """Path of a superseded figure in the committed 04_Figures folder (hashed by the validation, never loaded)."""
    return FIG_DIR / superseded_figure_name(key, ext)


def superseded_figure_names() -> list:
    return [superseded_figure_name(key, ext) for key in SUPERSEDED_FIGURES for ext in FIG_EXTS]


def fig_file(key: str, ext: str = "png") -> Path:
    """Figure path in THIS RUN's figure folder."""
    return figout() / fig_name(key, ext)


def fig_committed(key: str, ext: str = "png") -> Path:
    """Figure path in the committed 04_Figures folder."""
    return FIG_DIR / fig_name(key, ext)


def doc_name(key: str, version: str | None = None) -> str:
    key_ = _key(DOC, {}, key, "DOC")
    if version is None:
        return DOC[key_]
    if key_ not in DOC_VERSIONS:
        raise ValueError(f"DOC[{key_!r}] is not versioned by the registry (only {sorted(DOC_VERSIONS)})")
    if version not in DOC_VERSIONS[key_]:
        raise ValueError(f"unknown {key_} version {version!r}; registered: {DOC_VERSIONS[key_]}")
    return _DOC_VERSIONED[key_].format(v=version)


def doc_file(key: str, version: str | None = None) -> Path:
    """Committed document path (version only for manuscript / si)."""
    return DOC_LOCATION[_key(DOC, {}, key, "DOC")] / doc_name(key, version)


def doc_out(key: str, version: str | None = None) -> Path:
    """Where this run writes a generated document: the committed folder, or 01_Drafts/<tag>/ for run tags.
    Only documents of 01_Drafts are run-aware; other documents are always at their committed location."""
    path = doc_file(key, version)
    return docout() / path.name if path.parent == DOC_DIR else path


def code_file(key: str) -> Path:
    return CODE_DIR / CODE[_key(CODE, {}, key, "CODE")]


def repo_file(key: str) -> Path:
    return CODE_DIR / REPO[_key(REPO, {}, key, "REPO")]


def import_script(key: str):
    """Import a hyphenated entry script by registry key (importlib; puts CODE_DIR on sys.path once)."""
    name = Path(CODE[_key(CODE, {}, key, "CODE")]).stem
    if str(CODE_DIR) not in sys.path:
        sys.path.insert(0, str(CODE_DIR))
    return importlib.import_module(name)


def is_pending(key: str) -> bool:
    """True when the registered input `key` is delivered by a later step of the current revision (PENDING_INPUTS)."""
    return _key(IN, IN_ALIASES, key, "IN") in PENDING_INPUTS


def output_is_pending(key: str) -> bool:
    """True when the step that writes OUT[key] reads an input that is still pending (it is skipped, not expected)."""
    return key in OUT_NEEDS_INPUT and is_pending(OUT_NEEDS_INPUT[key])


def expected_outputs(scenarios=SCENARIOS, include_alt_grids: bool = True) -> list:
    """Names (relative to a processed root, POSIX) of every file a full run writes, in run order. An output whose
    step reads a pending input (PENDING_INPUTS) is left out; registry logic only, no I/O."""
    container = OUT["scenario_dir"]
    names = [f"{container}/{scenario_file_name(s, r, t)}"
             for s in scenarios for r in (True, False) for t in SCENARIO_TABLES]
    names += [out_name("scenario_kpis"), out_name("golden_kpis")]
    names += [out_name("conditions"), out_name("loss_ledger")]                         # Phase 3, steps 1b and 1c
    names += [out_name("benchmarks")]
    names += [out_name(k) for k in ("literature_harmonized",) if not output_is_pending(k)]   # Phase 3, step 2b
    names += [out_name("sensitivity", r) for r in (True, False)]
    for dist in DISTRIBUTIONS:
        for r in (True, False):
            names += [out_name("monte_carlo", dist, r), out_name("mc_summary", dist, r)]
        names.append(out_name("mc_correlations", dist))
    names += [out_name("scaling_baseline"), out_name("scaling_bands"), out_name("grid", "areal_se_rate")]
    names += [out_name("break_even", slug) for slug in BREAK_EVEN_SLUGS]
    names.append(out_name("break_even_targets"))
    if include_alt_grids:
        names += [out_name("grid", axis) for axis in ALT_GRID_AXES]
    return names


def expected_figures() -> list:
    return [fig_name(key, ext) for key in FIG for ext in FIG_EXTS]


def figure_code_present() -> bool:
    """True when the figure package is in this copy (it is project-only: a public clone has none)."""
    return (CODE_DIR / FIGURES_PACKAGE / "__init__.py").is_file()


# ---------------------------------------------------------------------------------------------- public repository
_DEPOSIT_VERSION_RE = re.compile(r"^v\d\d$")


def deposit_name(version: str | None = None) -> str:
    """Folder name of a deposit version: 'LCA-FCDI_ZenodoDeposit_vNN' (default: DEPOSIT_VERSION)."""
    version = DEPOSIT_VERSION if version is None else version
    if not isinstance(version, str) or not _DEPOSIT_VERSION_RE.match(version):
        raise ValueError(f"invalid deposit version {version!r}; use vNN, e.g. 'v01'")
    return f"{PROJECT}_ZenodoDeposit_{version}"


def deposit_dir(version: str | None = None) -> Path:
    """00_Project_Docs/LCA-FCDI_ZenodoDeposit_vNN: the built public repository (a frozen snapshot; never run there)."""
    return PROJDOC_DIR / deposit_name(version)


def deposit_files() -> list:
    """The allow-list as [(published path, source path, registry source)], in allow-list order. The published path is
    POSIX and relative to the deposit root, which mirrors the project layout (01_Data/03_Reference/..., 02_Analysis_Code/
    ...); the registry source is 'IN:<key>', 'PACKAGE:<path under CODE_DIR>' or 'CODE:<key>'. Names only, no I/O."""
    ref_rel = REF_DIR.relative_to(ROOT).as_posix()
    files = [(f"{ref_rel}/{IN[key]}", ref(key), f"IN:{key}") for key in DEPOSIT["inputs"]]
    files += [(f"{CODE_DIR.name}/{rel}", CODE_DIR / rel, f"PACKAGE:{rel}") for rel in DEPOSIT["package"]]
    files += [(f"{CODE_DIR.name}/{CODE[key]}", code_file(key), f"CODE:{key}") for key in DEPOSIT["code"]]
    return files


def is_deposit(root=None) -> bool:
    """True when this copy is a public repository (the deposit marker DEPOSIT_MANIFEST.csv sits at its root)."""
    return (Path(ROOT if root is None else root) / DEPOSIT_MARKER).is_file()


def is_deposit_folder_in_project(root=None) -> bool:
    """True for the built deposit inside a project's 00_Project_Docs: a frozen snapshot in which nothing may run (a run
    would write results into it). Clone or copy it elsewhere to run it."""
    root = Path(ROOT if root is None else root)
    return is_deposit(root) and root.parent.name == PROJDOC_DIR.name


# ---------------------------------------------------------------------------------------------- CSV / text I/O
def strip_bom(text: str) -> str:
    return text[1:] if text.startswith("﻿") else text


def read_csv_text(path) -> str:
    """The file as text without BOM (line endings normalised to '\\n')."""
    return Path(path).read_text(encoding="utf-8-sig")


def csv_rows(path) -> list:
    """stdlib csv reader: list of dicts with STRING values (no float parsing), BOM accepted."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_csv(path, **kw):
    """pandas.read_csv with an explicit BOM-safe encoding and pandas' DEFAULT float parsing."""
    import pandas as pd
    kw.setdefault("encoding", "utf-8-sig")
    return pd.read_csv(path, **kw)


def same_file(a, b) -> bool:
    """True when a and b are the same existing file: one path, or two names of one file (a hard link, or a path
    through a junction / symlink). False when either does not exist."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def is_protected(path) -> bool:
    """Files write_csv refuses to touch: the raw workbook folder, the superseded (orphan) container, the registered
    licensed factor table and the superseded input versions of 03_Reference (SUPERSEDED; Phase 3: a superseded version
    never changes). A path is also protected when it is another NAME of one of the protected registered files (a hard
    link), because writing through the link would change the protected file."""
    p = Path(path).resolve()
    licensed = (REF_DIR / IN["factors"]).resolve()
    if p == licensed or p in {(REF_DIR / name).resolve() for name in SUPERSEDED.values()}:
        return True
    for base in (RAW_DIR, orphan_dir()):
        try:
            p.relative_to(base.resolve())
            return True
        except ValueError:
            continue
    if p.is_file():                                     # an existing file may be a hard link to a protected one
        guarded = [REF_DIR / IN["factors"]] + [RAW_DIR / name for name in RAW_IN.values()]
        guarded += [orphan_dir() / name for name in ORPHANS["files"]]
        guarded += [REF_DIR / name for name in SUPERSEDED.values()]
        return any(same_file(p, other) for other in guarded)
    return False


def write_csv(df, path, **kw) -> Path:
    """DataFrame.to_csv that ALWAYS writes UTF-8 with BOM (utf-8-sig) into a plain new file: index=False unless
    given; creates the parent folder; returns the Path. Everything else (float_format, columns, lineterminator)
    passes through, so numbers are written exactly as the plain to_csv call would. `mode` (append) and
    `compression` are refused: an appended chunk would put a second BOM inside the file and a compressed file
    is not a CSV. A target that is protected, or a hard link to a protected file, raises PermissionError."""
    for name in ("mode", "compression"):
        if name in kw:
            raise ValueError(f"write_csv always writes a plain new file: the {name!r} keyword ({kw[name]!r}) "
                             "is not accepted")
    if kw.get("encoding") not in (None, "utf-8-sig"):
        raise ValueError(f"write_csv always writes utf-8-sig, not {kw['encoding']!r}")
    kw["encoding"] = "utf-8-sig"
    kw.setdefault("index", False)
    path = Path(path)
    if is_protected(path):
        raise PermissionError(f"refusing to write {rel_root(path)}: raw workbooks, the superseded container, the "
                              "licensed factor table and the superseded input versions are read-only for code")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, **kw)
    return path


# ---------------------------------------------------------------------------------------------- naming hygiene
_D = r"D0[1-9]"                                     # D01-D09 (D09 since Phase 3)
_RANGE = rf"{_D}(?:-{_D})?"
_CAMEL = r"[A-Za-z0-9]+"
_PROJ = re.escape(PROJECT)
_NAME_PATTERNS = {
    "reference": re.compile(rf"^(?:\d{{4}}-\d{{2}}-\d{{2}}_)?{_PROJ}_(?P<range>{_RANGE})_(?P<type>Reference)_"
                            rf"(?P<desc>{_CAMEL})_(?P<version>v\d\d)\.(?P<ext>csv|yaml)$"),
    "processed": re.compile(rf"^{_PROJ}_(?P<range>{_RANGE})_(?P<type>Processed)_"
                            rf"(?P<desc>{_CAMEL})_(?P<version>v\d\d)\.(?P<ext>csv|json)$"),
    "container": re.compile(rf"^{_PROJ}_(?P<range>{_RANGE})_(?P<type>Processed)_"
                            rf"(?P<desc>{_CAMEL})_(?P<version>v\d\d)$"),
    "raw": re.compile(rf"^{_PROJ}_(?P<range>{_RANGE})_(?P<date>\d{{4}}-\d{{2}}-\d{{2}})_"
                      rf"(?P<desc>{_CAMEL})\.(?P<ext>xlsx)$"),
    "figure": re.compile(rf"^{_PROJ}_(?P<fig>Fig(?:S)?\d\d)(?:_(?P<range>{_RANGE}))?_"
                         rf"(?P<desc>{_CAMEL})_(?P<version>v\d\d)(?:\.(?P<ext>png|pdf))?$"),
    "document": re.compile(rf"^{_PROJ}_(?:(?P<range>{_RANGE})_)?(?P<type>Report|Plan|Guide|Manuscript|SI|GraphicalAbstract|Highlights)"
                           rf"(?:_(?P<desc>{_CAMEL}))?_(?P<version>v\d\d)\.(?P<ext>docx|md|json|png)$"),
    "script": re.compile(rf"^{_PROJ}_(?P<range>{_RANGE})_(?P<desc>{_CAMEL})\.(?P<ext>py)$"),
}
_SCENARIO_FILE_RE = re.compile(r"^(?P<scenario>[A-Za-z0-9][A-Za-z0-9_-]*)_(?P<config>with|no)_recovery__"
                               r"(?P<table>[a-z_]+)\.csv$")


def _range_ok(drange) -> bool:
    if not drange:
        return True
    parts = drange.split("-")
    return len(parts) == 1 or parts[0] < parts[1]


def parse_name(name: str) -> dict | None:
    """Split a registered file name into its parts; None when it fits no project pattern.
    Returns dict(kind, range, type, desc, version, ext, ...) with missing parts as None."""
    for kind, pattern in _NAME_PATTERNS.items():
        m = pattern.match(name)
        if m:
            parts = m.groupdict()
            parts["kind"] = kind
            return parts
    return None


def is_licensed_name(name: str) -> bool:
    return fnmatch.fnmatch(name, LICENSED_PATTERN)


def registry_problems() -> list:
    """Structural problems of the registry (empty list = fine). Checks names only, never the file system."""
    problems: list = []
    seen: dict = {}

    def claim(kind: str, label: str, name: str, expect: str | None) -> None:
        if name in seen:
            problems.append(f"duplicate name {name!r}: {seen[name]} and {kind}:{label}")
        seen[name] = f"{kind}:{label}"
        if expect is None:
            return
        parts = parse_name(name)
        if parts is None or parts["kind"] != expect:
            problems.append(f"{kind}:{label}: {name!r} does not follow the {expect} naming pattern")
        elif not _range_ok(parts.get("range")):
            problems.append(f"{kind}:{label}: {name!r} has a descending dataset range")

    for key, name in IN.items():
        claim("IN", key, name, "reference")
    for alias, key in IN_ALIASES.items():
        if key not in IN:
            problems.append(f"IN_ALIASES[{alias!r}] -> unknown key {key!r}")
        if alias in IN:
            problems.append(f"IN_ALIASES[{alias!r}] shadows a real IN key")
    for key, name in RAW_IN.items():
        claim("RAW_IN", key, name, "raw")
    if set(RAW_IN) != set(RAW_LABEL):
        problems.append("RAW_IN and RAW_LABEL have different keys")
    for alias, key in RAW_ALIASES.items():
        if key not in RAW_IN:
            problems.append(f"RAW_ALIASES[{alias!r}] -> unknown key {key!r}")
    claim("OUT", "scenario_dir", OUT["scenario_dir"], "container")
    for name in expected_outputs():
        if name.startswith(OUT["scenario_dir"] + "/"):
            inner = name.split("/", 1)[1]
            m = _SCENARIO_FILE_RE.match(inner)
            if not m or m["table"] not in SCENARIO_TABLES:
                problems.append(f"OUT scenario file {inner!r} does not follow the native pattern")
            claim("OUT", "scenario_file", name, None)
        else:
            claim("OUT", name, name, "processed")
    for key in OUT_NEEDS_INPUT:                     # an output left out while its input is pending is still named here
        if key not in OUT or callable(OUT[key]):
            problems.append(f"OUT_NEEDS_INPUT names {key!r}, which is not a fixed OUT name")
        elif output_is_pending(key):
            claim("OUT", key, OUT[key], "processed")
        if OUT_NEEDS_INPUT[key] not in IN:
            problems.append(f"OUT_NEEDS_INPUT[{key!r}] -> unknown IN key {OUT_NEEDS_INPUT[key]!r}")
    for key in PENDING_INPUTS:
        if key not in IN or key == "factors":
            problems.append(f"PENDING_INPUTS names {key!r}, which is not a pending-capable IN key")
    labels = [label for label, _scenario in CONDITIONS]
    if len(set(labels)) != len(labels) or len({s for _l, s in CONDITIONS}) != len(CONDITIONS):
        problems.append("CONDITIONS repeats a label or a scenario")
    for label, scenario in CONDITIONS:
        if scenario not in SCENARIOS:
            problems.append(f"CONDITIONS[{label!r}] -> scenario {scenario!r} is not in SCENARIOS")
    if dict(CONDITIONS).get(BASE_CONDITION) != "base":
        problems.append(f"BASE_CONDITION {BASE_CONDITION!r} must map to the scenario 'base'")
    claim("ORPHANS", "container", ORPHANS["container"], "container")
    if ORPHANS["container"] == OUT["scenario_dir"]:
        problems.append("ORPHANS container must differ from the regenerated scenario container")
    for name in ORPHANS["files"]:
        if not _SCENARIO_FILE_RE.match(name):
            problems.append(f"ORPHANS file {name!r} does not follow the native pattern")
        claim("ORPHANS", "file", f"{ORPHANS['container']}/{name}", None)
    if len(ORPHANS["files"]) != len(ORPHAN_SCENARIOS) * 2 * len(SCENARIO_TABLES):
        problems.append("ORPHANS must hold every table of every orphan scenario configuration")
    for key in FIG:
        if key not in FIG_VERSION:
            problems.append(f"FIG_VERSION has no entry for {key!r}")
            continue
        for ext in FIG_EXTS:
            claim("FIG", key, fig_name(key, ext), "figure")
    for key in SUPERSEDED_FIGURES:                  # v07: unique against every registered name, never a FIG key
        if key not in SUPERSEDED_FIGURE_VERSION:
            problems.append(f"SUPERSEDED_FIGURE_VERSION has no entry for {key!r}")
            continue
        if key in FIG:
            problems.append(f"SUPERSEDED_FIGURES[{key!r}] is still a registered figure key")
        for ext in FIG_EXTS:
            claim("SUPERSEDED_FIGURES", key, superseded_figure_name(key, ext), "figure")
    for key, name in DOC.items():
        if key in ("legend", "crosswalk"):
            claim("DOC", key, name, None)
        else:
            claim("DOC", key, name, "document")
        if key not in DOC_LOCATION:
            problems.append(f"DOC_LOCATION has no entry for {key!r}")
    for key, versions in DOC_VERSIONS.items():
        for version in versions:
            name = doc_name(key, version)
            if parse_name(name) is None:
                problems.append(f"DOC version name {name!r} does not follow the document pattern")
        if DOC[key] != doc_name(key, versions[-1]):
            problems.append(f"DOC[{key!r}] is not the last registered version {versions[-1]}")
    for key, (doc_key, name) in DOC_SUPERSEDED.items():
        claim("DOC_SUPERSEDED", key, name, "document")
        if doc_key not in DOC:
            problems.append(f"DOC_SUPERSEDED[{key!r}] names the unknown DOC key {doc_key!r}")
            continue
        old, new = parse_name(name), parse_name(DOC[doc_key])
        if old and new and ((old["type"], old["desc"], old["ext"]) != (new["type"], new["desc"], new["ext"])
                            or not old["version"] < new["version"]):
            problems.append(f"DOC_SUPERSEDED[{key!r}] {name!r} is not an older version of {DOC[doc_key]!r}")
    for key, name in CODE.items():
        claim("CODE", key, name, "script")
    for key, name in REPO.items():
        claim("REPO", key, name, None)
    licensed = sorted(n for n in list(IN.values()) + list(CODE.values()) if is_licensed_name(n))
    expected = sorted([IN["factors"], CODE["write_factors_superseded"]])
    if licensed != expected:
        problems.append(f"names matching {LICENSED_PATTERN!r} are {licensed}, expected {expected}")
    if any(is_licensed_name(n) for n in (IN["factors_template"], IN["factors_dummy"])):
        problems.append("the public template / dummy table must not match the licensed pattern")
    problems += _superseded_problems(claim) + _deposit_problems()
    return problems


def _superseded_problems(claim) -> list:
    problems = []
    if set(SUPERSEDED) != set(SUPERSEDED_BY):
        problems.append("SUPERSEDED and SUPERSEDED_BY have different keys")
    for key, name in SUPERSEDED.items():
        claim("SUPERSEDED", key, name, "reference")              # unique against every registered name, pattern
        if name in IN.values():
            problems.append(f"SUPERSEDED[{key!r}] {name!r} is still a registered input (a superseded file is never loaded)")
        newer = IN.get(SUPERSEDED_BY.get(key, ""))
        if newer is None:
            problems.append(f"SUPERSEDED_BY[{key!r}] does not name an IN key")
            continue
        old, new = parse_name(name), parse_name(newer)
        if old and new and ((old["range"], old["desc"], old["ext"]) != (new["range"], new["desc"], new["ext"])
                            or not old["version"] < new["version"]):
            problems.append(f"SUPERSEDED[{key!r}] {name!r} is not an older version of {newer!r}")
    return problems


def _deposit_problems() -> list:
    """The allow-list may name only registered, public files: every input except the licensed table, the calculation
    package without the figure package, and the runner."""
    problems = []
    inputs, package, code = DEPOSIT["inputs"], DEPOSIT["package"], DEPOSIT["code"]
    if set(DEPOSIT) != {"inputs", "package", "code"}:
        problems.append(f"DEPOSIT has the parts {sorted(DEPOSIT)}, expected inputs, package, code")
    for label, items in (("inputs", inputs), ("package", package), ("code", code)):
        if len(set(items)) != len(items):
            problems.append(f"DEPOSIT[{label!r}] lists an entry twice")
    if "factors" in inputs or any(is_licensed_name(IN.get(k, "")) for k in inputs):
        problems.append("DEPOSIT['inputs'] names the licensed factor table")
    if set(inputs) != set(IN) - {"factors"}:
        problems.append(f"DEPOSIT['inputs'] must be every IN key except 'factors': missing "
                        f"{sorted(set(IN) - {'factors'} - set(inputs))}, unknown {sorted(set(inputs) - set(IN))}")
    for rel in package:
        if not (rel.startswith("fcdibes/") and rel.endswith(".py")) or rel.startswith(FIGURES_PACKAGE + "/") \
                or ".." in rel or "\\" in rel:
            problems.append(f"DEPOSIT['package'] entry {rel!r} is not a calculation-package file")
    if list(code) != ["run_all"]:
        problems.append(f"DEPOSIT['code'] must be ('run_all',), not {code}")
    if not _DEPOSIT_VERSION_RE.match(DEPOSIT_VERSION):
        problems.append(f"DEPOSIT_VERSION {DEPOSIT_VERSION!r} is not vNN")
    if any(is_licensed_name(Path(p).name) or "licensed" in p.lower() for p, _src, _reg in deposit_files()):
        problems.append("a deposit file name matches the licensed patterns")
    return problems


def _main() -> int:
    for key, value in describe_run().items():
        print(f"{key:>14}: {value}")
    problems = registry_problems()
    print(f"registry problems: {len(problems)}")
    for problem in problems:
        print("  -", problem)
    missing = [rel_root(ref(key)) for key in IN if not ref(key).is_file() and not is_pending(key)]
    pending = [rel_root(ref(key)) for key in IN if not ref(key).is_file() and is_pending(key)]
    if pending:
        print(f"pending inputs (delivered by a later step of the current revision, PENDING_INPUTS): {pending}")
    if not is_deposit():                       # a public clone has no raw workbooks, superseded inputs or figures
        missing += [rel_root(raw(key)) for key in RAW_IN if not raw(key).is_file()]
        missing += [rel_root(superseded(key)) for key in SUPERSEDED if not superseded(key).is_file()]
        missing += [rel_root(superseded_figure(key, ext)) for key in SUPERSEDED_FIGURES for ext in FIG_EXTS
                    if not superseded_figure(key, ext).is_file()]
    if is_deposit():
        print(f"public repository ({DEPOSIT_MARKER} found): the licensed table is supplied by the user")
    print(f"registered inputs missing on disk: {len(missing)}")
    for name in missing:
        print("  -", name)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(_main())
