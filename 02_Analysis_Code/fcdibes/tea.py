"""Techno-economic assessment: Guthrie capital build-up and cost of manufacture.

Reference: Seider, Seader, Lewin and Widagdo, *Product and Process Design
Principles*, 3rd ed., Chapters 16-17. Equation numbers below refer to the
Supporting Information (the SI document built by the MakeSIDocx script).

Sequence
--------
1. C_TBM -> C_DPI -> C_TDC -> C_TPI      capital build-up      SI Eq. S28-S33
2. Cost of manufacture                    maintenance needs C_TDC  SI Eq. S35-S41
3. Working capital                        depends on COM        SI Eq. S42
4. C_TCI = C_TPI + working capital                              SI Eq. S33
5. Levelized cost of treatment                                  SI Eq. S43-S47

The structure matters for the result: maintenance, taxes and operating
overhead are all fractions of depreciable capital, so a capital-intensive
reactor drives operating cost as well as capital cost.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .correlations import capital_recovery_factor
from .inventory import Inventory
from .params import Parameters
from .system import System


@dataclass
class TEAResult:
    # capital
    C_TBM: float
    C_DPI: float
    C_TDC: float
    C_TPI: float
    C_TCI: float
    working_capital: float
    site_preparation: float
    contingency: float
    contractors_fee: float
    land: float
    startup: float
    # cost of manufacture
    chemicals_annual: float
    utilities_annual: float
    labor_annual: float
    maintenance_annual: float
    operating_overhead_annual: float
    taxes_insurance_annual: float
    se_revenue_annual: float
    COM: float
    # labour and maintenance detail
    DWB: float
    DSB: float
    OSS: float
    control_lab: float
    MWB: float
    MSB: float
    MS: float
    MO: float
    # performance
    annualized_capex: float
    total_annualized_costs: float
    water_treated_m3_yr: float
    opex_per_m3: float
    capex_per_m3: float
    lcot_usd_m3: float
    total_energy_kWh_m3: float
    se_recovery_cost_usd_kg: float | None
    specific_energy_kWh_kg_se: float | None
    crf: float

    def capital_table(self) -> pd.DataFrame:
        rows = [
            ("Total Bare-Module Cost (C_TBM)", self.C_TBM),
            ("Site Preparation", self.site_preparation),
            ("Direct Permanent Investment (C_DPI)", self.C_DPI),
            ("Contingency Fee", self.contingency),
            ("Contractors Fee", self.contractors_fee),
            ("Total Depreciable Costs (C_TDC)", self.C_TDC),
            ("Land", self.land),
            ("Startup", self.startup),
            ("Total Permanent Investment (C_TPI)", self.C_TPI),
            ("Working Capital", self.working_capital),
            ("Total Capital Investment (C_TCI)", self.C_TCI),
        ]
        return pd.DataFrame(rows, columns=["entry", "value_usd"])

    def com_table(self) -> pd.DataFrame:
        rows = [
            ("Chemicals", "Total chemicals", self.chemicals_annual),
            ("Utilities", "Electricity", self.utilities_annual),
            ("Labor", "Direct wages and benefits (DW&B)", self.DWB),
            ("Labor", "Direct salaries and benefits (DS&B)", self.DSB),
            ("Labor", "Operating supplies and services (OS&S)", self.OSS),
            ("Labor", "Control laboratory", self.control_lab),
            ("Maintenance", "Maintenance wages and benefits (MW&B)", self.MWB),
            ("Maintenance", "Maintenance salaries and benefits (MS&B)", self.MSB),
            ("Maintenance", "Materials and services (M&S)", self.MS),
            ("Maintenance", "Maintenance overhead (MO)", self.MO),
            ("Operating Overhead", "Operating overhead", self.operating_overhead_annual),
            ("Taxes & Insurance", "Property taxes and insurance", self.taxes_insurance_annual),
            ("Se Credit", "Selenium sales revenue", -self.se_revenue_annual),
            ("COM", "Cost of manufacture", self.COM),
        ]
        return pd.DataFrame(rows, columns=["category", "entry", "value_usd_yr"])

    def metrics_table(self) -> pd.DataFrame:
        rows = [
            ("OPEX per volume treated", self.opex_per_m3, "$/m3"),
            ("Annualized CAPEX per volume treated", self.capex_per_m3, "$/m3"),
            ("Levelized cost of treatment (LCOT)", self.lcot_usd_m3, "$/m3"),
            ("Total energy per volume treated", self.total_energy_kWh_m3, "kWh/m3"),
            ("Se recovery cost", self.se_recovery_cost_usd_kg, "$/kg Se"),
            ("Specific energy use", self.specific_energy_kWh_kg_se, "kWh/kg Se"),
            ("Total OPEX", self.COM, "$/yr"),
            ("Total annualized CAPEX", self.annualized_capex, "$/yr"),
            ("Total annualized costs", self.total_annualized_costs, "$/yr"),
            ("Total capital investment", self.C_TCI, "$"),
        ]
        return pd.DataFrame(rows, columns=["metric", "value", "unit"])


def solve(system: System, inv: Inventory, p: Parameters) -> TEAResult:
    tea, uc, price = p.TEA, p.UC, p.PRICE

    op_hr_yr = tea["Operating Factor"] * uc["1 year to day"] * uc["1 day"]
    crf = capital_recovery_factor(tea["Interest Rate"], tea["Plant lifetime"])

    # -- 1. capital build-up (SI Eq. S28-S33) -------------------------------
    C_TBM = inv.total_bare_module_usd
    site_preparation = tea["Site Preparation Estimation"] * C_TBM
    C_DPI = C_TBM + site_preparation

    contingency = tea["Contingency Fee Estimation"] * C_DPI
    contractors_fee = tea["Contractor's Fee Estimation"] * C_DPI
    C_TDC = C_DPI + contingency + contractors_fee

    land = tea["Land Estimation"] * C_TDC
    startup = tea["Startup Estimation"] * C_TDC
    C_TPI = C_TDC + land + startup

    # -- 2. cost of manufacture (SI Eq. S35-S41) ----------------------------
    opex = inv.opex
    is_electricity = opex["entry"] == "Electricity"
    chemicals_annual = float(opex.loc[~is_electricity, "value_usd_yr"].sum())
    # The original model excluded cultivation-reactor electricity from
    # utilities without adding it anywhere else, so it fell out of COM
    # entirely. Included here; at well under a cent per year it moves nothing,
    # but the balance should close.
    utilities_annual = float(opex.loc[is_electricity, "value_usd_yr"].sum())

    n_operators = tea["No. of Shift Operators"]
    DWB = (n_operators * tea["Shifts Required per Operator"]
           * tea["Wage for Water and Wastewater Treatment Plant Operators"]
           * uc["1 year to week"] * tea["Weekly Working Hours of Shift Operators"])
    DSB = tea["DS&B Estimation"] * DWB
    OSS = tea["OS&S Estimation"] * DWB
    control_lab = tea["Chemical Technician (Control Lab) Salary"]
    labor_annual = DWB + DSB + OSS + control_lab

    MWB = tea["MW&B Estimation"] * C_TDC
    MSB = tea["MS&B Estimation"] * MWB
    MS = tea["M&S Estimation"] * MWB
    MO = tea["MO Estimation"] * MWB
    maintenance_annual = MWB + MSB + MS + MO

    operating_overhead_annual = tea["operating overhead estimation"] * (DWB + DSB + MWB + MSB)
    taxes_insurance_annual = tea["Taxes & Insurance Estimation"] * C_TDC

    se_kghr = system.se_recovered_kghr
    se_annual_kg = se_kghr * op_hr_yr
    se_revenue_annual = se_annual_kg * price["Selenium"]

    COM = (chemicals_annual + utilities_annual + labor_annual + maintenance_annual
           + operating_overhead_annual + taxes_insurance_annual - se_revenue_annual)

    # -- 3. working capital (SI Eq. S42) -------------------------------------
    working_capital = (COM / 12.0                       # one month of cash
                       + se_revenue_annual / 12.0        # accounts receivable
                       + chemicals_annual / 12.0         # accounts payable
                       + se_annual_kg / 365.0 * 7.0 * price["Selenium"])  # one week of product

    C_TCI = C_TPI + working_capital

    # -- 4. performance (SI Eq. S43-S47) -------------------------------------
    annualized_capex = (C_TCI + inv.replacement_npv_usd) * crf
    total_annualized_costs = COM + annualized_capex

    flow_m3hr = system.Qin.flowrate_m3hr
    water_treated_m3_yr = flow_m3hr * op_hr_yr

    return TEAResult(
        C_TBM=C_TBM, C_DPI=C_DPI, C_TDC=C_TDC, C_TPI=C_TPI, C_TCI=C_TCI,
        working_capital=working_capital, site_preparation=site_preparation,
        contingency=contingency, contractors_fee=contractors_fee,
        land=land, startup=startup,
        chemicals_annual=chemicals_annual, utilities_annual=utilities_annual,
        labor_annual=labor_annual, maintenance_annual=maintenance_annual,
        operating_overhead_annual=operating_overhead_annual,
        taxes_insurance_annual=taxes_insurance_annual,
        se_revenue_annual=se_revenue_annual, COM=COM,
        DWB=DWB, DSB=DSB, OSS=OSS, control_lab=control_lab,
        MWB=MWB, MSB=MSB, MS=MS, MO=MO,
        annualized_capex=annualized_capex,
        total_annualized_costs=total_annualized_costs,
        water_treated_m3_yr=water_treated_m3_yr,
        opex_per_m3=COM / water_treated_m3_yr,
        capex_per_m3=annualized_capex / water_treated_m3_yr,
        lcot_usd_m3=total_annualized_costs / water_treated_m3_yr,
        total_energy_kWh_m3=system.power_kW / flow_m3hr,
        se_recovery_cost_usd_kg=(total_annualized_costs / se_annual_kg
                                 if se_annual_kg > 0 else None),
        specific_energy_kWh_kg_se=(system.power_kW / se_kghr if se_kghr > 0 else None),
        crf=crf,
    )
