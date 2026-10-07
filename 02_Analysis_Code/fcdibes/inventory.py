"""Turn a solved flowsheet into mass, energy and cost flows.

Three tables come out of here and feed everything downstream:

  capex  installed cost and replacement NPV, by equipment item
  opex   annual operating cost, by cost entry and equipment
  lca    physical inventory per hour of operation, by flow and equipment

Materials are annualised by their service life (SI Eq. S49): a membrane with
an 8.5 year life contributes total_area / (8.5 x operating hours per year)
square metres per hour to the life-cycle inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .correlations import mixer_purchase_cost, replacement_npv
from .params import Parameters
from .system import System

# Cultivation reagents that are purchased, mapped to their price key.
# KH2PO4.H2O is excluded deliberately: it is synthesised on site from
# phosphoric acid and potassium hydroxide, which are themselves costed, so
# pricing it as well would double count.
_CULTIVATION_REAGENT_PRICES = {
    "DI water": "DI water",
    "Na2HPO4": "Na2HPO4",
    "NaHCO3": "NaHCO3",
    "H3PO4": "H3PO4",
    "NaOH": "NaOH",
    "KOH": "KOH",
    "NH4Cl": "NH4Cl",
    "KCl": "KCl",
    "MgSO4": "MgSO4",
    "NTA": "NTA",
    "NaCl": "NaCl",
    "MnSO4·H2O": "MnSO4·H2O",
    "FeSO4·7H2O": "FeSO4·7H2O",
    "CaCl2·2H2O": "CaCl2·2H2O",
    "CoCl2·6H2O": "CoCl2·6H2O",
    "Acetic acid": "CH3COOH",
}


@dataclass
class Inventory:
    capex: pd.DataFrame
    opex: pd.DataFrame
    lca: pd.DataFrame
    flows: dict = field(default_factory=dict)

    @property
    def total_bare_module_usd(self) -> float:
        return float(self.capex["initial_capex_usd"].sum())

    @property
    def replacement_npv_usd(self) -> float:
        return float(self.capex["replacement_npv_usd"].sum())


def build(system: System, p: Parameters) -> Inventory:
    uc, tea, price, mat, equip = p.UC, p.TEA, p.PRICE, p.MAT, p.EQUIP

    op_hr_yr = tea["Operating Factor"] * uc["1 year to day"] * uc["1 day"]
    plant_life = tea["Plant lifetime"]
    interest = tea["Interest Rate"]

    def repl(purchase: float, f_bm: float, life_yr: float) -> float:
        return replacement_npv(purchase, f_bm, life_yr, plant_life, interest)

    def elec_cost(power_kW: float) -> float:
        return power_kW * op_hr_yr * price["Electricity Price"]

    def chem_cost(kg_hr: float, unit_price: float) -> float:
        return kg_hr * op_hr_yr * unit_price

    f_bm = {
        "fcdi": equip["FCDI-BES Reactor"]["Bare-module factor"],
        "cultivation": equip["Cultivation Reactor"]["Bare-module factor"],
        "pump": equip["Pumps"]["Bare-module factor"],
        "screen": equip["Rotary Fine Mesh Filter Screen"]["Bare-module factor"],
        "centrifuge": equip["Disc Stack Centrifuge"]["Bare-module factor"],
    }

    capex_rows: list[dict] = []
    opex_rows: list[dict] = []
    lca_rows: list[dict] = []
    flows: dict = {}

    # -- FCDI-BES reactor ---------------------------------------------------
    rx = system.reactor
    membrane = mat["Membranes"]
    graphite = mat["Graphite Electrodes"]
    pmma = mat["Acrylic Glass (PMMA)"]
    gac = mat["Granular Activated Carbon (GAC)"]

    mem_area = rx.results["Total membrane area, m2"]
    mem_mass = rx.results["Total membrane mass, kg"]
    mem_purchase = mem_area * membrane["Cost"]
    mem_mass_hr = mem_mass / (membrane["Lifespan"] * op_hr_yr)

    glass_vol = rx.results["Total glass shell, m3"]
    glass_mass = glass_vol * pmma["Density"] * uc["1 m3"]
    glass_purchase = glass_mass * pmma["Cost"]
    glass_mass_hr = glass_mass / (pmma["Lifespan"] * op_hr_yr)

    elec_mass = rx.results["Total electrode mass, kg"]
    elec_purchase = elec_mass * graphite["Cost"]
    elec_mass_hr = elec_mass / (graphite["Lifespan"] * op_hr_yr)

    gac_mass = rx.results["GAC mass in reactor, kg"]
    gac_life_yr = gac["Biofilm GAC lifespan"] / 12.0
    gac_mass_hr = gac_mass / (gac_life_yr * op_hr_yr)

    fcdi_capex = (mem_purchase + glass_purchase + elec_purchase) * f_bm["fcdi"]
    fcdi_repl = (repl(mem_purchase, f_bm["fcdi"], membrane["Lifespan"])
                 + repl(glass_purchase, f_bm["fcdi"], pmma["Lifespan"])
                 + repl(elec_purchase, f_bm["fcdi"], graphite["Lifespan"]))

    capex_rows.append({"equipment": "FCDI-BES Reactor",
                       "initial_capex_usd": fcdi_capex,
                       "replacement_npv_usd": fcdi_repl})
    opex_rows.append({"entry": "Electricity", "equipment": "FCDI-BES Reactor",
                      "value_usd_yr": elec_cost(rx.power_kW)})
    opex_rows.append({"entry": "GAC", "equipment": "FCDI-BES Reactor",
                      "value_usd_yr": chem_cost(gac_mass_hr, gac["Cost"])})

    # Sub-component capital, needed for the reactor cost-breakdown figure.
    flows["CAPEX FCDI-BES membrane, $"] = mem_purchase * f_bm["fcdi"]
    flows["CAPEX FCDI-BES electrode, $"] = elec_purchase * f_bm["fcdi"]
    flows["CAPEX FCDI-BES housing, $"] = glass_purchase * f_bm["fcdi"]

    for flow, value in (("Membrane (IEM)", mem_mass_hr),
                        ("Acrylic Glass (PMMA)", glass_mass_hr),
                        ("Graphite Electrode", elec_mass_hr),
                        ("GAC", gac_mass_hr)):
        lca_rows.append({"flow": flow, "equipment": "FCDI-BES Reactor",
                         "value": value, "unit": "kg/hr"})

    # -- pumps ---------------------------------------------------------------
    pumps = {"P1": system.P1, "P2": system.P2, "P3": system.P3,
             "P4": system.P4, "P5": system.P5}
    pump_life = equip["Pumps"]["Lifespan"]
    for pid, pump in pumps.items():
        label = "Pump " + pid[-1]
        if pump is None:
            capex_rows.append({"equipment": label, "initial_capex_usd": 0.0,
                               "replacement_npv_usd": 0.0})
            opex_rows.append({"entry": "Electricity", "equipment": label,
                              "value_usd_yr": 0.0})
            flows[pid + " power, kW"] = 0.0
            lca_rows.append({"flow": "Electricity", "equipment": label,
                             "value": 0.0, "unit": "kWh/hr"})
            continue
        capex_rows.append({
            "equipment": label,
            "initial_capex_usd": pump.purchase_cost * f_bm["pump"],
            "replacement_npv_usd": repl(pump.purchase_cost, f_bm["pump"], pump_life),
        })
        opex_rows.append({"entry": "Electricity", "equipment": label,
                          "value_usd_yr": elec_cost(pump.power_kW)})
        flows[pid + " power, kW"] = pump.power_kW
        lca_rows.append({"flow": "Electricity", "equipment": label,
                         "value": pump.power_kW, "unit": "kWh/hr"})

    # -- recovery train ------------------------------------------------------
    if system.include_recovery:
        screen_purchase = system.screen.catalog_result["total_cost"]
        capex_rows.append({
            "equipment": "GAC Screen",
            "initial_capex_usd": screen_purchase * f_bm["screen"],
            "replacement_npv_usd": repl(screen_purchase, f_bm["screen"],
                                        equip["Rotary Fine Mesh Filter Screen"]["Lifespan"]),
        })
        opex_rows.append({"entry": "Electricity", "equipment": "GAC Screen",
                          "value_usd_yr": elec_cost(system.screen.power_kW)})

        cent_purchase = system.centrifuge.catalog_result["total_cost"]
        capex_rows.append({
            "equipment": "Centrifuge",
            "initial_capex_usd": cent_purchase * f_bm["centrifuge"],
            "replacement_npv_usd": repl(cent_purchase, f_bm["centrifuge"],
                                        equip["Disc Stack Centrifuge"]["Lifespan"]),
        })
        opex_rows.append({"entry": "Electricity", "equipment": "Centrifuge",
                          "value_usd_yr": elec_cost(system.centrifuge.power_kW)})

        for equipment, power in (("GAC Screen", system.screen.power_kW),
                                 ("Centrifuge", system.centrifuge.power_kW)):
            lca_rows.append({"flow": "Electricity", "equipment": equipment,
                             "value": power, "unit": "kWh/hr"})
    else:
        for equipment in ("GAC Screen", "Centrifuge"):
            capex_rows.append({"equipment": equipment, "initial_capex_usd": 0.0,
                               "replacement_npv_usd": 0.0})
            opex_rows.append({"entry": "Electricity", "equipment": equipment,
                              "value_usd_yr": 0.0})
            lca_rows.append({"flow": "Electricity", "equipment": equipment,
                             "value": 0.0, "unit": "kWh/hr"})

    # -- slurry mixer --------------------------------------------------------
    mixer = system.mixer
    mixer_purchase = mixer_purchase_cost(mixer.power_kW, uc["1 kW to HP"],
                                         tea["Chem Eng Price Index, CEPCI"])
    capex_rows.append({
        "equipment": "Slurry Mixer",
        "initial_capex_usd": mixer_purchase,
        "replacement_npv_usd": repl(mixer_purchase, 1.0,
                                    equip["Slurry Mixer"]["Lifespan"]),
    })
    na2so4_kghr = mixer.Na2SO4.mass_kghr
    ch3coona_kghr = mixer.CH3COONa.mass_kghr
    opex_rows.append({"entry": "Electricity", "equipment": "Slurry Mixer",
                      "value_usd_yr": elec_cost(mixer.power_kW)})
    opex_rows.append({"entry": "Na2SO4", "equipment": "Slurry Mixer",
                      "value_usd_yr": chem_cost(na2so4_kghr, price["Na2SO4"])})
    opex_rows.append({"entry": "CH3COONa", "equipment": "Slurry Mixer",
                      "value_usd_yr": chem_cost(ch3coona_kghr, price["CH3COONa"])})

    lca_rows.append({"flow": "Electricity", "equipment": "Slurry Mixer",
                     "value": mixer.power_kW, "unit": "kWh/hr"})
    lca_rows.append({"flow": "Na2SO4", "equipment": "Slurry Mixer",
                     "value": na2so4_kghr, "unit": "kg/hr"})
    lca_rows.append({"flow": "CH3COONa", "equipment": "Slurry Mixer",
                     "value": ch3coona_kghr, "unit": "kg/hr"})

    # -- cultivation reactor -------------------------------------------------
    cv = system.cultiv
    cv_mem_mass_hr = cv.membrane_mass_kg / (membrane["Lifespan"] * op_hr_yr)
    cv_mem_area_hr = cv_mem_mass_hr / ((membrane["Thickness"] / uc["1 m"])
                                       * (membrane["Density"] * uc["1 m3"]))
    # NOTE: the original model treats the annualised membrane replacement cost
    # as if it were a purchase cost here, unlike every other item, which uses
    # the installed stock. Preserved so the port reproduces the published
    # numbers; the cultivation reactor is well under 0.01% of C_TBM, so the
    # inconsistency is immaterial. Flagged in the model notes (Report_ModelNotes).
    cv_mem_annual_cost = cv_mem_area_hr * op_hr_yr * membrane["Cost"]

    cv_elec_mass_hr = cv.electrode_mass_kg / (graphite["Lifespan"] * op_hr_yr)
    cv_elec_purchase = cv.electrode_mass_kg * graphite["Cost"]

    shell = mat["Electrocultivation Reactor"]
    cv_pp_mass_hr = cv.pp_shell_mass_kg / (shell["Lifespan"] * op_hr_yr)
    cv_pp_purchase = cv.pp_shell_mass_kg * shell["Cost"]

    cv_mixer_purchase = cv.ecmix_catalog_result["total_cost"]

    capex_rows.append({
        "equipment": "Cultivation Reactor",
        "initial_capex_usd": (cv_mem_annual_cost * f_bm["cultivation"]
                              + cv_elec_purchase * f_bm["cultivation"]
                              + cv_pp_purchase + cv_mixer_purchase),
        "replacement_npv_usd": (
            repl(cv_mem_annual_cost, f_bm["cultivation"], membrane["Lifespan"])
            + repl(cv_elec_purchase, f_bm["cultivation"], graphite["Lifespan"])
            + repl(cv_pp_purchase, 1.0, shell["Lifespan"])
            + repl(cv_mixer_purchase, 1.0, equip["Electrocultivation Mixer"]["Lifespan"])),
    })

    opex_rows.append({"entry": "Electricity", "equipment": "Cultivation Reactor",
                      "value_usd_yr": elec_cost(cv.power_kW)})
    for reagent, price_key in _CULTIVATION_REAGENT_PRICES.items():
        opex_rows.append({
            "entry": reagent, "equipment": "Cultivation Reactor",
            "value_usd_yr": chem_cost(cv.chemicals_kghr[reagent], price[price_key]),
        })

    for flow, value in (("Membrane (IEM)", cv_mem_mass_hr),
                        ("Graphite Electrode", cv_elec_mass_hr),
                        ("Polypropylene (PP)", cv_pp_mass_hr)):
        lca_rows.append({"flow": flow, "equipment": "Cultivation Reactor",
                         "value": value, "unit": "kg/hr"})
    for reagent in ("DI water", "Na2HPO4", "NaHCO3", "H3PO4"):
        lca_rows.append({"flow": reagent, "equipment": "Cultivation Reactor",
                         "value": cv.chemicals_kghr[reagent], "unit": "kg/hr"})
    lca_rows.append({"flow": "Electricity", "equipment": "Cultivation Reactor",
                     "value": cv.power_kW, "unit": "kWh/hr"})

    # -- selenium product ----------------------------------------------------
    se_kghr = system.se_recovered_kghr
    if system.include_recovery and se_kghr > 0:
        lca_rows.append({"flow": "Selenium (recovered)", "equipment": "Centrifuge",
                         "value": se_kghr, "unit": "kg/hr"})

    # -- assemble ------------------------------------------------------------
    flows.update({
        "FCDI-BES membrane area, m2/hr": mem_area / (membrane["Lifespan"] * op_hr_yr),
        "FCDI-BES membrane mass, kg/hr": mem_mass_hr,
        "FCDI-BES electrode mass, kg/hr": elec_mass_hr,
        "FCDI-BES glass mass, kg/hr": glass_mass_hr,
        "FCDI-BES GAC mass, kg/hr": gac_mass_hr,
        "Cult. membrane mass, kg/hr": cv_mem_mass_hr,
        "Cult. electrode mass, kg/hr": cv_elec_mass_hr,
        "Cult. PP shell mass, kg/hr": cv_pp_mass_hr,
        "Cult. BioGAC, kg/hr": cv.BioGAC.mass_flowrate_kghr,
        "Cult. GAC feed, kg/hr": cv.GAC_feed.mass_flowrate_kghr,
        "Cult. wastewater, m3/hr": cv.WW_cult.flowrate_m3hr,
        "Slurry Mixer Na2SO4, kg/hr": na2so4_kghr,
        "Slurry Mixer CH3COONa, kg/hr": ch3coona_kghr,
        "Se recovered, kg/hr": se_kghr,
        "Total system power, kW": system.power_kW,
        "Operating hours per year": op_hr_yr,
    })

    capex = pd.DataFrame(capex_rows).groupby("equipment", as_index=False).sum()
    capex = capex.sort_values("initial_capex_usd", ascending=False).reset_index(drop=True)

    opex = pd.DataFrame(opex_rows)
    opex = opex.sort_values("value_usd_yr", ascending=False).reset_index(drop=True)

    lca = pd.DataFrame(lca_rows)
    lca = lca.sort_values("value", ascending=False).reset_index(drop=True)

    return Inventory(capex=capex, opex=opex, lca=lca, flows=flows)
