"""Competing selenium and desalination treatment costs, put on the model basis; and the published LCA/TEA values the
results are benchmarked against (Phase 3).

Treatment-cost benchmarks (SI Eq. S62)
---------------------------------------
The source table (Jensen et al. 2014) reports annualized costs in 2010 US dollars per 1000 gallons, annualizing
capital at 7% over 16 years. Since Phase 3 (2026-10-05) the input table holds only these as-reported rows and the code
puts them on the model basis in two explicit steps:

1. unit and dollar year: USD/m3 (2026) = value / 1000 * (gallons per m3) * CPI(2026) / CPI(base year), with the
   gallons per cubic metre from the UnitConversions table (1 m3 = 1000 L, 1 gal = 3.78541 L, as the source workbook's
   formula) and the consumer price index CPI-U (BLS series CUUR0000SA0, D08 CPIU table; 2026 = mean of January-August).
   The earlier input table carried rows escalated by hand with 1.07**16 = 2.952, i.e. the source's interest rate used
   as an inflation index (corrected in Phase 3, finding P3-F01; CPI-U gives 1.521 for 2010 -> 2026).
2. financing basis: only the capital component is re-levelized, by the ratio of the model's capital recovery factor
   (10% over 30 years) to the source's (7% over 16 years); operating cost is already an annual flow. The two factors
   differ by 0.2%, so the correction is negligible, but it is applied explicitly rather than assumed away.

Literature benchmarks (DESIGN_phase3 section 3.6)
-------------------------------------------------
harmonize_literature() copies the published values of the literature table with the adjustments recorded there (the
writer's harmonisation: dollar year with the CPI-U, unit conversions only where the paper gives the flow; nothing is
converted across functional units), fills the harmonized value of the rows that stand for the D08 benchmarks (note
'harmonized value filled by RunAll') from the committed benchmark band, and fills this work's rows (study_key
'this_work', note 'filled by RunAll; scenario=<id>; ...') from the scenario KPIs: the base case (with the stack and
scale-up losses), the laboratory-ideal case and the four measured operating points; levelized cost, energy intensity
and global warming per m3, and cost and energy per kg of selenium recovered (not harmonizable to m3: another
functional unit). A literature table without this-work rows gets them appended (this_work_rows()).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from .. import datalayer as dl
from .. import io
from ..correlations import capital_recovery_factor
from ..params import Parameters

CAPEX_METRIC = "Annualized CAPEX"
OPEX_METRIC = "OPEX"
TOTAL_METRIC = "Total Annualized Costs"
AS_REPORTED_UNIT = "$/1000 gal"
TARGET_DOLLAR_YEAR = 2026          # the model's dollar year (prices and wages of the D02 tables)
_YEAR_RE = re.compile(r"\b(20[0-9]{2})\b")


def price_vintage(data_dir: Path | None = None) -> str:
    """'2024–2026': the span of the years named in the provenance of the price tables (D02 Prices and TEAInputs,
    SI Tables S6-S7: the reagent, electricity, cost-index and wage entries), i.e. the mixed, un-escalated price
    vintage of this work's costs; the published benchmarks are escalated to TARGET_DOLLAR_YEAR instead (fix round
    2026-10-06, text audit S4)."""
    years = set()
    for key in ("prices", "tea_inputs"):
        frame = dl.read_csv(dl.require_file(dl.ref(key, data_dir), "price table"))
        text = " ".join(str(v) for c in ("reference", "source", "source_locator", "note") if c in frame.columns
                        for v in frame[c])
        years |= {int(y) for y in _YEAR_RE.findall(text)}
    if not years:
        raise ValueError("the price tables name no dollar year in their provenance columns")
    years = sorted(years)
    return f"{years[0]}\u2013{years[-1]}" if years[0] != years[-1] else str(years[0])


def load(p: Parameters, data_dir: Path | None = None) -> pd.DataFrame:
    """Return the benchmark costs per cubic metre in 2026 USD, re-levelized onto the model CRF (SI Eq. S62).

    Columns added to the input rows: gal_per_m3, cpi_base, cpi_target, cpi_factor, dollar_year, min_usd_m3,
    max_usd_m3, mean_usd_m3 (escalated, before the CRF step), source_crf, model_crf, crf_adjustment, and min_adj,
    max_adj, mean_adj (escalated and re-levelized; totals rebuilt from the adjusted parts)."""
    frame = dl.read_csv(dl.require_file(dl.ref("benchmarks", data_dir), "benchmark table"))
    frame = frame[frame["unit"] == AS_REPORTED_UNIT].copy()
    cpi = io.load_cpi(data_dir)

    gal_per_m3 = p.UC["1 m3"] / p.UC["1 gal"]
    frame["gal_per_m3"] = gal_per_m3
    frame["cpi_base"] = [cpi[int(year)] for year in frame["base_year"]]
    frame["cpi_target"] = cpi[TARGET_DOLLAR_YEAR]
    frame["cpi_factor"] = frame["cpi_target"] / frame["cpi_base"]
    frame["dollar_year"] = TARGET_DOLLAR_YEAR
    for column in ("min", "max", "mean"):
        frame[column + "_usd_m3"] = frame[column] / 1000 * gal_per_m3 * frame["cpi_target"] / frame["cpi_base"]

    model_crf = capital_recovery_factor(p.TEA["Interest Rate"], p.TEA["Plant lifetime"])
    frame["source_crf"] = [
        capital_recovery_factor(r.interest_rate, r.lifetime_yr)
        if pd.notna(r.interest_rate) and pd.notna(r.lifetime_yr) else float("nan")
        for r in frame.itertuples()
    ]
    frame["model_crf"] = model_crf
    frame["crf_adjustment"] = model_crf / frame["source_crf"]

    is_capex = frame["metric"] == CAPEX_METRIC
    for column in ("min", "max", "mean"):
        escalated = frame[column + "_usd_m3"]
        frame[column + "_adj"] = escalated.where(~is_capex, escalated * frame["crf_adjustment"])

    # Rebuild totals from the adjusted parts so they stay internally consistent. (A blank min / max of a part is
    # summed as 0.0, the quirk kept since Phase 1: Electrodialysis and Biological Denitrification report no range.)
    parts = (frame[frame["metric"].isin([CAPEX_METRIC, OPEX_METRIC])]
             .groupby("technology")[["min_adj", "max_adj", "mean_adj"]].sum())
    for technology, row in parts.iterrows():
        mask = (frame["technology"] == technology) & (frame["metric"] == TOTAL_METRIC)
        for column in ("min_adj", "max_adj", "mean_adj"):
            frame.loc[mask, column] = row[column]

    return frame.reset_index(drop=True)


def total_cost_band(p: Parameters, data_dir: Path | None = None) -> pd.DataFrame:
    """One row per technology: the adjusted total annualized cost per cubic metre (2026 USD), with the escalation
    record (dollar year of the source, CPI-U factor, target dollar year) and the CRF adjustment."""
    frame = load(p, data_dir)
    totals = frame[frame["metric"] == TOTAL_METRIC].copy()
    return totals[["technology", "min_adj", "max_adj", "mean_adj",
                   "crf_adjustment", "base_year", "cpi_factor", "dollar_year"]].rename(
        columns={"min_adj": "min_usd_m3", "max_adj": "max_usd_m3",
                 "mean_adj": "mean_usd_m3"})


def target_range(p: Parameters, data_dir: Path | None = None) -> tuple:
    """The cost band a new technology has to reach to be competitive.

    Lower edge: the cheapest benchmark mean (biological denitrification).
    Upper edge: the most expensive benchmark mean (reverse osmosis).
    """
    band = total_cost_band(p, data_dir)
    means = band["mean_usd_m3"].dropna()
    return float(means.min()), float(means.max())


# ---------------------------------------------------------------------------------------------- literature (Phase 3)
LITERATURE_COLUMNS = ("study_key", "technology", "scale", "assessment", "functional_unit_as_reported",
                      "boundary_as_reported", "metric", "value_as_reported", "unit_as_reported", "dollar_year",
                      "harmonizable", "harmonized_value", "harmonized_unit", "adjustments_made",
                      "not_harmonized_reason", "source", "source_locator", "value_type", "note")
THIS_WORK_KEY = "this_work"
# Rows of this work: (row label, scenario id). 3V is the base case, so the base row carries both names.
THIS_WORK_SCENARIOS = (("base case with stack and scale-up losses (= 3V)", "base"),
                       ("laboratory-ideal (no stack and scale-up losses)", "lab_ideal"),
                       ("2V", "cond_2v"), ("2V+N", "cond_2v_n"), ("3V+N", "cond_3v_n"))
# KPI -> (metric, unit, harmonizable, functional unit, unit stem). The unit stem identifies the KPI of a this-work row
# of the input table: its unit_as_reported, with 'USD2026' read as 'USD', starts with the stem.
THIS_WORK_METRICS = (
    ("lcot_usd_m3", "LCOW", "USD2026/m3", True, "1 m3 of FGD wastewater treated", "USD/m3"),
    ("total_energy_kWh_m3", "specific_energy", "kWh/m3", True, "1 m3 of FGD wastewater treated", "kWh/m3"),
    ("lca_per_m3::Global warming", "GWP", "kg CO2-eq/m3", True, "1 m3 of FGD wastewater treated", "kg CO2-eq/m3"),
    ("se_recovery_cost_usd_kg", "other", "USD2026/kg Se", False, "1 kg of selenium recovered", "USD/kg Se"),
    ("specific_energy_kWh_kg_se", "other", "kWh/kg Se", False, "1 kg of selenium recovered", "kWh/kg Se"),
)
# The note of a literature row whose harmonized value is the model's own conversion of the D08 benchmark table
# (the Jensen et al. 2014 rows): the value is the committed band's mean for the technology the row names.
BAND_FILL_NOTE = "harmonized value filled by runall"
_SCENARIO_IN_NOTE = re.compile(r"scenario\s*=\s*([A-Za-z0-9_]+)")


def is_this_work_row(study_key: str, note: str = "") -> bool:
    """A row of the literature table that stands for this work (filled by the pipeline, never by hand)."""
    key = str(study_key).strip().lower().replace(" ", "_")
    return key.startswith("this_work") or str(note).strip().lower().startswith("filled by runall")


def _this_work_kpi(metric: str, unit: str) -> str:
    """The KPI column of a this-work row of the input table, from its metric and unit (KeyError when none fits)."""
    stem = str(unit).strip().replace("USD2026", "USD")
    hits = [kpi for kpi, m, _u, _h, _f, prefix in THIS_WORK_METRICS if m == metric and stem.startswith(prefix)]
    if len(hits) != 1:
        raise KeyError(f"no KPI of this work matches metric {metric!r} with unit {unit!r} "
                       f"(known: {[(m, prefix) for _k, m, _u, _h, _f, prefix in THIS_WORK_METRICS]})")
    return hits[0]


def _kpi_row(kpis: pd.DataFrame, scenario: str) -> pd.Series:
    hit = kpis[(kpis["scenario"] == scenario) & (kpis["include_recovery"].astype(bool))]
    if hit.empty:
        raise KeyError(f"scenario {scenario!r} (with recovery) is not in the KPI table")
    return hit.iloc[0]


def this_work_rows(kpis: pd.DataFrame) -> pd.DataFrame:
    """This work's rows of the harmonized table, from the scenario KPI table (with selenium recovery); used when the
    literature table carries no this-work rows of its own."""
    rows = []
    kpi_file = dl.out_name("scenario_kpis")
    for label, scenario in THIS_WORK_SCENARIOS:
        record = _kpi_row(kpis, scenario)
        for kpi, metric, unit, harmonizable, functional_unit, _stem in THIS_WORK_METRICS:
            value = float(record[kpi])
            rows.append({
                "study_key": THIS_WORK_KEY, "technology": "FCDI-BES (this work)", "scale": "modeled",
                "assessment": "both", "functional_unit_as_reported": functional_unit,
                "boundary_as_reported": ("cradle to gate; pilot capacity 10 m3/h; US-SERC electricity; with selenium "
                                         "recovery"),
                "metric": metric, "value_as_reported": value, "unit_as_reported": unit,
                "dollar_year": TARGET_DOLLAR_YEAR if unit.startswith("USD") else "",
                "harmonizable": "yes" if harmonizable else "no",
                "harmonized_value": value if harmonizable else "",
                "harmonized_unit": unit if harmonizable else "",
                "adjustments_made": "none (model output in 2026 USD and the model's functional unit)"
                if harmonizable else "",
                "not_harmonized_reason": "" if harmonizable else (
                    "functional unit is 1 kg of selenium recovered; reported for comparison with per-product studies, "
                    "not converted to m3"),
                "source": "this work (committed scenario KPIs)",
                "source_locator": f"{kpi_file}: scenario {scenario}, include_recovery True, column {kpi}",
                "value_type": "derived", "note": label,
                "row_source": "this work", "scenario": scenario,
            })
    return pd.DataFrame(rows)


def _band_mean(band: pd.DataFrame, technology: str) -> float:
    """The committed band's mean (2026 USD per m3) of the benchmark technology a literature row names (its
    technology text starts with the band's name, case ignored); exactly one must match."""
    text = str(technology).strip().lower()
    hits = [row for row in band.itertuples() if text.startswith(str(row.technology).lower())]
    if len(hits) != 1:
        raise KeyError(f"literature technology {technology!r} matches {len(hits)} benchmark technologies "
                       f"({list(band['technology'])}); its harmonized value cannot be filled")
    return float(hits[0].mean_usd_m3)


def harmonize_literature(kpis: pd.DataFrame, band: pd.DataFrame, data_dir: Path | None = None) -> pd.DataFrame:
    """The harmonized benchmark table (DESIGN_phase3 sections 3.6 and 4.5).

    * literature rows are copied as given, with the adjustments recorded there (the writer's harmonisation);
    * a literature row whose note starts 'harmonized value filled by RunAll' (the Jensen et al. 2014 rows) gets the
      committed benchmark band's mean (2026 USD per m3, CPI-U escalated and re-levelized, SI Eq. S62) of the technology
      it names;
    * the rows that stand for this work (study_key 'this_work...' or a note starting 'filled by RunAll') are filled in
      place from `kpis` (the scenario KPI table of this run, with selenium recovery): the scenario comes from
      'scenario=<id>' in the note, the KPI from the row's metric and unit; value_as_reported is the KPI value, and so
      is harmonized_value where the row is harmonizable. A table without this-work rows gets this_work_rows() appended.

    The result must obey the harmonisation rules: no harmonized value on a row that is not harmonizable, and a finite
    harmonized value on every row that is (ValueError naming the rows otherwise)."""
    frame = io.load_literature(data_dir)
    frame["value_as_reported"] = frame["value_as_reported"].astype(object)
    frame["harmonized_value"] = frame["harmonized_value"].astype(object)
    frame["row_source"] = "literature"
    frame["scenario"] = ""
    kpi_file = dl.out_name("scenario_kpis")
    ours = [is_this_work_row(k, n) for k, n in zip(frame["study_key"], frame["note"])]
    for i in frame.index[ours]:
        row = frame.loc[i]
        found = _SCENARIO_IN_NOTE.search(str(row["note"]))
        if not found:
            raise ValueError(f"literature table row {i + 2}: a this-work row without 'scenario=<id>' in its note")
        scenario = found.group(1)
        kpi = _this_work_kpi(row["metric"], row["unit_as_reported"])
        value = float(_kpi_row(kpis, scenario)[kpi])
        frame.at[i, "value_as_reported"] = value
        if row["harmonizable"] == "yes":
            frame.at[i, "harmonized_value"] = value
        frame.at[i, "row_source"] = "this work"
        frame.at[i, "scenario"] = scenario
        if not str(row["source_locator"]).strip():
            frame.at[i, "source_locator"] = f"{kpi_file}: scenario {scenario}, include_recovery True, column {kpi}"
    for i in frame.index[[not o and str(n).strip().lower().startswith(BAND_FILL_NOTE)
                          for o, n in zip(ours, frame["note"])]]:
        if frame.at[i, "harmonizable"] != "yes":
            raise ValueError(f"literature table row {i + 2}: a band-filled row must be harmonizable")
        frame.at[i, "harmonized_value"] = _band_mean(band, frame.at[i, "technology"])

    columns = list(LITERATURE_COLUMNS) + ["row_source", "scenario"]
    result = frame[columns]
    if not any(ours):
        result = pd.concat([result, this_work_rows(kpis)[columns]], ignore_index=True)
    _check_harmonized(result)
    return result.reset_index(drop=True)


def _check_harmonized(frame: pd.DataFrame) -> None:
    def present(value) -> bool:
        return not (value is None or value == "" or (isinstance(value, float) and math.isnan(value)))

    problems = []
    for i, row in frame.iterrows():
        given = present(row["harmonized_value"])
        if row["harmonizable"] == "no" and given:
            problems.append(f"row {i}: a harmonized value on a row that is not harmonizable")
        if row["harmonizable"] == "yes" and not (given and math.isfinite(float(row["harmonized_value"]))):
            problems.append(f"row {i} ({row['study_key']}, {row['metric']}): harmonizable but no finite harmonized "
                            "value")
    if problems:
        raise ValueError("harmonized literature table: " + "; ".join(problems))


def write_harmonized(frame: pd.DataFrame, out_dir: Path | None = None) -> Path:
    path = dl.out("literature_harmonized") if out_dir is None \
        else Path(out_dir) / dl.out_name("literature_harmonized")
    return dl.write_csv(frame, path)
