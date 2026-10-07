# FCDI-BES life-cycle and techno-economic assessment: input data and calculation code

Input data and Python code behind the article *"Areal selenium productivity sets improvement targets for
integrated FCDI-BES: a prospective LCA-TEA"* (Riveros, Mostofifar and Jiang, The University of Alabama; the full
reference is added on publication).

**Repository:** https://github.com/maverickjiang0302/2026-fcdi-lca-tea. **Archive:** Zenodo,
https://doi.org/10.5281/zenodo.23201433 (concept DOI: all versions, resolving to the latest); every GitHub release is
archived there with its own version DOI.

The model couples flow-electrode capacitive deionization (FCDI) with a bio-electrochemical system (BES) that reduces
selenium oxyanions from flue-gas desulfurization wastewater to elemental selenium, with and without a downstream
selenium-recovery train, from pilot scale to a full-scale plant. Functional unit: 1 m3 of wastewater treated;
boundary: cradle to gate; impact method: TRACI 2.1 with ecoinvent 3.7 (cut-off) background data; capital costing
after Guthrie and Seider et al. The equations are stated in the article's Supplementary Material (SI).

## What is in this repository

**Input data** (CSV, and one YAML file; folder `01_Data/03_Reference/`). The SI table that prints each input is named. The SI
shows the input's original columns, or a selection of them, with numbers rounded for display; the files hold the full
values and, for the tables, four provenance columns.

| Class | File | SI table |
|---|---|---|
| Model parameters (D02) | `LCA-FCDI_D02_Reference_ProcessAssumptions_v03.csv` | S3 |
| Model parameters (D02) | `LCA-FCDI_D02_Reference_Materials_v01.csv` | S4 |
| Model parameters (D02) | `LCA-FCDI_D02_Reference_EquipmentGeneral_v02.csv` | S5 |
| Model parameters (D02) | `LCA-FCDI_D02_Reference_TEAInputs_v01.csv` | S6 |
| Model parameters (D02) | `LCA-FCDI_D02_Reference_Prices_v01.csv` | S7 |
| Model parameters (D02) | `LCA-FCDI_D02_Reference_UnitConversions_v01.csv` | S8 |
| Model parameters (D02) | `LCA-FCDI_D02_Reference_Influent_v01.csv` | S9 |
| Equipment catalogues (D03) | `LCA-FCDI_D03_Reference_PumpCatalogue_v01.csv` | S10 |
| Equipment catalogues (D03) | `LCA-FCDI_D03_Reference_CentrifugeCatalogue_v01.csv` | S11 |
| Equipment catalogues (D03) | `LCA-FCDI_D03_Reference_ScreenCatalogue_v01.csv` | S12 |
| Equipment catalogues (D03) | `LCA-FCDI_D03_Reference_ECMixerCatalogue_v01.csv` | S13 |
| Measured electron partitioning (D05) | `LCA-FCDI_D05_Reference_ElectronPartitioning_v01.csv` | S14 |
| Life-cycle inventories (D04) | `LCA-FCDI_D04_Reference_ForegroundProcesses_v01.csv` | S15 |
| Life-cycle inventories (D04) | `LCA-FCDI_D04_Reference_ElectrocultivationInventory_v01.csv` | S16 |
| Normalisation (D04) | `LCA-FCDI_D04_Reference_TRACINormalizationUS2008_v01.csv` | S17 |
| Scenario definitions (D06) | `LCA-FCDI_D06_Reference_ScenarioDefinitions_v03.yaml` | S18 (and S26) |
| Monte Carlo distributions (D07) | `LCA-FCDI_D07_Reference_UncertaintyDistributions_v03.csv` | S19 |
| Sampling settings (D07) | `LCA-FCDI_D07_Reference_SamplingSettings_v01.csv` | - |
| Benchmark treatment costs, as reported (D08) | `LCA-FCDI_D08_Reference_SeTreatmentBenchmarks_v02.csv` | S23 (input of) |
| Consumer price index CPI-U, annual averages (D08) | `LCA-FCDI_D08_Reference_CPIU_v01.csv` | S23 (input of) |
| Published LCA / TEA values compared with this work (D08) | `LCA-FCDI_D08_Reference_LiteratureBenchmarks_v02.csv` | S28 |
| Stack and scale-up loss ledger (D09) | `LCA-FCDI_D09_Reference_ScaleUpLossLedger_v02.csv` | S27 (input of) |
| Factor template (D01, public) | `LCA-FCDI_D01_Reference_TRACIFactorsTemplate_v01.csv` | - |
| Dummy factor table (D01, public) | `LCA-FCDI_D01_Reference_TRACIFactorsDummy_v01.csv` | - |
| Flow dictionary (D01, public) | `LCA-FCDI_D01_Reference_FlowDictionary_v01.csv` | - |

- The factor **template** lists every background dataset the model uses: one row per flow, dataset variant and
  TRACI 2.1 impact category, naming the ecoinvent activity, reference product, location, reference amount and unit.
  Its `value` column is blank. The **dummy** table has the same rows with every value 1.0. The **flow dictionary**
  lists each flow once, with the provider process. None of these files holds an ecoinvent value.
- SI Table S23 is computed from the benchmark input (the costs as reported, 2010 US dollars per 1000 gallons) and
  the CPI-U table by the code (it is a result); both inputs are shared. SI Tables S29 and S30 (the harmonized
  comparison and the comparable-metric table) and the computed column of Table S27 (the loss ledger) are results as
  well; their inputs are shared.

**The Python code that performs every calculation** (folder `02_Analysis_Code/`):

- `fcdibes/`: the model (unit operations, flowsheet, inventory, TEA, LCA), the data layer (`datalayer.py`, the
  registry of every folder, file name and version) and the input validation (`validation.py`);
- `fcdibes/analysis/`: one-at-a-time sensitivity, Monte Carlo, flowrate scaling, the two-dimensional scale grids,
  benchmarks (with the harmonized literature comparison), break-even targets, the four measured operating points
  (`conditions.py`) and the stack and scale-up loss ledger (`ledger.py`);
- `LCA-FCDI_D01-D08_RunAll.py`: the runner, which validates the inputs and runs every analysis in order.

**Repository files:** `README.md`, `LICENSE` (code), `LICENSE-DATA` (data), `CITATION.cff`, `requirements.txt`,
`.gitignore`, `.gitattributes`, and `DEPOSIT_MANIFEST.csv` (path, size and SHA-256 of every other file; its presence
tells the code that it runs in the public repository).

## What is not included, and why

- **The characterisation factors of the ecoinvent 3.7 datasets** (TRACI 2.1 scores per reference unit, with which
  the article's LCA results were computed). They are licensed data: the ecoinvent licence does not allow ecoinvent
  LCIA scores to be redistributed. The code reads them from `01_Data/03_Reference/LCA-FCDI_D01_Reference_TRACIFactorsEcoinvent37_v01.csv`, which users with a licence build themselves
  (see below); `.gitignore` keeps that file out of the repository.
- **Result files of any kind** (per-scenario tables, KPIs, sensitivity, Monte Carlo, scaling, grids, break-even).
  Flow-level contributions divide back into the factors, and scenario, Monte Carlo, grid and sensitivity results
  supply enough equations to solve for them, so no result file is published. The results are reported in the
  article and its SI; the code regenerates every file (243 tables) into `01_Data/02_Processed/`, which is git-ignored.
- **The source workbooks** of the authors: they contain ecoinvent-derived sheets. The inputs above were transcribed
  from them (the licensed factors excepted).
- **The figures, the scripts that draw them, the scripts that write the manuscript and the SI, the tests and the
  authors' internal tools**, and superseded versions of input files. The runner names the steps it skips.

## Reproducing the results

Requirements: Python (tested with 3.14.7) and the packages pinned in `requirements.txt`
(`pip install -r requirements.txt`). Run from `02_Analysis_Code/`. On Windows, a clone into a deep folder can fail
with `Filename too long` (the file names are long): run `git config --global core.longpaths true` before cloning,
or clone into a short path.

**1. Without an ecoinvent licence (dummy run).** Every factor is 1.0, so every LCA result is meaningless; every TEA
result is exact.

```bash
cd 02_Analysis_Code
LCA_FCDI_FACTORS=dummy python LCA-FCDI_D01-D08_RunAll.py
```

(PowerShell: `$env:LCA_FCDI_FACTORS = "dummy"`, then `python LCA-FCDI_D01-D08_RunAll.py`.) The results go to
`01_Data/02_Processed/dummy_run/`. The following results do not depend on the factors and are reproduced byte for byte
(176 of the 243 tables, checked when this repository was built): for every scenario and configuration
the tables `capex`, `lca_inventory`, `opex`, `power`, `reactor_capex`, `streams`, `tea_capital`, `tea_com`, `tea_metrics`; the benchmark table; the Monte Carlo draws, summaries and
correlations (they sample TEA quantities only); the scaling band; and the two break-even crossing tables. The other
tables (KPIs, LCA impacts and contributions, sensitivity, scaling baseline, grids, break-even targets) contain LCA
columns or rows; their TEA columns and rows are exact as well.

Without the factor table and without `LCA_FCDI_FACTORS` the runner stops before computing anything and says what to
do.

**2. With an ecoinvent 3.7 licence.**

1. Copy `01_Data/03_Reference/LCA-FCDI_D01_Reference_TRACIFactorsTemplate_v01.csv`. Keep every row, the row order and the columns.
2. For each row, enter in `value` the TRACI 2.1 score, in the row's `impact_category` (unit in its name), of
   `ref_amount` `ref_unit` of the ecoinvent 3.7 cut-off activity `ecoinvent_activity` in `location` (reference
   product `ecoinvent_flow`). The flow dictionary gives the same process per flow as `provider_process`. Every row
   needs a number: the input validation stops the run on a blank or non-numeric value (the authors' table has a
   value in every row).
3. Graphite electrode and selenium appear with two dataset variants (`inputs_v1`, `metafile_corrected`), because
   the authors' two source workbooks used different activities; the scenarios choose between them
   (`lca_dataset_overrides` in the scenario file). Keep both.
4. Either save the table as `01_Data/03_Reference/LCA-FCDI_D01_Reference_TRACIFactorsEcoinvent37_v01.csv` (git-ignored by the `*Ecoinvent37*` pattern) and run
   `python LCA-FCDI_D01-D08_RunAll.py`: the results go to `01_Data/02_Processed/`. Or keep it anywhere and run with
   `LCA_FCDI_FACTORS=<path of your table>`: the results go to `01_Data/02_Processed/altfactors_run/`.
5. `python -m fcdibes.validation` checks the table (and every other input) on its own.

With the authors' factor table this run reproduces all 243 result tables of the authors' run byte for byte
(checked when this repository was built). With factors computed in other software or database versions the LCA numbers
follow your factors.

**Other options.** `--quick` runs small sweeps into the run folder of the factor table in use: `quick_run/` with
the licensed table at its registered path, `dummy_run/` with `LCA_FCDI_FACTORS=dummy` and `altfactors_run/` with
another table; in the last two cases it overwrites the files a full run left in that folder. `--jobs N` sets the
number of worker processes (default: all cores). `--verify` rebuilds everything into `verify_run/` folders and
compares each file with the one in `01_Data/02_Processed/`; this repository commits no results, so on a fresh clone without
outputs it prints `VERIFY PASSED: 0 regenerated files identical ...; 243 not compared` and exits 0, and after a
run with your own table it compares with that run's files (the `verify_run/` folders it leaves are git-ignored).
Environment variables `LCA_FCDI_OUTDIR` and
`LCA_FCDI_FIGOUT` choose other output folders. The runner skips, and names, the project-only steps: the figures
(step 7) and the manuscript and SI builders.

## Structure and how the keys connect

| Folder | Contents |
|---|---|
| `01_Data/03_Reference/` | Every input, one file per table |
| `01_Data/02_Processed/` | Not in the repository: the code writes every result here (and run folders `*_run/`) |
| `02_Analysis_Code/` | The calculation code |

File names follow `LCA-FCDI_<D##[-D##]>_<Type>_<Description>_vNN.<ext>`: D01-D09 are the dataset lineages (D01
characterisation factors, D02 model parameters, D03 equipment catalogues, D04 inventories and normalisation, D05
electron partitioning, D06 scenarios, D07 Monte Carlo distributions and sampling settings, D08 benchmarks, the CPI-U
table and the published values compared with this work, D09 the stack and scale-up loss ledger). Every folder, file
name and version is defined once, in `02_Analysis_Code/fcdibes/datalayer.py`; a version that a later one replaced is
not part of this repository.

Every run starts by validating the inputs (`python -m fcdibes.validation` runs the same checks alone and names the
file, row and reason of every problem). The checks are structural and change no number:

- **Headers and keys.** Each table has the columns the loader needs, the original columns first and four provenance
  columns (`source`, `source_locator`, `value_type`, `note`) after them; keys are unique per file (`group, parameter`
  in the grouped parameter tables, `parameter` in the flat ones, `flow, dataset_id, impact_category` in the factor
  tables, `process, flow` in the foreground inventory, `model_name` per catalogue, and so on). Catalogues are sorted
  ascending (the model takes the first model that fits).
- **Numbers.** Numeric columns are numeric, through the coercion the loader uses.
- **Factors and flows.** The chosen factor table has a number in all ten TRACI 2.1 categories for every flow and
  dataset variant; the template, the dummy table and (when present) the licensed table have the same keys in the same
  order; the flow dictionary lists every flow that the factor, foreground, inventory and price tables use, with the
  factor table's unit.
- **Scenarios.** Inheritance is acyclic; every override names a parameter of the loaded tables and is a number; every
  `lca_dataset_overrides` entry names a dataset variant that exists for that flow in the chosen factor table.
- **Uncertainty and sampling.** Every distribution row maps to a model parameter; the sampling settings have their
  frozen values.

## Key modeling conventions

- **Scenario ids are data keys:** `base`, `cond_2v`, `cond_2v_n`, `cond_3v_n`, `lab_ideal`, `lci_graphite_battery_anode`, `nitrate_competition`, `window_full_41d`, `legacy_2v`. `base`, `cond_2v`, `cond_2v_n` and `cond_3v_n`
  are the four measured operating points 3V, 2V, 2V+N and 3V+N (with the stack and scale-up losses); `lab_ideal` is
  the base case without those losses; `nitrate_competition` is a counterfactual; `legacy_2v` is a superseded
  operating point, kept so that the correction stays auditable. Each runs with and without selenium recovery
  (`with_recovery`, `no_recovery`). Scenarios inherit from one another and override parameters and dataset
  variants.
- **Sampling is per draw, not Latin hypercube.** Draw *i* seeds its own numpy generator with `default_rng([seed, i])`
  and every uncertain input is drawn independently; seed, draw counts, the one-at-a-time step and the grid sizes are
  data (the sampling settings), so reruns give identical files.
- **Units** are in the column name or in the `unit` column. Provenance is recorded per row (`source_type` and
  `reference`, and the four appended provenance columns; the model does not read them).
- **Number tokens are read by pandas' default parser** and never re-serialised; the CSV files are UTF-8 with a
  byte-order mark and are stored byte for byte (`.gitattributes`).
- **Outputs.** Every result is a CSV (one JSON file holds the base-case KPIs); figures are not produced here.

## Licences and credit

- **Code** (`02_Analysis_Code/`): MIT License, see `LICENSE`.
- **Data** (`01_Data/`): Creative Commons Attribution 4.0 International (CC BY 4.0), see `LICENSE-DATA`.
- **Background data:** the LCA uses the ecoinvent database v3.7 (cut-off system model; ecoinvent Association),
  which is not included and remains under the ecoinvent licence. Please cite: Wernet, G., Bauer, C., Steubing, B.,
  Reinhard, J., Moreno-Ruiz, E. and Weidema, B. (2016). The ecoinvent database version 3 (part I): overview and
  methodology. *Int. J. Life Cycle Assess.* 21, 1218-1230.
- **Impact method:** TRACI 2.1 (U.S. Environmental Protection Agency), with its US 2008 per-capita normalisation
  factors (the `TRACINormalizationUS2008` table, transcribed from the authors' workbook). Bare, J. (2011). TRACI 2.0:
  the tool for the reduction and assessment of chemical and other environmental impacts 2.0. *Clean Technol.
  Environ. Policy* 13, 687-696.
- **Other sources** are cited in the `source` and `reference` columns of the CSV files (for example the benchmark
  treatment costs and the published values compared with this work); please cite them too when you reuse those
  values. The electron partitioning (D05) was measured in the 41-day continuous campaign of Riveros et al., *Water
  Res.* 2026, 303, 126244, with the reactor of Riveros et al., *Water Res.* 2025, 271, 122844.
- **How to cite:** the article and this repository (`CITATION.cff`).
