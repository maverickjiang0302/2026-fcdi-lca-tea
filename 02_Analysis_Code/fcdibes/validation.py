"""Structural validation of the LCA-FCDI inputs (guide Section 8, adapted to this model).

    validate_all(strict=True)  ->  list[str]   one message per problem, [] when everything is consistent
                                               strict and problems  ->  SystemExit naming file, row and reason

The layer is structural only. It changes no number, reads no provenance column into the model and does not
re-implement the model: parameter paths, dataset choices, gaps and the uncertainty label map are checked by calling
the code that already exists (fcdibes.io, fcdibes.params, fcdibes.analysis.uncertainty / sensitivity /
benchmarks). Every file is addressed through the registry of fcdibes.datalayer (registry KEYS, never file names),
and a copied reference folder can be validated with data_dir=<folder> (tests do this with corrupted copies).

Message format:   [family] <file name> row <N>: <reason>       (or  [family] <file name>: <reason>)
Row numbers count the header as row 1 (as a spreadsheet shows them). Families, in run order:

    registry    the data layer's own name registry is consistent
    exists      every registered input exists (the licensed factor table may be absent in a deposit clone that
                selects another table; an input listed in datalayer.PENDING_INPUTS may be absent while the current
                revision has not delivered it: it is reported in run_checks()['pending'], not as a problem); in the
                project scope every superseded input version (datalayer.SUPERSEDED) is still on disk with its
                recorded SHA-256 (SUPERSEDED_SHA256): it is only hashed, never parsed or loaded
    header      UTF-8 (BOM or not), no ragged rows, the columns the loaders need, the four provenance columns
                (source, source_locator, value_type, note), the original columns first and in order
    keys        key uniqueness per file: (group, parameter) in the grouped parameter files, parameter in the flat
                files, (flow, dataset_id, impact_category) in the factor tables, (process, flow) in foreground,
                (group, direction, flow) in the inventory, model_name per catalogue, parameter in uncertainty,
                (technology, metric) in the benchmarks (v02, Phase 3: only the as-reported rows, 2010 USD per
                1000 gal; the code escalates them with the CPI-U), flow_name in the dictionary, key in
                SamplingSettings, year in the CPI-U table, mechanism in the loss ledger. Also: catalogues hold one
                equipment type and are sorted ascending (the model takes the first model that fits), inventory
                directions are Input / Output, every technology has its three cost rows; Phase 3: the vocabularies of
                the loss ledger (treatment, derating_target; every parameter it names is in ProcessAssumptions group
                Scale-up) and of the literature table (scale, assessment, metric, harmonizable, harmonized_unit; no
                harmonized value where harmonizable is 'no', a reason where it is 'no', no harmonized value for an
                unverified row), no repeated literature row, the seven Scale-up parameters present, each benchmark
                dollar year and the target dollar year present in the CPI-U table
    provenance  value_type vocabulary (blank allowed, counted), NA-like literals in the appended columns
    numeric     numeric columns numeric, through the same coercion the loader uses (io._numeric); the one
                known text value of the materials table is listed, any other text value is reported;
                ref_amount is 1 (the model never reads it); Phase 3: the CPI-U values positive, the Scale-up
                parameters inside their physical ranges (retention in (0, 1], shunt fraction in [0, 1), resistivity
                >= 0, path, viscosities and diameter > 0), literature values numeric where given
    factors     the factor table (the CHOSEN one: LCA_FCDI_FACTORS) has exactly the ten configured impact
                categories, every flow-dataset pair has all of them (the gaps io._lca_factors reports must be
                exactly KNOWN_GAPS, which is empty today), the licensed table has its 270 rows and (project scope
                only: a licensee builds the table from the template, which has no labels) the recorded workbook
                labels, the normalisation table covers the categories
    flows       FlowDictionary: unique flow names, vocabularies, unit == ref_unit of the factor table for
                ecoinvent37 flows (and for foreground inputs), alias targets exist and do not loop, used_by names
                registered files, every factor / foreground / inventory / price flow is in the dictionary
    template    template and dummy: same key set and order as the licensed table, 270 rows, dummy all 1.0,
                template blank
    scenarios   inheritance acyclic, every override path exists in the loaded parameters (fcdibes.params) and its
                value is a number, no misspelled scenario section, the run list is defined, every
                lca_dataset_overrides entry names a dataset that exists for that flow in the CHOSEN table
                (a table lacking the alternative datasets would ignore the override silently); io._lca_factors
                is run per scenario, so a flow left without a dataset choice is caught
    uncertainty every uncertainty row maps to a model parameter through analysis.uncertainty's label map and
                that parameter exists in the loaded parameters (unmapped rows are skipped silently by the
                sampler); distribution word and bounds; the sensitivity registry resolves too
    sampling    SamplingSettings keys present, numeric where numeric, equal to the frozen Phase 1 values
    orphans     the 24 superseded per-scenario tables exist, are non-empty, read-only and match the recorded
                SHA-256 (ORPHAN_SHA256, one value per file name; the message names the file)
    raw         both raw workbooks exist, are read-only and match the recorded SHA-256

Public API
    validate_all(strict=True, data_dir=None, *, raw_dir=None, orphan_dir=None, scope=None,
                 families=None) -> list[str]
    run_checks(data_dir=None, *, raw_dir=None, orphan_dir=None, scope=None, families=None) -> dict
                (problems per family, blank value_type counts per file, the pending inputs that are absent);
                families limits the run (tests)
    FAMILIES, VALUE_TYPES, PROVENANCE_COLUMNS, ORIGINAL_COLUMNS, KNOWN_GAPS, SHA256, ORPHAN_SHA256,
    SUPERSEDED_SHA256 (the recorded constants)
    python -m fcdibes.validation   prints the summary and exits 1 when there are problems

scope: "project" requires everything, including the raw workbooks, the orphan tables and the superseded inputs.
"deposit" is for a public clone (the public repository built by the deposit builder): the licensed table may be absent
when another factor table is chosen (LCA_FCDI_FACTORS), the template and the dummy table are required like every other
public input, and the raw-workbook, orphan, superseded-input and workbook-label checks are skipped. The default (None)
picks "deposit" only when the deposit marker (datalayer.DEPOSIT_MARKER, the deposit manifest) sits at the root of this
copy (datalayer.is_deposit()); without it the tree is always checked in full, whatever files are missing.
Import is side-effect free and light (standard library and the data layer only); pandas and the model modules load
when a check needs them.
"""
from __future__ import annotations

import csv
import hashlib
import math
import stat
from collections import Counter, defaultdict
from pathlib import Path

from . import datalayer as dl

# ---------------------------------------------------------------------------------------------- vocabularies
PROVENANCE_COLUMNS = ("source", "source_locator", "value_type", "note")
VALUE_TYPES = ("reported", "derived", "assumed", "protocol", "not reported (protocol default)", "legacy",
               "standard", "documentation", "unverified")       # plus blank (allowed, counted); 'unverified' (Phase 3):
                                                                # a literature value not confirmed from the source
# pandas' default NA strings, lower-cased. A cell holding one of them is read back as a missing value.
NA_LITERALS = frozenset({"#n/a", "#n/a n/a", "#na", "-1.#ind", "-1.#qnan", "-nan", "1.#ind", "1.#qnan",
                         "<na>", "n/a", "na", "null", "nan", "none"})
FLOW_CLASSES = ("technosphere_input", "elementary_emission", "waste_treatment", "product")
FACTOR_SOURCES = ("ecoinvent37", "custom_lci", "legacy_openlca", "traci_elementary", "computed", "none")
FLOW_COLUMNS = ("flow_name", "flow_class", "unit", "factor_source", "provider_process", "database",
                "default_contribution_group", "used_by", "alias_of", "note")
DIRECTIONS = ("Input", "Output")
UNCERTAINTY_DISTRIBUTIONS = ("triangular", "discrete")          # the sampler treats every other word as continuous
BENCHMARK_UNITS = ("$/1000 gal",)              # v02 (Phase 3): the as-reported rows only; the code converts them
SCENARIO_KEYS = ("inherits", "label", "note", "source", "parameter_overrides", "lca_dataset_overrides",
                 "condition")                  # 'condition' (Phase 3): the measured operating point a scenario is
# Phase 3 vocabularies
SCALE_UP_PARAMETERS = {                        # ProcessAssumptions group 'Scale-up': name -> (lowest, highest, kind)
    "Current density retention": (0.0, 1.0, "(lo, hi]"),
    "Shunt current fraction": (0.0, 1.0, "[lo, hi)"),
    "Graphite resistivity": (0.0, math.inf, "[lo, hi)"),
    "Collector current path": (0.0, math.inf, "(lo, hi)"),
    "Water viscosity": (0.0, math.inf, "(lo, hi)"),
    "Slurry viscosity ratio": (0.0, math.inf, "(lo, hi)"),
    "Slurry channel diameter": (0.0, math.inf, "(lo, hi)"),
}
LEDGER_TREATMENTS = ("sampled", "computed", "design-basis", "modeled", "not-modeled")
LEDGER_TARGETS = ("J", "energy", "RE", "none")
LITERATURE_ENUMS = {
    "scale": ("lab", "pilot", "modeled", "review"),
    "assessment": ("LCA", "TEA", "energy", "both"),
    "metric": ("LCOW", "specific_energy", "GWP", "capital", "other"),
    "harmonizable": ("yes", "no"),
    "harmonized_unit": ("USD2026/m3", "kWh/m3", "kg CO2-eq/m3", ""),
}

# ---------------------------------------------------------------------------------------------- recorded constants
SHA256 = {                      # the two raw workbooks, as copied (read-only) into the raw folder
    "inputs": "a721522a80414058015ce2771392195ffa5954e4409efcaeb9b5e4b4ebb6ac02",
    "metafile": "7866bb4cfe5d468398761648e5fe0bc4af3b48024d219db89bdf3c3399e5b293",
}
# The 24 superseded per-scenario tables (scenario 'lci_graphite_low', no code regenerates them), file name -> SHA-256.
# Taken from the frozen old repository (results/scenarios/lci_graphite_low_*) and equal to the copies in the
# superseded container; the read-only attribute alone would not notice an edited cell.
_ORPHAN_HASHES = {             # (configuration, table) -> SHA-256 of that table of scenario 'lci_graphite_low'
    ("with_recovery", "capex"):
        "82548c56bfd44e668652762c193a4f06f39695ca8c639dd8024a37088d70b008",
    ("with_recovery", "kpis"):
        "e23eb2c1a5028f0f54842912fe404bbfc2d00dd135941f47351e0562f2039a90",
    ("with_recovery", "lca_contributions"):
        "f71007a9914294ec92d3981febfe5b32ba1c6d1284c09ba3c5c36d78d337eec1",
    ("with_recovery", "lca_impacts"):
        "b59f984a2451d531168cbca3af8040826f769810a2f34697afe1af884f2480e2",
    ("with_recovery", "lca_inventory"):
        "602af7790eebf9c7556e5871d72d43a3d18a5a771d927561af505fd6484e39f4",
    ("with_recovery", "opex"):
        "4980f74e68d35064627fef985a264bf0458a445f5d568a0ce9ce621adfeb98c5",
    ("with_recovery", "power"):
        "8a88d1415a5cd07008705277b04362d76302dfe3ab150bb8ea9b0fa9e051e2c3",
    ("with_recovery", "reactor_capex"):
        "95711e448f5a4b5984bd21d57b3d88a6088132d3bf2b278914bc85db8586951f",
    ("with_recovery", "streams"):
        "32b28719c3db446c55ebbc1137e20b139db2fd48447c9f00d5bb3ba2c41238bf",
    ("with_recovery", "tea_capital"):
        "cd0a527e5ce6cffcc4e3592425d008f937ecd1a451a4562ff2a28d0b91448cb2",
    ("with_recovery", "tea_com"):
        "1cb9b16365f8e4c2bd1a015a8705777054f1ff04a7363530c554945245478f0d",
    ("with_recovery", "tea_metrics"):
        "c3be7eb721340ccb027e1abe83cc0e3bb322e46ee1a3b3228bc3ddfbaa720dc9",
    ("no_recovery", "capex"):
        "481d76b47d54d52967bd4b8874f5dbb93192ac4c6ebc47d4eb6aae5f56c5aca7",
    ("no_recovery", "kpis"):
        "4af315b4f833ddf195c6330f9678f67256aa397e5416e58e86148a7f14f46f48",
    ("no_recovery", "lca_contributions"):
        "f88bf0fea4499647139b0c55264e45b3822f964c468d975482e5247fdd2f6e08",
    ("no_recovery", "lca_impacts"):
        "9d90ed7c0f0c9664c81e1f20b5ef8ee76edf67c50813c9fda14aa9d08e356902",
    ("no_recovery", "lca_inventory"):
        "c87f82fa8e127ff70e639aab98a63f17fec1d28f07f856d65c5c7baf870e32df",
    ("no_recovery", "opex"):
        "4a4e0025252c723000b8275e8ce510bfdc3b1044c6dc8fd7db127ec50a441f56",
    ("no_recovery", "power"):
        "d02dd6d43b905bb88a81b735653ca3a85ee77568e697f0dc3250aaccbe95553a",
    ("no_recovery", "reactor_capex"):
        "95711e448f5a4b5984bd21d57b3d88a6088132d3bf2b278914bc85db8586951f",
    ("no_recovery", "streams"):
        "b54ad9424b32807158826499034b16b92c6669ecc4b92c372c7fba0c27ce7693",
    ("no_recovery", "tea_capital"):
        "098404767f10a97e22083185aaa6af6b7ed35a42c520c3a93a83d04e1730ad51",
    ("no_recovery", "tea_com"):
        "59f6176a02f1ecd4499480a5700d63771b2aea54ef14940933551631e89e455f",
    ("no_recovery", "tea_metrics"):
        "cc4eb730790235f8af90bb47cda96beaac4f3bd311bf602bf29dbfe975b591df",
}
ORPHAN_SHA256 = {dl.scenario_file_name(dl.ORPHAN_SCENARIOS[0], config, table): digest
                 for (config, table), digest in _ORPHAN_HASHES.items()}    # registered file name -> SHA-256
# The superseded input versions (datalayer.SUPERSEDED key -> SHA-256). scenarios_v01 is byte-identical to the old
# repository's scenario file (data/scenarios in the frozen oracle) and to the Phase 1 sign-off manifest. Hashed, never read.
SUPERSEDED_SHA256 = {
    "scenarios_v01": "d99611d8f32081005f2559998bef7e5c5e15392519003ccf0f4a4bf84c88abb3",
    # Phase 3 (2026-10-05): the five predecessors, hashed as registered in the Phase-2 sign-off manifest
    "equipment_general_v01": "b80b844d83ea32fe1598d751def81cd62d7b6baa8eaf4191ec5b373d4ac74858",
    "process_assumptions_v01": "8bb5b96a1834a418ed077131c01f95d50d53dd3d886980889ecf2975ac68b604",
    "scenarios_v02": "f82f76220d20f23b029e189c9a1502b2b14e9672ee70116bffc7bc3b4bb4aa3c",
    "uncertainty_v01": "054dda2be44da5fe31a9776ad5b094f5e7341e9f09830a70050efff3ab4ee919",
    "benchmarks_v01": "4bac8b9f0e8d1e6527f2ece8b28322441ee338354f8737885912000489b93248",
    # Literature confirmation (2026-10-06): the predecessors of the restated text versions (values identical), as the
    # deposit manifests v02 / v03 record them
    "process_assumptions_v02": "6a1845d43f66fa306a93a379e0e5a810add25e6532252e74817e342037715888",
    "uncertainty_v02": "042e48f1280155df27fe25df3db544f3fbcc5326179f0c911162004000e43d41",
    "literature_benchmarks_v01": "aab71d2be0a216d6da2bf4405caf6f3e285a658aa51e6ab6368e4e22ebdf9912",
    "scaleup_ledger_v01": "873893e51dae288a9732fe4057760d009012d07d1a8c2e8c9088a2474beb999f",
}
# The superseded figures (datalayer.SUPERSEDED_FIGURES; documents v07, 2026-10-06): the seven figures of v06, PNG and
# PDF (the framework figure as regenerated by the v06 document revision; the other six as committed by Phase 3), keyed
# '<registry key>.<extension>' (no file-name literal in the package). They stay in 04_Figures with these SHA-256, never
# regenerated.
SUPERSEDED_FIGURE_SHA256 = {
    "fig1_framework.png": "2789843ab8d28e8b12221b352f111484a476730c542e9136437d2282113f721d",
    "fig1_framework.pdf": "706ae8186bcfde35def601b421a73b778e47f69fead1c65c860ff6e36391db97",
    "fig2_flowsheet.png": "2b82d2ae8bdd80f1273ae8b2024fdcbd7ff2b4bf7d132c3648fb14791ed645c2",
    "fig2_flowsheet.pdf": "623d73ce4ef2ed84ef77207fb888f7d1495c77f1fd5c0e8add409fb054775158",
    "fig3_lca_normalized.png": "e42c8174368d3f4a21860f7a0f3a4c15ef27d5f27dc7e0e0d78b006906c20238",
    "fig3_lca_normalized.pdf": "bce5fb6599ef01e83e0f506c199acd871a0456a265f1238355101151f53f288d",
    "fig4_cost_structure.png": "8c109431bd56a4d0d4e3c847a006576e536c29944fdbd2a72f306b6edd885cde",
    "fig4_cost_structure.pdf": "304918c54daaf932a111acf80339b36888216023553f548fb43db9a987e92593",
    "fig5_scale_vs_performance_gwp.png": "f5c6a568fdb7ea55071532b2d9d3e850dea542688c3d30e645a9ce4172aff5a0",
    "fig5_scale_vs_performance_gwp.pdf": "6a3799576c6a48c70b4a11effe282af7fe67acd67a8d699e211542be2e2f3cef",
    "fig6_scale_vs_performance_lcot.png": "062bc09f8f15595a4ed335c3f0da2a425d15cf9dc3160a4806502f175269fea9",
    "fig6_scale_vs_performance_lcot.pdf": "ebededd93c6d523cc17dc39200dc12f3007b7731f154816cdee2c61048e99659",
    "fig7_sensitivity.png": "b2a3c4121b17b87f37b89a86b74bef4edd9771fff05d8be08df810ada564086f",
    "fig7_sensitivity.pdf": "d85662c8910340792dae0473e837445407193b988573a3abc2a43faf88a83fcf",
}
EXPECTED_FACTOR_ROWS = 270      # Phase 1 freeze: 27 flow-dataset pairs x 10 impact categories
# Gaps the model reports today, {(flow, dataset_id): (missing impact categories, ...)}: io._lca_factors returns them
# per scenario and the model then reports those categories as unavailable instead of as zero. Measured on the frozen
# old package for all five scenarios: none. A gap that appears is a change of the data, not something to hide, so it
# has to be entered here on purpose.
KNOWN_GAPS: dict = {}
# Text values the loader deliberately keeps as text: (registry key, group, parameter).
KNOWN_TEXT_VALUES = frozenset({("materials", "Electrocultivation Reactor", "Material of construction")})

_FLAT_KEYS = ("unit_conversions", "influent", "prices", "tea_inputs")
_GROUPED_KEYS = ("process_assumptions", "materials", "equipment_general")
_FACTOR_KEYS = ("factors", "factors_template", "factors_dummy")
_FACTOR_COLUMNS = ("flow", "dataset_id", "ecoinvent_flow", "ecoinvent_activity", "location", "ref_amount",
                   "ref_unit", "impact_category", "value", "source_workbook")
_DESCRIPTOR_COLUMNS = ("flow", "dataset_id", "ecoinvent_flow", "ecoinvent_activity", "location", "ref_amount",
                       "ref_unit", "impact_category")           # everything of a factor row except value / labels
_CATALOGUES = {             # key -> (equipment_type, ascending max column, required numeric, optional numeric)
    "pumps": ("Pump", "max_flowrate_m3hr",
              ("min_flowrate_m3hr", "max_flowrate_m3hr", "max_head_m", "purchase_cost_usd", "nominal_power_kW"), ()),
    "centrifuges": ("Centrifuge", "max_flowrate_m3hr",
                    ("min_flowrate_m3hr", "max_flowrate_m3hr", "purchase_cost_usd", "power_kW"), ()),
    "screens": ("Rotary Fine Mesh Filter Screen", "max_flowrate_m3hr",
                ("min_flowrate_m3hr", "max_flowrate_m3hr", "purchase_cost_usd", "power_kW"), ()),
    "ec_mixers": ("Electrocultivation Mixer", "max_volume_L",
                  ("min_volume_L", "max_volume_L", "purchase_cost_usd"), ("power_kW",)),
}
_D05_TEXT_COLUMNS = ("condition", "window", "nitrate_present")

# The columns each input had in the old repository. Phase 1 appends the four provenance columns after them, so
# the header must start with exactly these (original columns first, in order).
ORIGINAL_COLUMNS = {
    "unit_conversions": ("parameter", "value", "unit", "source_type", "reference"),
    "influent": ("parameter", "value", "unit", "source_type", "reference"),
    "prices": ("parameter", "value", "unit", "source_type", "reference"),
    "tea_inputs": ("parameter", "value", "unit", "source_type", "reference"),
    "process_assumptions": ("group", "parameter", "value", "unit", "source_type", "reference"),
    "materials": ("group", "parameter", "value", "unit", "source_type", "reference"),
    "equipment_general": ("group", "parameter", "value", "unit", "source_type", "reference"),
    "pumps": ("equipment_type", "model_name", "min_flowrate_m3hr", "max_flowrate_m3hr", "max_head_m",
              "purchase_cost_usd", "nominal_power_kW", "notes"),
    "centrifuges": ("equipment_type", "model_name", "min_flowrate_m3hr", "max_flowrate_m3hr", "purchase_cost_usd",
                    "power_kW", "notes"),
    "screens": ("equipment_type", "model_name", "min_flowrate_m3hr", "max_flowrate_m3hr", "purchase_cost_usd",
                "power_kW", "notes"),
    "ec_mixers": ("equipment_type", "model_name", "min_volume_L", "max_volume_L", "purchase_cost_usd", "power_kW",
                  "notes"),
    "electrocultivation": ("group", "direction", "flow", "amount", "unit"),
    "foreground_processes": ("process", "flow", "amount", "unit"),
    "normalization": ("impact_category", "value", "unit"),
    "uncertainty": ("parameter", "distribution", "base", "min", "max", "reference"),
    "benchmarks": ("technology", "metric", "min", "max", "mean", "unit", "base_year", "interest_rate",
                   "lifetime_yr", "reference"),
    "factors": _FACTOR_COLUMNS,
    "factors_template": _FACTOR_COLUMNS,
    "factors_dummy": _FACTOR_COLUMNS,
    "sampling": ("key", "value"),                     # a new file: key, value, then note and the provenance columns
    "electron_partitioning": (
        "condition", "window", "voltage_V", "nitrate_present", "Q_total_C", "SeO3_in_mgL", "SeO4_in_mgL",
        "NO3_in_mgL", "RE_SeO3", "RE_SeO4", "RE_NO3", "Se0_produced_mg", "flow_mL_min", "duration_days",
        "V_total_L", "mol_SeO4_removed", "mol_SeO3_removed", "mol_NO3_removed", "mol_Se0_produced", "n_eff",
        "FE_Se_pct", "FE_NO3_pct", "parasitic_pct", "FE_Se", "FE_NO3"),
    # Phase 3 (2026-10-05). The CPI-U table is a copy of the BLS lookup: its columns year, cpi_u_annual_average and
    # basis come first; its extra column 'accessed' sits among the four provenance columns, as in the source.
    "cpi": ("year", "cpi_u_annual_average", "basis"),
    "scaleup_ledger": ("mechanism", "scale", "modeled_in", "treatment", "derating_target", "parameter", "evidence",
                       "justification"),
    "literature_benchmarks": (
        "study_key", "technology", "scale", "assessment", "functional_unit_as_reported", "boundary_as_reported",
        "metric", "value_as_reported", "unit_as_reported", "dollar_year", "harmonizable", "harmonized_value",
        "harmonized_unit", "adjustments_made", "not_harmonized_reason"),
}

# The columns the loaders (fcdibes.io, fcdibes.analysis.benchmarks, fcdibes.params) and the SI table read.
NEEDED_COLUMNS = {
    **{key: ("parameter", "value") for key in _FLAT_KEYS},
    **{key: ("group", "parameter", "value") for key in _GROUPED_KEYS},
    "normalization": ("impact_category", "value"),
    "electrocultivation": ("group", "direction", "flow", "amount"),
    "foreground_processes": ("process", "flow", "amount", "unit"),
    **{key: ("flow", "dataset_id", "impact_category", "value", "ref_unit") for key in _FACTOR_KEYS},
    "uncertainty": ("parameter", "distribution", "base", "min", "max"),
    "sampling": ("key", "value"),
    "benchmarks": ("technology", "metric", "min", "max", "mean", "unit", "base_year", "interest_rate", "lifetime_yr"),
    "cpi": ("year", "cpi_u_annual_average", "basis"),
    "scaleup_ledger": ("mechanism", "treatment", "derating_target", "parameter", "evidence", "justification"),
    "literature_benchmarks": ("study_key", "technology", "scale", "assessment", "metric", "value_as_reported",
                              "unit_as_reported", "dollar_year", "harmonizable", "harmonized_value", "harmonized_unit",
                              "adjustments_made", "not_harmonized_reason"),
    "pumps": ("model_name", "max_flowrate_m3hr", "purchase_cost_usd", "nominal_power_kW"),
    "centrifuges": ("model_name", "max_flowrate_m3hr", "purchase_cost_usd", "power_kW"),
    "screens": ("model_name", "max_flowrate_m3hr", "purchase_cost_usd", "power_kW"),
    "ec_mixers": ("model_name", "max_volume_L", "purchase_cost_usd", "power_kW"),
    "electron_partitioning": ("condition", "window", "voltage_V", "nitrate_present", "RE_SeO3", "RE_SeO4",
                              "FE_Se", "FE_NO3", "n_eff", "parasitic_pct"),
}

# Key columns per file (blank parts and duplicates are reported after the loader's own key normalisation).
KEY_COLUMNS = {
    **{key: ("parameter",) for key in _FLAT_KEYS},
    **{key: ("group", "parameter") for key in _GROUPED_KEYS},
    "normalization": ("impact_category",),
    "electrocultivation": ("group", "direction", "flow"),
    "foreground_processes": ("process", "flow"),
    **{key: ("flow", "dataset_id", "impact_category") for key in _FACTOR_KEYS},
    **{key: ("model_name",) for key in _CATALOGUES},
    "uncertainty": ("parameter",),
    "electron_partitioning": ("condition", "window"),
    "flows": ("flow_name",),
    "sampling": ("key",),
    "cpi": ("year",),
    "scaleup_ledger": ("mechanism",),
}

# SamplingSettings: the frozen Phase 1 values (key -> (kind, expected)). The runner reads them from the file; they
# equal the constants of the old code (run_all.py, MCSpec, ScalingSpec, GridSpec, sensitivity.run), so the file may
# not drift silently. kind: int, float, or text (expected None = any non-blank text).
SAMPLING_SETTINGS = {
    "method": ("text", None),
    "seed": ("int", 20260901),
    "n_draws": ("int", 1000),
    "n_draws_quick": ("int", 100),
    "distributions": ("text", "|".join(dl.DISTRIBUTIONS)),
    "oat_step": ("float", 0.1),
    "scaling_n_flow": ("int", 30),
    "scaling_n_flow_quick": ("int", 10),
    "scaling_n_draws": ("int", 100),
    "scaling_n_draws_quick": ("int", 20),
    "grid_n_flow": ("int", 28),
    "grid_n_axis": ("int", 28),
    "grid_n_flow_quick": ("int", 8),
    "grid_n_axis_quick": ("int", 8),
    "alt_grid_n_flow": ("int", 16),
    "alt_grid_n_axis": ("int", 16),
}

FAMILIES = ("registry", "exists", "header", "keys", "provenance", "numeric", "factors", "flows", "template",
            "scenarios", "uncertainty", "sampling", "orphans", "raw")
_SCOPES = ("project", "deposit")
_LIMIT = 10                     # rows listed per check before "... and N more"


# ---------------------------------------------------------------------------------------------- reading
class _Table:
    """One CSV file as text: header, records (lists of strings) and column access by name."""

    def __init__(self, key, path: Path, header: list, records: list):
        self.key, self.path, self.name = key, path, path.name
        self.header, self.records = header, records
        self._pos: dict = {}
        for i, column in enumerate(header):
            self._pos.setdefault(column, i)

    def has(self, column: str) -> bool:
        return column in self._pos

    def col(self, column: str):
        """The cells of one column (strings, '' for a short row), or None when the column is missing."""
        pos = self._pos.get(column)
        if pos is None:
            return None
        return [r[pos] if pos < len(r) else "" for r in self.records]

    def cols(self, *columns):
        """Row tuples over several columns, or None when any is missing."""
        parts = [self.col(c) for c in columns]
        return None if any(p is None for p in parts) else list(zip(*parts)) if parts else None

    @staticmethod
    def row_no(index: int) -> int:
        return index + 2                                    # header is row 1


def _read_table(key, path: Path) -> _Table:
    with open(path, newline="", encoding="utf-8-sig") as handle:      # accepts a BOM and no BOM
        records = [row for row in csv.reader(handle) if row]
    if not records:
        raise ValueError("the file is empty")
    return _Table(key, path, records[0], records[1:])


def _writable(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fmt(parts) -> str:
    parts = tuple(parts)
    return repr(parts[0]) if len(parts) == 1 else " / ".join(repr(p) for p in parts)


class _Ctx:
    """State of one validation run: where to read, the problems found so far, the tables read so far."""

    def __init__(self, data_dir, raw_dir, orphan_dir, scope):
        if scope is not None and scope not in _SCOPES:
            raise ValueError(f"scope must be one of {_SCOPES} (or None for automatic), not {scope!r}")
        self.data_dir = data_dir
        self.raw_dir = Path(raw_dir) if raw_dir is not None else dl.RAW_DIR
        self.orphan_dir = Path(orphan_dir) if orphan_dir is not None else dl.orphan_dir()
        if scope is None:            # a public clone carries the deposit marker at its root; anything else is the project
            scope = "deposit" if dl.is_deposit() else "project"
        self.scope = scope
        self.problems: dict = {family: [] for family in FAMILIES}
        self.blank_value_type: dict = {}
        self.pending: list = []                  # pending inputs (datalayer.PENDING_INPUTS) that are absent
        self._tables: dict = {}

    # -- reporting
    def add(self, family: str, where: str, reason: str, row=None) -> None:
        location = where if row is None else f"{where} row {row}"
        self.problems[family].append(f"[{family}] {location}: {reason}")

    def add_rows(self, family: str, where: str, findings: list) -> None:
        """findings = [(row number or None, reason)], listed up to _LIMIT then summarised."""
        for row, reason in findings[:_LIMIT]:
            self.add(family, where, reason, row)
        if len(findings) > _LIMIT:
            self.add(family, where, f"... and {len(findings) - _LIMIT} more rows with problems of this kind")

    # -- reading
    def path(self, key: str) -> Path:
        return dl.ref(key, self.data_dir)

    def table(self, key: str):
        """The registered CSV as a _Table, or None (missing files are reported by the 'exists' family)."""
        if key not in self._tables:
            self._tables[key] = self._load(key, self.path(key))
        return self._tables[key]

    def table_at(self, path: Path, label: str = "chosen factor table"):
        """A CSV that is not a registry key (an alternative factor table)."""
        ident = ("path", str(path))
        if ident not in self._tables:
            self._tables[ident] = self._load(label, path)
        return self._tables[ident]

    def _load(self, key, path: Path):
        if not path.is_file():
            return None
        try:
            return _read_table(key, path)
        except UnicodeDecodeError as exc:
            self.add("header", path.name, f"not valid UTF-8 ({exc.reason} at byte {exc.start}); "
                     "input files are read as utf-8-sig")
        except (csv.Error, ValueError, OSError) as exc:
            self.add("header", path.name, f"cannot be read as CSV: {exc}")
        return None

    def factor_reference(self):
        """The table the template / dictionary checks compare against: the licensed table, else the dummy."""
        licensed = self.table("factors")
        return licensed if licensed is not None else self.table("factors_dummy")


# ---------------------------------------------------------------------------------------------- helpers
def _pandas():
    import pandas as pd
    return pd


def _io():
    from . import io
    return io


def _classify(cells: list) -> list:
    """[(state, value)] with state ok (value is a finite float), blank, text or nonfinite, using the
    coercion the loaders use for value columns (io._numeric = pd.to_numeric(errors='coerce'))."""
    coerced = _io()._numeric(_pandas().Series(list(cells), dtype=object))
    out = []
    for raw, value in zip(cells, coerced):
        if isinstance(value, float):
            out.append(("ok", value) if math.isfinite(value) else ("nonfinite", raw))
        else:
            out.append(("blank" if str(value).strip() == "" else "text", raw))
    return out


def _factor_pairs(table: _Table):
    """{flow: set(dataset ids)} and {flow: set(ref units)} of a factor table (loader-style stripped flows)."""
    rows = table.cols("flow", "dataset_id", "ref_unit")
    datasets: dict = defaultdict(set)
    units: dict = defaultdict(set)
    for flow, dataset, unit in rows or ():
        datasets[flow.strip()].add(dataset)
        units[flow.strip()].add(unit)
    return datasets, units


def _guard(ctx: _Ctx, family: str, function) -> None:
    """Run one check; an unexpected exception becomes a message, so one corrupt file cannot hide the others."""
    try:
        function(ctx)
    except Exception as exc:                                  # noqa: BLE001
        ctx.add(family, "validation", f"internal error while checking ({type(exc).__name__}: {exc})")


# ---------------------------------------------------------------------------------------------- registry, exists
def _check_registry(ctx: _Ctx) -> None:
    for problem in dl.registry_problems():
        ctx.add("registry", "datalayer", problem)


def _check_exists(ctx: _Ctx) -> None:
    for key, name in dl.IN.items():
        path = ctx.path(key)
        if path.is_file():
            continue
        if key == "factors" and ctx.scope == "deposit" and not dl.is_default_factors():
            continue                                          # licensed table not shipped; another one is chosen
        if dl.is_pending(key):
            ctx.pending.append(name)                          # delivered by a later step of the revision (declared)
            continue
        ctx.add("exists", name, f"registered input is missing (expected at {path})")
    if ctx.scope == "deposit":
        return                                                # superseded versions are never published
    for name in sorted(set(dl.SUPERSEDED) ^ set(SUPERSEDED_SHA256)):
        ctx.add("exists", name, "superseded input key without a recorded SHA-256 (or a recorded SHA-256 without a key)")
    for key, name in dl.SUPERSEDED.items():
        path = dl.ref_dir(ctx.data_dir) / name                # hashed only: a superseded version is never parsed
        if not path.is_file():
            ctx.add("exists", name, f"superseded input is missing (expected at {path}); superseded versions stay on "
                    "disk")
            continue
        expected = SUPERSEDED_SHA256.get(key)
        digest = _sha256(path)
        if expected is not None and digest != expected:
            ctx.add("exists", name, f"SHA-256 is {digest}, the recorded value is {expected}; a superseded input "
                    "version never changes")
    # the superseded figures (documents v07): on disk in 04_Figures with their recorded SHA-256, never regenerated
    figures = {f"{key}.{ext}": (key, ext) for key in dl.SUPERSEDED_FIGURES for ext in dl.FIG_EXTS}
    for label in sorted(set(figures) ^ set(SUPERSEDED_FIGURE_SHA256)):
        ctx.add("exists", label, "superseded figure without a recorded SHA-256 (or a recorded SHA-256 without a "
                "registered superseded figure)")
    for label, (key, ext) in figures.items():
        name = dl.superseded_figure_name(key, ext)
        path = dl.superseded_figure(key, ext)                 # hashed only: a superseded figure is never loaded
        if not path.is_file():
            ctx.add("exists", name, f"superseded figure is missing (expected at {path}); superseded figures stay on disk")
            continue
        expected = SUPERSEDED_FIGURE_SHA256.get(label)
        digest = _sha256(path)
        if expected is not None and digest != expected:
            ctx.add("exists", name, f"SHA-256 is {digest}, the recorded value is {expected}; a superseded figure "
                    "never changes")


# ---------------------------------------------------------------------------------------------- header
def _check_header(ctx: _Ctx) -> None:
    for key in dl.IN:
        if key == "scenarios":
            continue
        table = ctx.table(key)
        if table is None:
            continue
        name = table.name
        duplicated = [c for c, n in Counter(table.header).items() if n > 1]
        for column in duplicated:
            ctx.add("header", name, f"column {column!r} appears more than once in the header")
        width = len(table.header)
        ragged = [(table.row_no(i), f"has {len(r)} fields, the header has {width} (an unquoted comma or a "
                   "missing field?)") for i, r in enumerate(table.records) if len(r) != width]
        ctx.add_rows("header", name, ragged)
        if not table.records:
            ctx.add("header", name, "has a header but no data rows")

        if key == "flows":
            if tuple(table.header) != FLOW_COLUMNS:
                ctx.add("header", name, f"columns are {table.header}, expected exactly {list(FLOW_COLUMNS)}")
            continue

        needed = NEEDED_COLUMNS.get(key, ())
        for column in needed:
            if not table.has(column):
                ctx.add("header", name, f"missing column {column!r}, which the loader / model needs")
        original = ORIGINAL_COLUMNS.get(key)
        if original is None:
            continue
        for column in PROVENANCE_COLUMNS:
            if not table.has(column):
                ctx.add("header", name, f"missing provenance column {column!r} "
                        f"(the appended columns are {list(PROVENANCE_COLUMNS)})")
        if tuple(table.header[:len(original)]) != original:
            ctx.add("header", name, f"the original columns must come first and in order: expected "
                    f"{list(original)}, found {table.header[:len(original)]}")


# ---------------------------------------------------------------------------------------------- keys
def _unique(ctx: _Ctx, table: _Table, columns: tuple, label: str = "", subset=None) -> None:
    """Report blank and duplicate keys of `table` over `columns` (after the loader's key normalisation).
    `subset` limits the check to the record indexes it lists."""
    rows = table.cols(*columns)
    if rows is None:
        return                                                # missing columns are a header problem
    norm = _io()._key
    seen: dict = {}
    findings = []
    for i, parts in enumerate(rows):
        if subset is not None and i not in subset:
            continue
        key = tuple(norm(p) for p in parts)
        blank = [c for c, p in zip(columns, key) if p == ""]
        if blank:
            findings.append((table.row_no(i), f"blank key column(s) {blank}"))
        elif key in seen:
            findings.append((table.row_no(i), f"duplicate key {_fmt(key)}{label}: same as row {seen[key]}"))
        else:
            seen[key] = table.row_no(i)
    ctx.add_rows("keys", table.name, findings)


def _check_keys(ctx: _Ctx) -> None:
    for key, columns in KEY_COLUMNS.items():
        table = ctx.table(key)
        if table is not None:
            _unique(ctx, table, columns)

    # catalogues: one equipment type per file, sorted ascending by the column the model selects on
    for key, (equipment_type, max_col, _required, _optional) in _CATALOGUES.items():
        table = ctx.table(key)
        if table is None:
            continue
        kinds = table.col("equipment_type")
        findings = [(table.row_no(i), f"equipment_type is {v!r}, this catalogue holds {equipment_type!r} only")
                    for i, v in enumerate(kinds or ()) if v != equipment_type]
        state = _classify(table.col(max_col)) if table.has(max_col) else []
        previous = None
        for i, (status, value) in enumerate(state):
            if status != "ok":
                continue
            if previous is not None and value <= previous:
                findings.append((table.row_no(i), f"{max_col} {value:g} is not above the previous row's "
                                 f"{previous:g}: the model takes the first model that fits, so the catalogue "
                                 "must be sorted ascending"))
            previous = value
        ctx.add_rows("keys", table.name, findings)

    # direction vocabulary of the electrocultivation inventory (any other word drops the row silently)
    table = ctx.table("electrocultivation")
    if table is not None and table.has("direction"):
        ctx.add_rows("keys", table.name, [
            (table.row_no(i), f"direction {v!r} is not one of {list(DIRECTIONS)}; the model would ignore the row")
            for i, v in enumerate(table.col("direction")) if _io()._key(v) not in DIRECTIONS])

    # benchmarks (v02, Phase 3): only the as-reported rows (2010 USD per 1000 gal); the model converts every row to
    # USD per m3 and escalates it with the CPI-U table, so another unit would be converted wrongly (it is an error)
    table = ctx.table("benchmarks")
    if table is not None and table.has("unit") and table.has("metric") and table.has("technology"):
        from .analysis import benchmarks as bm
        metrics = (bm.CAPEX_METRIC, bm.OPEX_METRIC, bm.TOTAL_METRIC)
        units = table.col("unit")
        _unique(ctx, table, ("technology", "metric"), " (technology / metric)")
        findings = [(table.row_no(i), f"unit {u!r} is not one of {list(BENCHMARK_UNITS)}: the model reads every row "
                     "as reported in that unit and converts it") for i, u in enumerate(units) if u not in BENCHMARK_UNITS]
        findings += [(table.row_no(i), f"metric {m!r} is not one of {list(metrics)}")
                     for i, m in enumerate(table.col("metric")) if m not in metrics]
        for technology in sorted(set(table.col("technology"))):
            present = {m for t, m in zip(table.col("technology"), table.col("metric")) if t == technology}
            for metric in metrics:
                if metric not in present:
                    findings.append((None, f"technology {technology!r} has no row for metric {metric!r}"))
        ctx.add_rows("keys", table.name, findings)
        _check_cpi_years(ctx, table)

    _check_scale_up_group(ctx)
    _check_ledger(ctx)
    _check_literature(ctx)


def _cpi_years(ctx: _Ctx):
    """{year: value} of the CPI-U table (numeric cells only), or None when it cannot be read."""
    cpi = ctx.table("cpi")
    if cpi is None or not (cpi.has("year") and cpi.has("cpi_u_annual_average")):
        return None
    years = {}
    for (s_year, year), (s_value, value) in zip(_classify(cpi.col("year")), _classify(cpi.col("cpi_u_annual_average"))):
        if s_year == "ok" and s_value == "ok":
            years[int(year)] = value
    return years


def _check_cpi_years(ctx: _Ctx, table: _Table) -> None:
    """Every benchmark dollar year and the target dollar year of the escalation must be rows of the CPI-U table."""
    from .analysis import benchmarks as bm
    years = _cpi_years(ctx)
    if years is None or not table.has("base_year"):
        return
    findings = [(table.row_no(i), f"base_year {raw!r} is not a year of the CPI-U table ({sorted(years)}): the cost "
                 "cannot be escalated") for i, (status, value), raw in
                zip(range(len(table.records)), _classify(table.col("base_year")), table.col("base_year"))
                if status == "ok" and int(value) not in years]
    ctx.add_rows("keys", table.name, findings)
    if bm.TARGET_DOLLAR_YEAR not in years:
        ctx.add("keys", ctx.path("cpi").name, f"the target dollar year {bm.TARGET_DOLLAR_YEAR} of the benchmark "
                "escalation (analysis.benchmarks.TARGET_DOLLAR_YEAR) is not a row of the CPI-U table")


def _check_scale_up_group(ctx: _Ctx) -> None:
    """The seven parameters of ProcessAssumptions group 'Scale-up' (Phase 3) that the reactor and the ledger read."""
    table = ctx.table("process_assumptions")
    rows = table.cols("group", "parameter") if table is not None else None
    if rows is None:
        return
    norm = _io()._key
    present = {norm(p) for g, p in rows if norm(g) == "Scale-up"}
    for name in SCALE_UP_PARAMETERS:
        if name not in present:
            ctx.add("keys", table.name, f"group 'Scale-up' has no parameter {name!r}: the reactor model reads it")


def _check_ledger(ctx: _Ctx) -> None:
    """The loss ledger D09 (documentation): vocabularies, a justification on every row, parameter names that exist."""
    table = ctx.table("scaleup_ledger")
    if table is None or table.cols("treatment", "derating_target", "parameter", "justification", "evidence") is None:
        return
    pa = ctx.table("process_assumptions")
    norm = _io()._key
    pairs = (pa.cols("group", "parameter") or ()) if pa is not None else ()
    scale_up = {norm(p) for g, p in pairs if norm(g) == "Scale-up"}
    findings = []
    for i, (treatment, target, parameter, justification, evidence) in enumerate(
            table.cols("treatment", "derating_target", "parameter", "justification", "evidence")):
        row = table.row_no(i)
        if treatment not in LEDGER_TREATMENTS:
            findings.append((row, f"treatment {treatment!r} is not one of {list(LEDGER_TREATMENTS)}"))
        if target not in LEDGER_TARGETS:
            findings.append((row, f"derating_target {target!r} is not one of {list(LEDGER_TARGETS)}"))
        if not justification.strip():
            findings.append((row, "justification is blank (a mechanism may be design basis or not modelled only with a "
                             "stated justification)"))
        if treatment == "sampled" and not parameter.strip():
            findings.append((row, "a sampled mechanism must name the Scale-up parameter that carries it"))
        for name in filter(None, (p.strip() for p in parameter.split(";"))):
            if name not in scale_up:
                findings.append((row, f"parameter {name!r} is not a parameter of ProcessAssumptions group 'Scale-up'"))
    ctx.add_rows("keys", table.name, findings)


def _check_literature(ctx: _Ctx) -> None:
    """The literature table (D08, Phase 3): vocabularies and the harmonisation rules of DESIGN_phase3 section 3.6."""
    table = ctx.table("literature_benchmarks")
    columns = tuple(LITERATURE_ENUMS) + ("harmonized_value", "not_harmonized_reason", "value_type", "study_key",
                                         "metric", "dollar_year")
    if table is None or any(not table.has(c) for c in columns):
        return
    findings = []
    records = [{c: (r[table._pos[c]] if table._pos[c] < len(r) else "") for c in columns} for r in table.records]
    years = _cpi_years(ctx) or {}
    seen = {}
    for i, rec in enumerate(records):
        row = table.row_no(i)
        for column, allowed in LITERATURE_ENUMS.items():
            if rec[column] not in allowed:
                findings.append((row, f"{column} {rec[column]!r} is not one of {[a for a in allowed if a]}"
                                 + (" (or blank)" if "" in allowed else "")))
        if not rec["study_key"].strip():
            findings.append((row, "blank study_key"))
        value = rec["harmonized_value"].strip()
        if rec["harmonizable"] == "no":
            if value:
                findings.append((row, f"harmonized_value {value!r} given although harmonizable is 'no'"))
            if not rec["not_harmonized_reason"].strip():
                findings.append((row, "harmonizable is 'no' but not_harmonized_reason is blank"))
        if rec["value_type"] == "unverified" and value:
            findings.append((row, "an unverified value carries a harmonized value (it must stay blank until confirmed)"))
        if value and not rec["harmonized_unit"]:                  # the number itself: family 'numeric'
            findings.append((row, "a harmonized value without harmonized_unit"))
        year = rec["dollar_year"].strip()
        if year:
            ((status, number),) = _classify([year])
            if status != "ok":
                findings.append((row, f"dollar_year {year!r} is not a year"))
            elif value and rec["harmonized_unit"] == "USD2026/m3" and int(number) not in years:
                findings.append((row, f"dollar_year {year} is not a year of the CPI-U table: the escalation factor "
                                 "of a harmonized cost must come from it"))
        ident = tuple(r for r in table.records[i])
        if ident in seen:
            findings.append((row, f"repeats row {seen[ident]}"))
        seen.setdefault(ident, row)
    ctx.add_rows("keys", table.name, findings)


# ---------------------------------------------------------------------------------------------- provenance
def _check_provenance(ctx: _Ctx) -> None:
    for key in list(ORIGINAL_COLUMNS) + ["flows"]:
        table = ctx.table(key)
        if table is None:
            continue
        findings = []
        columns = ("note",) if key == "flows" else PROVENANCE_COLUMNS
        for column in columns:
            cells = table.col(column)
            if cells is None:
                continue
            for i, cell in enumerate(cells):
                if cell.strip().lower() in NA_LITERALS:
                    findings.append((table.row_no(i), f"column {column!r} holds the NA-like literal {cell!r}, "
                                     "which is read back as a missing value; leave the cell blank instead"))
        ctx.add_rows("provenance", table.name, findings)
        if key == "flows":
            continue
        cells = table.col("value_type")
        if cells is None:
            continue
        findings = [(table.row_no(i), f"value_type {v!r} is not in the vocabulary {list(VALUE_TYPES)} "
                     "(or blank)") for i, v in enumerate(cells) if v != "" and v not in VALUE_TYPES]
        ctx.add_rows("provenance", table.name, findings)
        ctx.blank_value_type[table.name] = (sum(1 for v in cells if v == ""), len(cells))


# ---------------------------------------------------------------------------------------------- numeric
def _numeric_column(ctx: _Ctx, table: _Table, column: str, required: bool, known_text=frozenset()) -> list:
    """Check one numeric column; returns its [(state, value)] list ([] when the column is missing).
    known_text holds (registry key, group, parameter) rows whose text value is deliberate."""
    cells = table.col(column)
    if cells is None:
        return []
    state = _classify(cells)
    ident = [c for c in ("group", "parameter", "flow", "dataset_id", "model_name", "technology", "metric",
                         "condition", "process", "impact_category") if table.has(c)][:3]
    findings = []
    for i, (status, value) in enumerate(state):
        if status == "ok":
            continue
        record = table.records[i]
        labels = {c: (record[table._pos[c]] if table._pos[c] < len(record) else "") for c in ident}
        if status == "text" and (table.key, labels.get("group"), labels.get("parameter")) in known_text:
            continue
        where = "; ".join(f"{c}={v!r}" for c, v in labels.items())
        if status == "blank":
            if required:
                findings.append((table.row_no(i), f"column {column!r} is blank ({where})"))
        else:
            kind = "text" if status == "text" else "not finite"
            findings.append((table.row_no(i), f"column {column!r} holds {value!r}, which is {kind}, not a "
                             f"number ({where})"))
    ctx.add_rows("numeric", table.name, findings)
    return state


def _check_numeric(ctx: _Ctx) -> None:
    for key in _FLAT_KEYS + _GROUPED_KEYS + ("normalization",):
        table = ctx.table(key)
        if table is not None:
            _numeric_column(ctx, table, "value", True, KNOWN_TEXT_VALUES)
    table = ctx.table("electrocultivation")
    if table is not None:
        _numeric_column(ctx, table, "amount", True)
    table = ctx.table("foreground_processes")
    if table is not None:
        _numeric_column(ctx, table, "amount", True)
    for key, (_type, _max, required, optional) in _CATALOGUES.items():
        table = ctx.table(key)
        if table is not None:
            for column in required:
                _numeric_column(ctx, table, column, True)
            for column in optional:
                _numeric_column(ctx, table, column, False)
    table = ctx.table("uncertainty")
    if table is not None:
        for column in ("base", "min", "max"):
            _numeric_column(ctx, table, column, True)
    table = ctx.table("benchmarks")
    if table is not None:
        # v02 (Phase 3): the dollar year and the source annualisation basis (interest rate, lifetime) of every row are
        # read by the escalation and the CRF re-levelization, so they are required; min / max are blank where the
        # source reports none
        for column in ("mean", "base_year", "interest_rate", "lifetime_yr"):
            _numeric_column(ctx, table, column, True)
        for column in ("min", "max"):
            _numeric_column(ctx, table, column, False)
    table = ctx.table("cpi")
    if table is not None:
        _numeric_column(ctx, table, "year", True)
        state = _numeric_column(ctx, table, "cpi_u_annual_average", True)
        ctx.add_rows("numeric", table.name, [(table.row_no(i), f"cpi_u_annual_average {v!r} is not positive")
                                             for i, (s, v) in enumerate(state) if s == "ok" and not v > 0])
    _check_scale_up_ranges(ctx)
    table = ctx.table("literature_benchmarks")
    if table is not None:
        _numeric_column(ctx, table, "harmonized_value", False)
    table = ctx.table("electron_partitioning")
    if table is not None:
        for column in ORIGINAL_COLUMNS["electron_partitioning"]:
            if column not in _D05_TEXT_COLUMNS:
                _numeric_column(ctx, table, column, column == "voltage_V")
    # factor tables: value is required for the licensed / dummy table (the template is blank by design)
    for key in ("factors", "factors_dummy"):
        table = ctx.table(key)
        if table is not None:
            _numeric_column(ctx, table, "value", True)
    for key in _FACTOR_KEYS:
        table = ctx.table(key)
        if table is None or not table.has("ref_amount"):
            continue
        state = _numeric_column(ctx, table, "ref_amount", True)
        ctx.add_rows("numeric", table.name, [
            (table.row_no(i), f"ref_amount is {v:g}: the model reads every factor as per 1 reference unit and never "
             "looks at ref_amount, so it must be 1") for i, (s, v) in enumerate(state) if s == "ok" and v != 1.0])


def _check_scale_up_ranges(ctx: _Ctx) -> None:
    """The Scale-up parameters (Phase 3) inside their physical ranges: the model divides by 1 - shunt fraction and by
    the retained current density, so a value outside the range is an error, not a modelling choice."""
    table = ctx.table("process_assumptions")
    rows = table.cols("group", "parameter", "value") if table is not None else None
    if rows is None:
        return
    norm = _io()._key
    findings = []
    for i, (group, parameter, raw) in enumerate(rows):
        bounds = SCALE_UP_PARAMETERS.get(norm(parameter)) if norm(group) == "Scale-up" else None
        if bounds is None:
            continue
        ((status, value),) = _classify([raw])
        if status != "ok":
            continue                                              # reported by the numeric check of 'value'
        low, high, kind = bounds
        ok = ((value > low if kind.startswith("(") else value >= low)
              and (value <= high if kind.endswith("]") else value < high))
        if not ok:
            findings.append((table.row_no(i), f"Scale-up parameter {parameter!r} = {raw} is outside {kind} with "
                             f"lo = {low:g}, hi = {high:g}"))
    ctx.add_rows("numeric", table.name, findings)


# ---------------------------------------------------------------------------------------------- factors
def _chosen_factor_table(ctx: _Ctx):
    """(table, is_registered_key) of the factor table the model will read (LCA_FCDI_FACTORS); cached."""
    if "_chosen" in ctx._tables:
        return ctx._tables["_chosen"]
    mode = dl.factors_mode()
    if mode == "default":
        result = (ctx.table("factors"), True)
    elif mode == "dummy":
        result = (ctx.table("factors_dummy"), True)
    else:
        path = dl.factors_path(ctx.data_dir)
        if path.is_file():
            result = (ctx.table_at(path), False)
        else:
            ctx.add("factors", path.name, f"the alternative factor table selected by {dl.ENV_FACTORS} is missing "
                    f"(looked for {path})")
            result = (None, False)
    ctx._tables["_chosen"] = result
    return result


def _check_factor_structure(ctx: _Ctx, table: _Table) -> None:
    """Impact categories and completeness of one factor table (values may be blank, as in the template)."""
    from .config import IMPACT_CATEGORIES
    rows = table.cols("flow", "dataset_id", "impact_category")
    if rows is None:
        return
    name = table.name
    configured = set(IMPACT_CATEGORIES)
    first_row: dict = {}
    for i, (_flow, _dataset, category) in enumerate(rows):
        first_row.setdefault(category, table.row_no(i))
    for category in sorted(set(first_row) - configured):
        ctx.add("factors", name, f"impact category {category!r} is not one of the {len(configured)} categories "
                "of config.IMPACT_CATEGORIES", first_row[category])
    for category in IMPACT_CATEGORIES:
        if category not in first_row:
            ctx.add("factors", name, f"impact category {category!r} of config.IMPACT_CATEGORIES never appears")
    pairs: dict = defaultdict(set)
    for flow, dataset, category in rows:
        pairs[(flow.strip(), dataset)].add(category)
    for (flow, dataset), categories in pairs.items():
        missing = tuple(c for c in IMPACT_CATEGORIES if c not in categories)
        if missing != tuple(KNOWN_GAPS.get((flow, dataset), ())):
            ctx.add("factors", name, f"flow {flow!r} dataset {dataset!r} lacks {len(missing)} of "
                    f"{len(configured)} impact categories: {list(missing)}"
                    + (" (not a known gap)" if missing else " (KNOWN_GAPS says it should)"))


def _check_factors(ctx: _Ctx) -> None:
    from .config import IMPACT_CATEGORIES, normalization_key
    io = _io()
    for key in _FACTOR_KEYS:
        table = ctx.table(key)
        if table is not None:
            _check_factor_structure(ctx, table)
    chosen, registered = _chosen_factor_table(ctx)
    if chosen is not None and not registered:
        _check_factor_structure(ctx, chosen)
        _unique(ctx, chosen, ("flow", "dataset_id", "impact_category"))
        _numeric_column(ctx, chosen, "value", True)

    licensed = ctx.table("factors")
    if licensed is not None:
        allowed = set(dl.RAW_LABEL.values())
        labels = licensed.col("source_workbook")
        if labels is not None and ctx.scope != "deposit":    # the authors' provenance labels; a licensee has none
            ctx.add_rows("factors", licensed.name, [
                (licensed.row_no(i), f"source_workbook {v!r} is not one of the recorded workbook names "
                 f"{sorted(allowed)}") for i, v in enumerate(labels) if v not in allowed])
        rows = licensed.cols("flow", "dataset_id", "impact_category")
        if rows is not None and len(rows) != EXPECTED_FACTOR_ROWS:
            ctx.add("factors", licensed.name, f"has {len(rows)} rows, expected {EXPECTED_FACTOR_ROWS} "
                    "(27 flow-dataset pairs x 10 impact categories, frozen in Phase 1)")
        units: dict = defaultdict(set)
        for flow, unit in licensed.cols("flow", "ref_unit") or ():
            units[flow.strip()].add(unit)
        ctx.add_rows("factors", licensed.name, [
            (None, f"flow {flow!r} has several reference units {sorted(us)}") for flow, us in units.items()
            if len(us) > 1])

    # the normalisation table must cover every configured category (the model skips missing ones silently)
    norm = ctx.table("normalization")
    if norm is not None and norm.has("impact_category"):
        names = {io._key(v) for v in norm.col("impact_category")}
        wanted = {normalization_key(c) for c in IMPACT_CATEGORIES}
        for missing in sorted(wanted - names):
            ctx.add("factors", norm.name, f"no normalisation factor for impact category {missing!r}: "
                    "the model would silently drop its normalised score")
        for extra in sorted(names - wanted):
            ctx.add("factors", norm.name, f"impact category {extra!r} is not one of the configured categories")

    # the gaps the model itself reports, per scenario, must be exactly the known ones
    if chosen is not None:
        _check_model_gaps(ctx, chosen)


def _scenarios(ctx: _Ctx):
    """The YAML scenarios mapping, or None (a problem is reported once)."""
    if "_scenarios" in ctx._tables:
        return ctx._tables["_scenarios"]
    result = None
    path = ctx.path("scenarios")
    if path.is_file():
        try:
            import yaml
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or not isinstance(document.get("scenarios"), dict):
                ctx.add("scenarios", path.name, "must be a mapping with a top-level 'scenarios' mapping")
            else:
                result = document["scenarios"]
        except Exception as exc:                                  # noqa: BLE001
            ctx.add("scenarios", path.name, f"cannot be parsed as YAML ({type(exc).__name__}: {exc})")
    ctx._tables["_scenarios"] = result
    return result


def _resolved(ctx: _Ctx, name: str, scenarios: dict):
    """io.resolve_scenario(name) or None; an unresolvable inheritance chain is reported once."""
    ident = ("resolved", name)
    if ident not in ctx._tables:
        result = None
        try:
            result = _io().resolve_scenario(name, scenarios)
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            detail = exc.args[0] if exc.args else exc
            ctx.add("scenarios", ctx.path("scenarios").name, f"scenario {name!r}: inheritance cannot be "
                    f"resolved ({detail})")
        ctx._tables[ident] = result
    return ctx._tables[ident]


def _expected_gaps(datasets: dict, overrides) -> dict:
    """The KNOWN_GAPS the model should report for one scenario: those of the dataset it picks for each flow."""
    picks = {str(k).strip(): v for k, v in (overrides or {}).items()}
    expected = {}
    for (flow, dataset), categories in KNOWN_GAPS.items():
        picked = "default" if datasets.get(flow) == {"default"} else picks.get(flow)
        if picked == dataset:
            expected[flow] = tuple(categories)
    return expected


def _check_model_gaps(ctx: _Ctx, chosen: _Table) -> None:
    """Run io._lca_factors for every scenario on the chosen table; the gaps it reports must be the known ones."""
    io = _io()
    scenarios = _scenarios(ctx)
    if not scenarios or not (chosen.has("flow") and chosen.has("dataset_id")):
        return
    datasets, _units = _factor_pairs(chosen)
    for name in scenarios:
        spec = _resolved(ctx, name, scenarios)
        if spec is None:
            continue
        overrides = spec.get("lca_dataset_overrides")
        try:
            _factors, gaps = io._lca_factors(chosen.path, overrides)
        except ValueError as exc:
            ctx.add("scenarios", chosen.name, f"scenario {name!r}: the model would stop here: {exc}")
            continue
        except Exception as exc:                              # noqa: BLE001
            ctx.add("factors", chosen.name, f"io._lca_factors failed for scenario {name!r} "
                    f"({type(exc).__name__}: {exc})")
            continue
        reported = {flow: tuple(categories) for flow, categories in gaps.items()}
        expected = _expected_gaps(datasets, overrides)
        if reported != expected:
            ctx.add("factors", chosen.name, f"scenario {name!r}: the model reports the gaps {reported}, the "
                    f"known gaps are {expected}")


# ---------------------------------------------------------------------------------------------- flows
def _check_flows(ctx: _Ctx) -> None:
    table = ctx.table("flows")
    if table is None:
        return
    name = table.name
    records = [{c: (r[table._pos[c]] if table._pos[c] < len(r) else "") for c in table.header}
               for r in table.records]
    if not all(c in table._pos for c in FLOW_COLUMNS):
        return                                                # header problem already reported
    names = {r["flow_name"] for r in records}
    by_name = {r["flow_name"]: r for r in records}
    registered_files = set(dl.IN.values())
    findings = []
    for i, r in enumerate(records):
        row = table.row_no(i)
        if r["flow_name"].strip() == "":
            findings.append((row, "blank flow_name"))
        if r["flow_class"] not in FLOW_CLASSES:
            findings.append((row, f"flow_class {r['flow_class']!r} is not one of {list(FLOW_CLASSES)}"))
        if r["factor_source"] not in FACTOR_SOURCES:
            findings.append((row, f"factor_source {r['factor_source']!r} is not one of {list(FACTOR_SOURCES)}"))
        if r["unit"].strip() == "":
            findings.append((row, f"flow {r['flow_name']!r} has no unit"))
        alias = r["alias_of"]
        if alias:
            if alias not in names:
                findings.append((row, f"alias_of {alias!r} does not exist in the dictionary"))
            else:
                seen, current = {r["flow_name"]}, alias
                while current in by_name and by_name[current]["alias_of"]:
                    if current in seen:
                        findings.append((row, f"alias_of chain of {r['flow_name']!r} is circular"))
                        break
                    seen.add(current)
                    current = by_name[current]["alias_of"]
                if alias == r["flow_name"]:
                    findings.append((row, "alias_of points at the flow itself"))
        for token in filter(None, (t.strip() for t in r["used_by"].split("|"))):
            if token not in registered_files:
                findings.append((row, f"used_by names {token!r}, which is not a registered input file"))
    ctx.add_rows("flows", name, findings)

    def canonical(flow: str):
        seen = set()
        while flow in by_name and by_name[flow]["alias_of"] and flow not in seen:
            seen.add(flow)
            flow = by_name[flow]["alias_of"]
        return flow

    # units: dictionary unit == ref_unit of the factor table for ecoinvent37 flows (aliases resolve first)
    reference = ctx.factor_reference()
    if reference is not None and reference.has("flow") and reference.has("ref_unit"):
        datasets, units = _factor_pairs(reference)
        findings = []
        for i, r in enumerate(records):
            if r["alias_of"] or r["factor_source"] != "ecoinvent37":
                continue
            flow = r["flow_name"]
            if flow not in units:
                findings.append((table.row_no(i), f"flow {flow!r} is marked ecoinvent37 but has no rows in the "
                                 "factor table"))
            elif units[flow] != {r["unit"]}:
                findings.append((table.row_no(i), f"unit {r['unit']!r} differs from ref_unit "
                                 f"{sorted(units[flow])} of the factor table"))
        ctx.add_rows("flows", name, findings)

        # every flow of the factor tables (licensed and chosen) is in the dictionary
        findings = []
        tables = [t for t in (ctx.table("factors"), _chosen_factor_table(ctx)[0]) if t is not None]
        seen_tables = set()
        for factor_table in tables:
            if factor_table.path in seen_tables:
                continue
            seen_tables.add(factor_table.path)
            for flow in sorted({f.strip() for f in factor_table.col("flow") or ()}):
                if flow not in names:
                    findings.append((None, f"flow {flow!r} of {factor_table.name} is not in the dictionary"))
        ctx.add_rows("flows", name, findings)

        # foreground amounts are per reference unit of the factor table: units must agree
        fg = ctx.table("foreground_processes")
        if fg is not None and fg.cols("process", "flow", "unit") is not None:
            findings = []
            for i, (process, flow, unit) in enumerate(fg.cols("process", "flow", "unit")):
                flow, process = flow.strip(), process.strip()
                if flow.lower() == process.lower():
                    continue                                  # the process's own output row
                target = canonical(flow)
                if target in units and units[target] != {unit.strip()}:
                    findings.append((fg.row_no(i), f"unit {unit!r} of flow {flow!r} differs from the ref_unit "
                                     f"{sorted(units[target])} of the factor table"))
            ctx.add_rows("flows", fg.name, findings)

    # every flow name used by foreground, inventory and prices is in the dictionary
    for key, columns in (("foreground_processes", ("process", "flow")), ("electrocultivation", ("flow",)),
                         ("prices", ("parameter",))):
        source = ctx.table(key)
        if source is None:
            continue
        findings = []
        for column in columns:
            for i, value in enumerate(source.col(column) or ()):
                if value.strip() not in names:
                    findings.append((source.row_no(i), f"{column} {value!r} is not in the flow dictionary"))
        ctx.add_rows("flows", source.name, findings)


# ---------------------------------------------------------------------------------------------- template, dummy
def _check_template(ctx: _Ctx) -> None:
    """Template and dummy: 270 rows, the key set and order of the licensed table, dummy all 1.0, template blank.
    (Without the licensed table, as in a deposit clone, the dummy is the reference the template follows.)"""
    licensed = ctx.table("factors")
    template = ctx.table("factors_template")
    dummy = ctx.table("factors_dummy")
    reference = licensed if licensed is not None else dummy
    if reference is None:
        return
    key_columns = ("flow", "dataset_id", "impact_category")
    ref_keys = reference.cols(*key_columns)
    ref_desc = reference.cols(*_DESCRIPTOR_COLUMNS)
    ref_set = set(ref_keys or ())
    for table, label in ((template, "template"), (dummy, "dummy")):
        if table is None:
            continue
        name = table.name
        keys = table.cols(*key_columns)
        if keys is None:
            continue
        if len(keys) != EXPECTED_FACTOR_ROWS:
            ctx.add("template", name, f"the {label} has {len(keys)} rows, expected {EXPECTED_FACTOR_ROWS} "
                    "(every flow-dataset pair x every impact category; fewer rows silently ignore scenario "
                    "dataset overrides)")
        if table is not reference and ref_keys is not None:
            key_set = set(keys)
            missing = [k for k in ref_keys if k not in key_set]
            extra = [k for k in keys if k not in ref_set]
            for k in missing[:_LIMIT]:
                ctx.add("template", name, f"key {_fmt(k)} of {reference.name} is missing from the {label}")
            for k in extra[:_LIMIT]:
                ctx.add("template", name, f"key {_fmt(k)} is not in {reference.name}")
            if len(missing) > _LIMIT or len(extra) > _LIMIT:
                ctx.add("template", name, f"... {len(missing)} missing and {len(extra)} unexpected keys in all")
            if not missing and not extra and len(keys) == len(ref_keys):
                if keys != ref_keys:
                    first = next(i for i, (a, b) in enumerate(zip(keys, ref_keys)) if a != b)
                    ctx.add("template", name, f"same keys as {reference.name} but in a different order (first "
                            f"difference: {_fmt(keys[first])} where {_fmt(ref_keys[first])} is expected)",
                            table.row_no(first))
                else:
                    desc = table.cols(*_DESCRIPTOR_COLUMNS)
                    if desc is not None and ref_desc is not None:
                        ctx.add_rows("template", name, [
                            (table.row_no(i), "flow descriptors (ecoinvent flow / activity / location / ref amount "
                             f"/ ref unit) differ from {reference.name}")
                            for i, (a, b) in enumerate(zip(desc, ref_desc)) if a != b])
            if list(table.header) != list(reference.header):
                ctx.add("template", name, f"columns {table.header} differ from those of {reference.name} "
                        f"{reference.header}")
        values = table.col("value")
        if values is None:
            continue
        if label == "template":
            ctx.add_rows("template", name, [
                (table.row_no(i), f"template value must be blank, found {v!r}")
                for i, v in enumerate(values) if v.strip() != ""])
        else:
            findings = []
            for i, (status, value) in enumerate(_classify(values)):
                if status != "ok" or value != 1.0:
                    findings.append((table.row_no(i), f"dummy value must be 1.0, found {values[i]!r}"))
            ctx.add_rows("template", name, findings)


# ---------------------------------------------------------------------------------------------- scenarios
def _raw_parameters(ctx: _Ctx):
    """A Parameters object over the eight lookup sections the overrides address (None when a file cannot load)."""
    if "_params" in ctx._tables:
        return ctx._tables["_params"]
    from .params import Parameters
    io = _io()
    result = None
    try:
        raw = {
            "UC": io._flat(ctx.path("unit_conversions")), "INF": io._flat(ctx.path("influent")),
            "PRICE": io._flat(ctx.path("prices")), "TEA": io._flat(ctx.path("tea_inputs")),
            "NORM": io._flat(ctx.path("normalization"), key="impact_category"),
            "PROC": io._grouped(ctx.path("process_assumptions")), "MAT": io._grouped(ctx.path("materials")),
            "EQUIP": io._grouped(ctx.path("equipment_general")),
        }
        result = Parameters(raw)
    except Exception as exc:                                  # noqa: BLE001
        ctx.add("scenarios", "parameter files", f"the parameter tables cannot be loaded ({type(exc).__name__}: "
                f"{exc}); override paths were not checked")
    ctx._tables["_params"] = result
    return result


def _check_parameter_overrides(ctx: _Ctx, name: str, overrides, p, where: str) -> None:
    from . import params as params_mod
    from .analysis.uncertainty import POWER_MULTIPLIER_UNITS

    def numeric(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def bad(text):
        ctx.add("scenarios", where, f"scenario {name!r}: {text}")

    if overrides is None:
        return
    if not isinstance(overrides, dict):
        bad("parameter_overrides must be a mapping")
        return
    for section, payload in overrides.items():
        if not isinstance(payload, dict):
            bad(f"parameter_overrides[{section!r}] must be a mapping")
            continue
        leaves = []                                           # (label, override dict, value)
        if section == "POWER_MULT":
            for unit, value in payload.items():
                if unit not in POWER_MULTIPLIER_UNITS:
                    bad(f"POWER_MULT names unit {unit!r}, which the model does not know "
                        f"({list(POWER_MULTIPLIER_UNITS)}); the multiplier would be ignored")
                leaves.append((f"POWER_MULT / {unit}", None, value))
        elif section in params_mod._NESTED:
            for group, entries in payload.items():
                if not isinstance(entries, dict):
                    bad(f"{section} / {group!r} must map parameter names to values")
                    continue
                for parameter, value in entries.items():
                    leaves.append((f"{section} / {group} / {parameter}",
                                   {section: {group: {parameter: value}}}, value))
        elif section in params_mod._FLAT:
            for parameter, value in payload.items():
                leaves.append((f"{section} / {parameter}", {section: {parameter: value}}, value))
        else:
            bad(f"unsupported override section {section!r}; use {list(params_mod._NESTED + params_mod._FLAT)} "
                "or POWER_MULT")
            continue
        for label, override, value in leaves:
            if not numeric(value):
                bad(f"override {label} = {value!r} is not a number (YAML reads 1e-5 without a dot as text)")
            if override is not None and p is not None:
                try:
                    p.replace(override)
                except (KeyError, AttributeError, TypeError) as exc:
                    detail = exc.args[0] if exc.args else exc
                    bad(f"override {label} is not a parameter of the loaded tables ({detail})")


def _check_dataset_overrides(ctx: _Ctx, name: str, overrides, datasets: dict, table_name: str, where: str) -> None:
    if overrides is None:
        return
    if not isinstance(overrides, dict):
        ctx.add("scenarios", where, f"scenario {name!r}: lca_dataset_overrides must be a mapping")
        return
    for flow, dataset in overrides.items():
        key = str(flow).strip()
        if key not in datasets:
            ctx.add("scenarios", where, f"scenario {name!r}: lca_dataset_overrides names flow {flow!r}, which "
                    f"is not in {table_name}; the model ignores the entry silently")
        elif dataset not in datasets[key]:
            if datasets[key] == {"default"}:
                ctx.add("scenarios", where, f"scenario {name!r}: lca_dataset_overrides picks dataset "
                        f"{dataset!r} for flow {flow!r}, but {table_name} carries only 'default' for it, so the "
                        "model ignores the choice silently (a table without the alternative datasets hides it)")
            else:
                ctx.add("scenarios", where, f"scenario {name!r}: lca_dataset_overrides picks dataset "
                        f"{dataset!r} for flow {flow!r}, but {table_name} has only {sorted(datasets[key])}")


def _check_scenarios(ctx: _Ctx) -> None:
    scenarios = _scenarios(ctx)
    if scenarios is None:
        return
    where = ctx.path("scenarios").name
    for wanted in dl.SCENARIOS:
        if wanted not in scenarios:
            ctx.add("scenarios", where, f"scenario {wanted!r} of the run list (datalayer.SCENARIOS) is not defined")
    for name, entry in scenarios.items():
        try:
            dl.scenario_prefix(name, True)
        except (ValueError, TypeError) as exc:
            ctx.add("scenarios", where, f"scenario id {name!r} is not usable in file names ({exc})")
        if not isinstance(entry, dict):
            ctx.add("scenarios", where, f"scenario {name!r} must be a mapping")
            continue
        for key in entry:
            if key not in SCENARIO_KEYS:
                ctx.add("scenarios", where, f"scenario {name!r} has the unknown key {key!r} (known: "
                        f"{list(SCENARIO_KEYS)}); the loader ignores it silently")
        parent = entry.get("inherits")
        if parent is not None and parent not in scenarios:
            ctx.add("scenarios", where, f"scenario {name!r} inherits from {parent!r}, which is not defined")
    scenarios = {k: v for k, v in scenarios.items() if isinstance(v, dict)}
    for name in scenarios:
        _resolved(ctx, name, scenarios)                       # reports circular inheritance

    p = _raw_parameters(ctx)
    chosen, _registered = _chosen_factor_table(ctx)
    datasets = None
    if chosen is not None and chosen.has("flow") and chosen.has("dataset_id"):
        datasets, _units = _factor_pairs(chosen)
    for name, entry in scenarios.items():
        _check_parameter_overrides(ctx, name, entry.get("parameter_overrides"), p, where)
        if datasets is not None:
            _check_dataset_overrides(ctx, name, entry.get("lca_dataset_overrides"), datasets, chosen.name, where)


# ---------------------------------------------------------------------------------------------- uncertainty
def _check_uncertainty(ctx: _Ctx) -> None:
    table = ctx.table("uncertainty")
    if table is None:
        return
    from .analysis import sensitivity, uncertainty
    name = table.name
    rows = table.cols("parameter", "distribution", "base", "min", "max")
    if rows is None:
        return
    by_label = {sa.label: sa for sa in sensitivity.PARAMETERS}
    p = _raw_parameters(ctx)
    findings = []
    for i, (label, dist, base, low, high) in enumerate(rows):
        row = table.row_no(i)
        label = label.strip()
        sa_label = uncertainty._LABEL_TO_SA.get(label)
        if sa_label is None:
            findings.append((row, f"parameter {label!r} is not in analysis.uncertainty's label map "
                             f"({len(uncertainty._LABEL_TO_SA)} entries); the sampler would skip the row silently"))
        elif sa_label not in by_label:
            findings.append((row, f"parameter {label!r} maps to {sa_label!r}, which is not in the sensitivity "
                             "registry; the sampler would skip the row silently"))
        elif p is not None:
            try:
                by_label[sa_label].base_value(p)
            except (KeyError, AttributeError, TypeError) as exc:
                detail = exc.args[0] if exc.args else exc
                findings.append((row, f"parameter {label!r} maps to the model parameter {sa_label!r}, which does "
                                 f"not exist in the loaded parameter tables ({detail})"))
        if dist.strip().lower() not in UNCERTAINTY_DISTRIBUTIONS:
            findings.append((row, f"distribution {dist!r} is not one of {list(UNCERTAINTY_DISTRIBUTIONS)}; the "
                             "sampler treats any word except 'discrete' as continuous"))
        state = _classify([base, low, high])
        if all(s == "ok" for s, _v in state):
            b, lo, hi = (v for _s, v in state)
            if lo > hi:
                findings.append((row, f"min {low} is above max {high}"))
            elif not lo <= b <= hi:
                findings.append((row, f"base {base} is outside [{low}, {high}]"))
    ctx.add_rows("uncertainty", name, findings)

    # the sensitivity registry addresses the same parameter tables
    findings = []
    if p is not None:
        for sa in sensitivity.PARAMETERS:
            try:
                sa.base_value(p)
            except (KeyError, AttributeError, TypeError) as exc:
                detail = exc.args[0] if exc.args else exc
                findings.append((None, f"sensitivity parameter {sa.label!r} ({sa.section}/{sa.group}/{sa.name}) "
                                 f"does not exist in the loaded tables ({detail})"))
    ctx.add_rows("uncertainty", "analysis.sensitivity", findings)


# ---------------------------------------------------------------------------------------------- sampling
def _check_sampling(ctx: _Ctx) -> None:
    table = ctx.table("sampling")
    if table is None or not (table.has("key") and table.has("value")):
        return
    name = table.name
    found: dict = {}
    for i, (key, value) in enumerate(table.cols("key", "value")):
        key = key.strip()
        if key and key not in found:
            found[key] = (table.row_no(i), value)
        if value.strip() == "":
            ctx.add("sampling", name, f"setting {key!r} has no value", table.row_no(i))
    for key, (kind, expected) in SAMPLING_SETTINGS.items():
        if key not in found:
            ctx.add("sampling", name, f"required setting {key!r} is missing")
            continue
        row, value = found[key]
        if value.strip() == "":
            continue                                          # reported above
        if kind == "text":
            if expected is not None and value != expected:
                ctx.add("sampling", name, f"setting {key!r} is {value!r}, the frozen value is {expected!r}", row)
            continue
        ((status, number),) = _classify([value])
        if status != "ok":
            ctx.add("sampling", name, f"setting {key!r} = {value!r} is not a number", row)
        elif kind == "int" and number != int(number):
            ctx.add("sampling", name, f"setting {key!r} = {value!r} must be a whole number", row)
        elif not math.isclose(number, expected, rel_tol=1e-12, abs_tol=0.0):
            ctx.add("sampling", name, f"setting {key!r} is {value!r}, the frozen Phase 1 value is {expected!r}",
                    row)


# ---------------------------------------------------------------------------------------------- orphans, raw
def _check_orphans(ctx: _Ctx) -> None:
    if ctx.scope == "deposit":
        return
    folder = ctx.orphan_dir
    if not folder.is_dir():
        ctx.add("orphans", folder.name, f"superseded container folder is missing (expected {folder})")
        return
    for name in sorted(set(dl.ORPHANS["files"]) ^ set(ORPHAN_SHA256)):
        if name in ORPHAN_SHA256:
            ctx.add("orphans", name, "has a recorded SHA-256 but is not a registered orphan table")
        else:
            ctx.add("orphans", name, "registered orphan table has no recorded SHA-256 (validation.ORPHAN_SHA256)")
    for name in dl.ORPHANS["files"]:
        path = folder / name
        if not path.is_file():
            ctx.add("orphans", name, f"orphan table is missing from {folder.name} (it cannot be regenerated)")
            continue
        if path.stat().st_size == 0:
            ctx.add("orphans", name, "orphan table is empty")
            continue
        if _writable(path):
            ctx.add("orphans", name, "orphan table is not read-only (superseded results must never change)")
        expected = ORPHAN_SHA256.get(name)
        digest = _sha256(path)
        if expected is not None and digest != expected:
            ctx.add("orphans", name, f"SHA-256 is {digest}, the recorded value is {expected}; the superseded table "
                    "differs from the frozen copy (read-only does not protect its content)")


def _check_raw(ctx: _Ctx) -> None:
    if ctx.scope == "deposit":
        return
    for key, name in dl.RAW_IN.items():
        path = ctx.raw_dir / name
        if not path.is_file():
            ctx.add("raw", name, f"raw workbook is missing (expected at {path})")
            continue
        if _writable(path):
            ctx.add("raw", name, "raw workbook is not read-only (raw data must never change)")
        digest = _sha256(path)
        if digest != SHA256[key]:
            ctx.add("raw", name, f"SHA-256 is {digest}, the recorded value is {SHA256[key]}; the workbook differs "
                    "from the copy the model was verified against")


# ---------------------------------------------------------------------------------------------- entry points
_CHECKS = (("registry", _check_registry), ("exists", _check_exists), ("header", _check_header),
           ("keys", _check_keys), ("provenance", _check_provenance), ("numeric", _check_numeric),
           ("factors", _check_factors), ("flows", _check_flows), ("template", _check_template),
           ("scenarios", _check_scenarios), ("uncertainty", _check_uncertainty), ("sampling", _check_sampling),
           ("orphans", _check_orphans), ("raw", _check_raw))


def run_checks(data_dir=None, *, raw_dir=None, orphan_dir=None, scope=None, families=None) -> dict:
    """Run the checks. Returns {'problems': {family: [message, ...]}, 'blank_value_type': {file: (blank, rows)}}.
    data_dir: a copied reference folder (default: the registered one); raw_dir / orphan_dir: alternative folders
    for the raw workbooks / the superseded container (tests); scope: 'project', 'deposit' or None = automatic
    (see the module doc); families: run only these check families (default: all; a check that is not run
    reports nothing)."""
    wanted = FAMILIES if families is None else tuple(families)
    unknown = [f for f in wanted if f not in FAMILIES]
    if unknown:
        raise ValueError(f"unknown check families {unknown}; known: {list(FAMILIES)}")
    ctx = _Ctx(data_dir, raw_dir, orphan_dir, scope)
    for family, function in _CHECKS:
        if family in wanted:
            _guard(ctx, family, function)
    return {"problems": ctx.problems, "blank_value_type": ctx.blank_value_type, "pending": list(ctx.pending)}


def validate_all(strict: bool = True, data_dir=None, *, raw_dir=None, orphan_dir=None,
                 scope=None, families=None) -> list:
    """The list of problems (each names the file, the row and the reason); [] when the inputs are consistent.
    With strict=True (the runner's call) any problem raises SystemExit listing all of them."""
    result = run_checks(data_dir, raw_dir=raw_dir, orphan_dir=orphan_dir, scope=scope, families=families)
    problems = [message for family in FAMILIES for message in result["problems"][family]]
    if strict and problems:
        lines = [f"input validation failed: {len(problems)} problem(s)"]
        lines += [f"  {n}. {message}" for n, message in enumerate(problems, 1)]
        raise SystemExit("\n".join(lines).encode("ascii", "backslashreplace").decode("ascii"))
    return problems


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate the LCA-FCDI inputs (structural checks only).")
    parser.add_argument("--data-dir", default=None, help="a copied reference folder (default: the registered one)")
    parser.add_argument("--scope", choices=_SCOPES, default=None, help="default: automatic")
    args = parser.parse_args(argv)
    result = run_checks(args.data_dir, scope=args.scope)
    total = 0
    for family in FAMILIES:
        found = result["problems"][family]
        total += len(found)
        print(f"{family:<12} {'ok' if not found else str(len(found)) + ' problem(s)'}")
        for message in found:
            print("   ", message.encode("ascii", "backslashreplace").decode("ascii"))
    blanks = result["blank_value_type"]
    print(f"\nblank value_type (allowed, counted): {sum(b for b, _ in blanks.values())} of "
          f"{sum(n for _, n in blanks.values())} rows in {len(blanks)} files")
    if result["pending"]:
        print(f"pending inputs, absent and declared in datalayer.PENDING_INPUTS (delivered by a later step of the "
              f"current revision; not checked until present): {result['pending']}")
    print("validation " + ("passed" if not total else f"FAILED ({total} problem(s))"))
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
