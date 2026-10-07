"""Load the reference tables into the lookup structures the model uses.

Every input read in the project happens here, and every file is located
through the data layer's registry (`datalayer.ref()`), never by a typed path.
`data_dir` means "a folder that holds the registry file names": None is the
project's reference folder, a test passes a copy of it. The returned
structures keep the shapes the original spreadsheet model used - nested dicts
keyed by the human-readable labels from the workbooks - so the engineering
code reads almost like the source equations:

    p.PROC["FCDI-BES"]["Faradaic Efficiency"]
    p.MAT["Membranes"]["Cost"]
    p.UC["Faraday constant"]

Reading order matters in one place only: a scenario selects which background
LCI dataset to use for the flows where the two source workbooks disagreed, so
that choice is applied while the characterisation table is being built.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import yaml

from . import datalayer as dl
from .config import IMPACT_CATEGORIES

# The four columns every v01 input file carries after its original columns
# (see the Dataset_ID_Legend). The model never reads them: every loader below
# selects the columns it needs by name. Catalogue frames are handed to the
# equipment selection whole, so these four are dropped there and the frames
# stay identical to what the pre-restructure loader returned.
PROVENANCE_COLUMNS = ("source", "source_locator", "value_type", "note")

# Lookup keys come straight from the workbooks and carry typographic
# superscripts ("1 m3" is stored as "1 m³"). Normalising them here keeps
# the engineering code readable and typo-resistant; nothing else about the
# label is touched, so chemical names such as MnSO4-H2O survive intact.
_SUPERSCRIPTS = str.maketrans({"²": "2", "³": "3"})


def _key(label: object) -> str:
    return str(label).strip().translate(_SUPERSCRIPTS)


def _numeric(series: pd.Series) -> list:
    """Coerce a value column to numbers, leaving genuine text alone.

    Some parameter tables mix a text value into an otherwise numeric column
    (Materials carries "PP and PVDF" as a material of construction), which
    makes pandas type the whole column as strings. Without this, every
    downstream arithmetic operation on that table fails.
    """
    converted = pd.to_numeric(series, errors="coerce")
    return [raw if pd.isna(num) else float(num)
            for raw, num in zip(series, converted)]


def _flat(path: Path, key: str = "parameter", value: str = "value") -> dict:
    """A two-column lookup: {parameter: value}."""
    frame = dl.read_csv(path)
    return dict(zip([_key(k) for k in frame[key]], _numeric(frame[value])))


def _grouped(path: Path) -> dict:
    """A three-level lookup: {group: {parameter: value}}."""
    frame = dl.read_csv(path)
    out: dict = {}
    for group, sub in frame.groupby("group", sort=False):
        out[_key(group)] = dict(
            zip([_key(k) for k in sub["parameter"]], _numeric(sub["value"]))
        )
    return out


def _inventory(path: Path) -> dict:
    """Process inventory: {group: {Input|Output: {flow: amount}}}."""
    frame = dl.read_csv(path)
    out: dict = {}
    for group, sub in frame.groupby("group", sort=False):
        entry: dict = {}
        for direction, dsub in sub.groupby("direction", sort=False):
            entry[_key(direction)] = dict(
                zip([_key(f) for f in dsub["flow"]], _numeric(dsub["amount"]))
            )
        out[_key(group)] = entry
    return out


def _foreground(path: Path) -> dict:
    """Return {process: {flow: (amount, unit)}}.

    The row where the flow name equals the process name declares the process's
    own output and is not an input, so it is skipped.
    """
    frame = dl.read_csv(path)
    out: dict = {}
    for _, row in frame.iterrows():
        process, flow = str(row["process"]).strip(), str(row["flow"]).strip()
        if flow.lower() == process.lower():
            continue
        out.setdefault(process, {})[flow] = (float(row["amount"]), str(row["unit"]).strip())
    return out


def _lca_factors(path: Path, dataset_overrides: dict | None) -> tuple[dict, dict]:
    """Return ({flow: {impact_category: cf}}, {flow: [missing categories]}).

    Flows whose CFs agree between the two source workbooks carry dataset_id
    "default". Divergent flows appear once per dataset and the scenario must
    pick one; picking nothing raises rather than defaulting silently, because
    the graphite choice alone moves ecotoxicity by four orders of magnitude.

    A dataset recovered without every category (see the note in the
    superseded D01 factor-writer script) reports its gaps rather than
    silently contributing zero.
    """
    frame = dl.read_csv(path)
    overrides = {k.strip(): v for k, v in (dataset_overrides or {}).items()}

    out: dict = {}
    gaps: dict = {}
    for flow, sub in frame.groupby("flow", sort=False):
        flow = str(flow).strip()
        available = set(sub["dataset_id"])
        if available == {"default"}:
            chosen = "default"
        else:
            chosen = overrides.get(flow)
            if chosen is None:
                raise ValueError(
                    "flow " + repr(flow) + " has multiple LCI datasets "
                    + repr(sorted(available))
                    + " but the scenario does not choose one; "
                    + "add it to lca_dataset_overrides")
            if chosen not in available:
                raise ValueError(
                    "scenario selects dataset " + repr(chosen) + " for flow "
                    + repr(flow) + ", but only " + repr(sorted(available)) + " exist")
        picked = sub[sub["dataset_id"] == chosen]
        out[flow] = dict(zip(picked["impact_category"], picked["value"]))
        missing = [c for c in IMPACT_CATEGORIES if c not in out[flow]]
        if missing:
            gaps[flow] = missing
    return out, gaps


def _uncertainty(path: Path) -> dict:
    """Monte Carlo distributions: {parameter: {dist, base, min, max}}."""
    frame = dl.read_csv(path)
    return {
        str(r["parameter"]).strip(): {
            "dist": str(r["distribution"]).strip().lower(),
            "base": float(r["base"]),
            "min": float(r["min"]),
            "max": float(r["max"]),
        }
        for _, r in frame.iterrows()
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def load_scenarios(data_dir: Path | None = None) -> dict:
    path = dl.require_file(dl.ref("scenarios", data_dir), "scenario definitions")
    doc = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    return doc["scenarios"]


def resolve_scenario(name: str, scenarios: dict) -> dict:
    """Flatten a scenario's inheritance chain into a single dict."""
    if name not in scenarios:
        raise KeyError("unknown scenario " + repr(name) + "; have " + repr(sorted(scenarios)))

    chain, seen = [], set()
    current = name
    while current is not None:
        if current in seen:
            raise ValueError("circular inheritance involving " + repr(current))
        seen.add(current)
        chain.append(scenarios[current])
        current = scenarios[current].get("inherits")

    resolved: dict = {"parameter_overrides": {}, "lca_dataset_overrides": {}}
    for entry in reversed(chain):          # base-most ancestor first
        resolved["lca_dataset_overrides"].update(entry.get("lca_dataset_overrides", {}))
        _merge(resolved["parameter_overrides"], entry.get("parameter_overrides", {}))
        for key in ("label", "note", "source"):
            if key in entry:
                resolved[key] = entry[key]
    resolved["name"] = name
    return resolved


def _merge(target: dict, incoming: dict) -> None:
    """Recursively merge override dicts, so a child scenario can refine one
    parameter without discarding its parent's siblings."""
    for key, value in incoming.items():
        if isinstance(value, dict):
            _merge(target.setdefault(key, {}), value)
        else:
            target[key] = value


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _catalogue(path: Path) -> pd.DataFrame:
    """One equipment catalogue with its original columns only."""
    frame = dl.read_csv(path)
    return frame.drop(columns=[c for c in PROVENANCE_COLUMNS if c in frame.columns])


def _input(key: str, data_dir: Path | None) -> Path:
    """The registered input file `key`, which must exist."""
    return dl.require_file(dl.ref(key, data_dir), "input file '" + key + "'")


def load_raw(data_dir: Path | None = None, scenario: str = "base") -> dict:
    """Read every table and return the raw lookup structures for Parameters.

    The LCA factor table is the licensed one unless LCA_FCDI_FACTORS selects
    another (see datalayer.factors_path); the choice is read here, at call time.
    """
    spec = resolve_scenario(scenario, load_scenarios(data_dir))

    catalogs = {
        "Pump": _catalogue(_input("pumps", data_dir)),
        "Centrifuge": _catalogue(_input("centrifuges", data_dir)),
        "Rotary Fine Mesh Filter Screen": _catalogue(_input("screens", data_dir)),
        "Electrocultivation Mixer": _catalogue(_input("ec_mixers", data_dir)),
    }

    factors, gaps = _lca_factors(
        dl.require_file(dl.factors_path(data_dir), "LCA factor table"),
        spec["lca_dataset_overrides"])

    return {
        "scenario": spec,
        "UC": _flat(_input("unit_conversions", data_dir)),
        "INF": _flat(_input("influent", data_dir)),
        "PRICE": _flat(_input("prices", data_dir)),
        "TEA": _flat(_input("tea_inputs", data_dir)),
        "NORM": _flat(_input("normalization", data_dir), key="impact_category"),
        "PROC": _grouped(_input("process_assumptions", data_dir)),
        "MAT": _grouped(_input("materials", data_dir)),
        "EQUIP": _grouped(_input("equipment_general", data_dir)),
        "EC_INV": _inventory(_input("electrocultivation", data_dir)),
        "FG_RAW": _foreground(_input("foreground_processes", data_dir)),
        "LCA_FACTORS": factors,
        "LCA_GAPS": gaps,
        "CATALOG": catalogs,
        "MC_DIST": _uncertainty(_input("uncertainty", data_dir)),
    }


# ---------------------------------------------------------------------------
# Phase 3 (2026-10-05): CPI-U, the loss ledger, the literature table, D05
# ---------------------------------------------------------------------------

def load_cpi(data_dir: Path | None = None) -> dict:
    """The CPI-U table as {year (int): annual average}. A repeated year raises (validation reports it first)."""
    frame = dl.read_csv(_input("cpi", data_dir))
    out: dict = {}
    for year, value in zip(frame["year"], frame["cpi_u_annual_average"]):
        year = int(year)
        if year in out:
            raise ValueError(dl.rel_root(dl.ref("cpi", data_dir)) + ": year " + str(year) + " appears more than once")
        out[year] = float(value)
    return out


def load_cpi_table(data_dir: Path | None = None) -> pd.DataFrame:
    """The CPI-U table as read (every column, provenance included), for the SI and the reports."""
    return dl.read_csv(_input("cpi", data_dir))


def load_ledger(data_dir: Path | None = None) -> pd.DataFrame:
    """The stack and scale-up loss ledger D09 (documentation: the model reads no number from it). Blank cells are ''."""
    return dl.read_csv(_input("scaleup_ledger", data_dir), keep_default_na=False)


def load_literature(data_dir: Path | None = None) -> pd.DataFrame:
    """The literature benchmark table (D08). Text columns keep blank cells as ''; harmonized_value is numeric or
    blank (NaN). A missing file raises FileNotFoundError naming it (it is a pending input until delivered)."""
    frame = dl.read_csv(_input("literature_benchmarks", data_dir), keep_default_na=False, dtype=str)
    frame["harmonized_value"] = pd.to_numeric(frame["harmonized_value"].replace("", float("nan")))
    return frame


def load_electron_partitioning(data_dir: Path | None = None) -> pd.DataFrame:
    """The measured electron partitioning D05, as the default float parser reads it (one row per condition and
    window)."""
    return dl.read_csv(_input("electron_partitioning", data_dir))


# ---------------------------------------------------------------------------
# Sampling settings
# ---------------------------------------------------------------------------

def _pipe_list(text: str) -> tuple:
    return tuple(part.strip() for part in text.split("|") if part.strip())


# key -> parser of the text in the `value` column. The numbers are parsed with
# int() / float() on the cell text, which gives exactly the value the same
# literal has in Python source; the pandas default float parser can differ by
# one ULP on long tokens (see datalayer.read_csv), so pandas is not used here.
SAMPLING_KEYS = {
    "method": str,                    # description only; the code path is fixed
    "seed": int,                      # Monte Carlo and scaling-band seed
    "n_draws": int,                   # Monte Carlo draws per distribution x configuration
    "n_draws_quick": int,             # same, for --quick
    "distributions": _pipe_list,      # Monte Carlo distribution checks, in run order
    "oat_step": float,                # one-at-a-time relative step
    "scaling_n_flow": int,            # flowrate sweep: points per configuration
    "scaling_n_flow_quick": int,
    "scaling_n_draws": int,           # flowrate sweep: Monte Carlo draws per point
    "scaling_n_draws_quick": int,
    "grid_n_flow": int,               # main two-dimensional grid
    "grid_n_axis": int,
    "grid_n_flow_quick": int,
    "grid_n_axis_quick": int,
    "alt_grid_n_flow": int,           # the three alternate-axis grids (skipped by --quick)
    "alt_grid_n_axis": int,
}


def load_sampling(data_dir: Path | None = None) -> dict:
    """The registered SamplingSettings table as {key: typed value}.

    Every key of SAMPLING_KEYS must be present exactly once; other keys are
    ignored. A missing file, a repeated or missing key and a value that does
    not parse all raise, naming the file and the key.
    """
    path = _input("sampling", data_dir)
    values: dict = {}
    for row in dl.csv_rows(path):
        key = (row.get("key") or "").strip()
        if key not in SAMPLING_KEYS:
            continue
        if key in values:
            raise ValueError(dl.rel_root(path) + ": key " + repr(key) + " appears more than once")
        text = (row.get("value") or "").strip()
        try:
            values[key] = SAMPLING_KEYS[key](text)
        except ValueError as exc:
            raise ValueError(dl.rel_root(path) + ": key " + repr(key)
                             + " has the value " + repr(text) + ", which does not parse ("
                             + str(exc) + ")") from None
    missing = [k for k in SAMPLING_KEYS if k not in values]
    if missing:
        raise ValueError(dl.rel_root(path) + ": missing keys " + repr(missing))
    return values


def sampling_value(key: str, data_dir: Path | None = None):
    """One value of the SamplingSettings table (see SAMPLING_KEYS)."""
    if key not in SAMPLING_KEYS:
        raise KeyError("unknown sampling key " + repr(key) + "; known: " + repr(sorted(SAMPLING_KEYS)))
    return load_sampling(data_dir)[key]


def deep_copy(raw: dict) -> dict:
    """Copy the mutable lookups. Catalog DataFrames are read-only, so shared."""
    return {
        k: (v if k in ("CATALOG", "scenario") else copy.deepcopy(v))
        for k, v in raw.items()
    }
