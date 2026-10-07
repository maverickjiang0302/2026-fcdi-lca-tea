"""Unit operations.

Each solve() takes the Parameters object explicitly rather than reading module
globals, so any number of parameter sets can be solved concurrently.

Equation numbers in the comments refer to the Supporting Information
(the SI document built by the MakeSIDocx script), which states each relation
in symbols.
"""

from __future__ import annotations

import math

from .correlations import (hydraulic_power_kW, mixer_power_kW, motor_efficiency,
                           pump_efficiency)
from .params import Parameters
from .streams import Stream


class UnitProcess:
    """Base class: read inputs, write outputs and a results dict."""

    def __init__(self, name: str, inputs: list, outputs: list):
        self.name = name
        self.inputs = inputs
        self.outputs = outputs
        self.power_kW = 0.0
        self.results: dict = {}
        self._solved = False

    def solve(self, p: Parameters) -> None:
        raise NotImplementedError(self.name + ": solve() not implemented")


# ---------------------------------------------------------------------------
# FCDI-BES reactor
# ---------------------------------------------------------------------------

class FCDIBESReactor(UnitProcess):
    """Three-chamber FCDI-BES reactor.

    FCDI electromigrates selenium oxyanions out of the middle chamber; the BES
    side chambers reduce them to elemental selenium on biofilm-coated GAC.

    Current demand - and therefore membrane area, electrode mass and most of
    the capital cost - scales with removal efficiency divided by Faradaic
    efficiency (SI Eq. S1-S5). That ratio is the central lever of the whole
    assessment.

    Stack and scale-up losses (Phase 3, SI Eq. S63-S67; ProcessAssumptions
    group 'Scale-up'): the laboratory current density is derated by the
    retention factor (membrane and electrode area follow J_eff = retention x J),
    the stack supply current exceeds the Faradaic current by the shunt fraction,
    and the in-plane ohmic drop of the current collector adds to the cell
    voltage. The channel pressure drops are computed and reported only. With
    retention 1.0, shunt fraction 0.0 and graphite resistivity 0.0 every
    expression below reduces EXACTLY (bit for bit) to the Phase-1/2 arithmetic:
    J * 1.0 == J, V + 0.0 == V and eta * (1 - 0.0) == eta.
    """

    def __init__(self, p: Parameters):
        influent = Stream(
            "influent", descriptor="WW influent",
            flowrate_m3hr=p.INF["WW Flowrate Influent"],
            selenate_mgL=p.INF["selenate concentration"],
            selenite_mgL=p.INF["selenite concentration"],
            density_kg_m3=p.INF["WW Density"],
        )
        super().__init__(
            name="FCDI-BES Reactor",
            inputs=[influent, Stream("slurry_in", descriptor="Recirculating GAC slurry")],
            outputs=[Stream("effluent", descriptor="Treated WW effluent"),
                     Stream("slurry_out", descriptor="Reactor slurry outlet")],
        )

    Qin = property(lambda self: self.inputs[0])
    Q1 = property(lambda self: self.inputs[1])
    Qout = property(lambda self: self.outputs[0])
    Q2 = property(lambda self: self.outputs[1])

    def solve(self, p: Parameters) -> None:
        fcdi, uc, inf, mat = p.PROC["FCDI-BES"], p.UC, p.INF, p.MAT
        Qin, Q1, Qout, Q2 = self.Qin, self.Q1, self.Qout, self.Q2

        Qout.flowrate_m3hr = Qin.flowrate_m3hr
        removal = fcdi["Removal Efficiency"]
        faradaic = fcdi["Faradaic Efficiency"]

        # -- charge demand (SI Eq. S1-S3) ----------------------------------
        def electrons(species: str, per_mol_key: str) -> float:
            return ((Qin.flowrate_m3hr * inf[species + " concentration"] / uc[species])
                    * removal * uc["Faraday constant"] * uc[per_mol_key] / faradaic)

        total_current_A = (electrons("selenite", "e per selenite")
                           + electrons("selenate", "e per selenate")) / uc["1 hr"]

        # -- stack and scale-up losses (SI Eq. S65-S67) --------------------
        scale_up = p.PROC["Scale-up"]
        shunt = scale_up["Shunt current fraction"]
        # Effective (retained) current density at scale  (SI Eq. S67)
        j_eff = fcdi["Current Density"] * scale_up["Current density retention"]
        # In-plane ohmic drop of the collector plate, current taken off one
        # edge over the path L_c: dV = J_eff rho L_c^2 / (2 t)  (SI Eq. S65)
        t_elec_m = mat["Graphite Electrodes"]["Thickness"] / uc["1 m"]
        ir_drop_V = (j_eff * scale_up["Graphite resistivity"]
                     * scale_up["Collector current path"] ** 2 / (2 * t_elec_m))
        # Stack supply current: the shunt paths carry the fraction s of it  (SI Eq. S66)
        supply_current_A = total_current_A / (1 - shunt)
        v_eff = fcdi["Voltage Applied"] + ir_drop_V
        eta_eff = fcdi["Power Efficiency"] * (1 - shunt)
        # Electrical power, P = I V / eta  (SI Eq. S4), with the losses: P = I (V + dV) / (eta (1 - s))  (SI Eq. S66)
        self.power_kW = (total_current_A * v_eff
                         / (uc["1 kW to W"] * eta_eff))

        # -- stack geometry (SI Eq. S6-S8) ---------------------------------
        middle_volume_m3 = Qin.flowrate_m3hr * fcdi["Middle Chamber HRT"]
        cell_volume_m3 = (fcdi["Cell Length"] * fcdi["Cell Height"]
                          * fcdi["Middle Chamber Thickness"] / uc["1 m"])
        n_cells = math.ceil(middle_volume_m3 / cell_volume_m3)
        n_stacks = math.ceil(n_cells ** 0.5)
        n_cells_per_stack = math.ceil(n_cells / n_stacks)

        # -- materials (SI Eq. S5, S9-S11) ---------------------------------
        # Two membranes per cell, hence the factor of 2. The area is sized at
        # the retained current density J_eff (SI Eq. S67).
        membrane_area_m2 = 2 * total_current_A / j_eff
        membrane_mass_kg = (membrane_area_m2
                            * (mat["Membranes"]["Thickness"] / uc["1 m"])
                            * (mat["Membranes"]["Density"] * uc["1 m3"]))

        pmma = mat["Acrylic Glass (PMMA)"]
        glass_per_stack_m3 = (2 * fcdi["Cell Length"] * fcdi["Cell Height"]
                              * pmma["Thickness"] / uc["1 m"])
        glass_volume_m3 = glass_per_stack_m3 * n_stacks

        graphite = mat["Graphite Electrodes"]
        electrode_mass_kg = (membrane_area_m2
                             * (fcdi["Electrode Area"] / fcdi["Membrane Area"])
                             * (graphite["Thickness"] / uc["1 m"])
                             * (graphite["Density"] * uc["1 m3"]))

        # -- selenium production (SI Eq. S18-S20) --------------------------
        def to_bes_kghr(species: str) -> float:
            c_in = inf[species + " concentration"]
            return (Qin.flowrate_m3hr * c_in
                    - Qout.flowrate_m3hr * (1 - removal) * c_in) / uc["1 kg"]

        def se_production_kghr(species: str) -> float:
            captured = to_bes_kghr(species)
            recirculated = captured * ((1 - faradaic) / faradaic)
            return (captured + recirculated) * faradaic * (uc["Se"] / uc[species])

        selenium_kghr = (se_production_kghr("selenate")
                         + se_production_kghr("selenite"))

        # -- recirculation rate (SI Eq. S21) -------------------------------
        # Upper bound: the rate at which the GAC inventory can produce Se.
        max_recirc_m3hr = (selenium_kghr
                           / (fcdi["Side chamber HRT"] * fcdi["GAC Loading"]
                              * uc["1 m3"] * fcdi["Selenium Production Rate"]))

        # Lower bound: hold the slurry above its settling velocity in a pipe
        # sized to resist clogging. Defined at the fixed pilot reference flow
        # and scaled linearly, so which bound binds could in principle change
        # with plant scale. In practice the anti-clogging bound binds across
        # the whole range examined.
        pipe_diameter_m = (fcdi["Pipe to GAC Diameter Ratio"]
                           * mat["Granular Activated Carbon (GAC)"]["Diameter"] / 1000)
        pipe_area_m2 = math.pi / 4 * pipe_diameter_m ** 2
        min_velocity_m_s = (fcdi["Lab Recirculating Velocity"] / 100
                            * fcdi["Velocity Safety Factor"])
        min_recirc_ref_m3hr = min_velocity_m_s * pipe_area_m2 * uc["1 hr"]
        min_recirc_m3hr = (min_recirc_ref_m3hr
                           * Qin.flowrate_m3hr / fcdi["Pilot Reference WW Flowrate"])

        Q1.flowrate_m3hr = min(min_recirc_m3hr, max_recirc_m3hr)
        Q2.flowrate_m3hr = Q1.flowrate_m3hr
        binding = "anti-clog" if min_recirc_m3hr <= max_recirc_m3hr else "Se-rate"

        # -- compositions ----------------------------------------------------
        Qout.selenate_mgL = inf["selenate concentration"] * (1 - removal)
        Qout.selenite_mgL = inf["selenite concentration"] * (1 - removal)

        # Concentration in the recirculating slurry = oxyanion mass sent to the
        # BES divided by the slurry flow. The original model multiplied this by
        # ((1 - RE) * C_in) as well, which yields (kg/m3)*(mg/L) and is not a
        # concentration. Reporting only; no KPI depends on it.
        Q2.selenate_mgL = to_bes_kghr("selenate") * uc["1 kg"] / Q2.flowrate_m3hr
        Q2.selenite_mgL = to_bes_kghr("selenite") * uc["1 kg"] / Q2.flowrate_m3hr
        Q1.selenate_mgL = Q2.selenate_mgL
        Q1.selenite_mgL = Q2.selenite_mgL
        Q2.selenium_content_kghr = selenium_kghr

        # -- GAC inventory and slurry density (SI Eq. S12) -------------------
        side_volume_m3 = Q1.flowrate_m3hr * fcdi["Side chamber HRT"]
        gac_mass_kg = side_volume_m3 * fcdi["GAC Loading"] * uc["1 m3"]

        gac_bulk = mat["Granular Activated Carbon (GAC)"]["Bulk Density"]
        gac_ratio = ((fcdi["GAC Loading"] / gac_bulk)
                     / (1 + fcdi["GAC Loading"] / gac_bulk))
        slurry_density = (gac_ratio * fcdi["GAC Loading"]
                          + (1 - gac_ratio) * fcdi["Water Density"])

        self.gac_volume_ratio = gac_ratio
        self.gac_slurry_density_kg_m3 = slurry_density
        Q1.density_kg_m3 = slurry_density
        Q2.density_kg_m3 = slurry_density

        # -- channel pressure drops (SI Eq. S63-S64), reported only ---------
        # Gap and both residence times are preserved at scale, so the mean
        # velocity is the path length over the residence time. Laminar flow:
        # parallel-plate diluate channel and circular slurry channel.
        path_m = fcdi["Cell Height"]
        water_viscosity = scale_up["Water viscosity"]
        gap_m = fcdi["Middle Chamber Thickness"] / uc["1 m"]
        v_mid = path_m / (fcdi["Middle Chamber HRT"] * uc["1 hr"])
        dp_mid_Pa = 12 * water_viscosity * v_mid * path_m / gap_m ** 2                       # SI Eq. S63
        channel_m = scale_up["Slurry channel diameter"] / 1000
        v_side = path_m / (fcdi["Side chamber HRT"] * uc["1 hr"])
        dp_side_Pa = (32 * water_viscosity * scale_up["Slurry viscosity ratio"] * v_side
                      * path_m / channel_m ** 2)                                             # SI Eq. S64
        # The static-head allowance already in the P1 / P2 heads, as a pressure.
        allowance_Pa = (fcdi["Water Density"] * uc["gravity constant"]
                        * p.PROC["Pump"]["Static Head - FCDI-BES Modules"])

        self.results = {
            "Current, A": total_current_A,
            "Total membrane area, m2": membrane_area_m2,
            "Total membrane mass, kg": membrane_mass_kg,
            "Total electrode mass, kg": electrode_mass_kg,
            "Total glass shell, m3": glass_volume_m3,
            "Middle chamber volume, m3": middle_volume_m3,
            "Side chamber volume, m3": side_volume_m3,
            "GAC mass in reactor, kg": gac_mass_kg,
            "Number of cells": n_cells,
            "Number of stacks": n_stacks,
            "Number of cells per stack": n_cells_per_stack,
            "Specific energy, kWh/m3": self.power_kW / Qin.flowrate_m3hr,
            "GAC slurry density, kg/m3": slurry_density,
            "Min recirc flow (anti-clog), m3/hr": min_recirc_m3hr,
            "Max recirc flow (Se rate), m3/hr": max_recirc_m3hr,
            "Recirc bound binding": binding,
            "Recirc pipe diameter, mm": pipe_diameter_m * 1000,
            "Se produced, kg/hr": selenium_kghr,
            # stack and scale-up losses (SI Eq. S63-S67)
            "Effective current density, A/m2": j_eff,
            "In-plane IR drop, V": ir_drop_V,
            "Stack supply current, A": supply_current_A,
            "Shunt current fraction": shunt,
            "Diluate channel pressure drop, Pa": dp_mid_Pa,
            "Slurry channel pressure drop, Pa": dp_side_Pa,
            "Pressure-drop allowance, Pa": allowance_Pa,
        }
        self._solved = True


# ---------------------------------------------------------------------------
# Selenium recovery train
# ---------------------------------------------------------------------------

class RotaryScreen(UnitProcess):
    """Rotary fine-mesh screen: retains GAC (Q6), passes Se-rich water (Q3)."""

    def __init__(self, Q2: Stream):
        super().__init__(
            name="GAC Screen",
            inputs=[Q2],
            outputs=[Stream("GAC_solids", descriptor="GAC solids flow"),
                     Stream("Se_rich_water", descriptor="Se-rich water")],
        )

    Q2 = property(lambda self: self.inputs[0])
    Q6 = property(lambda self: self.outputs[0])
    Q3 = property(lambda self: self.outputs[1])

    def solve(self, p: Parameters) -> None:
        fcdi, uc, mat = p.PROC["FCDI-BES"], p.UC, p.MAT
        Q2, Q6, Q3 = self.Q2, self.Q6, self.Q3

        gac_bulk = mat["Granular Activated Carbon (GAC)"]["Bulk Density"]
        gac_ratio = ((fcdi["GAC Loading"] / gac_bulk)
                     / (1 + fcdi["GAC Loading"] / gac_bulk))

        Q3.flowrate_m3hr = Q2.flowrate_m3hr * (1 - gac_ratio)
        # Filtrate is essentially GAC-free, so it carries process-water density.
        Q3.density_kg_m3 = fcdi["Water Density"]
        Q3.selenium_kghr = Q2.selenium_content_kghr
        Q3.selenate_mgL = Q2.selenate_mgL
        Q3.selenite_mgL = Q2.selenite_mgL

        Q6.mass_flowrate_kghr = (Q2.flowrate_m3hr * fcdi["GAC Loading"]
                                 * uc["1 m3"] / uc["1 kg"])
        Q6.flowrate_m3hr = Q2.flowrate_m3hr - Q3.flowrate_m3hr

        catalog = p.select_from_catalog("Rotary Fine Mesh Filter Screen", Q2.flowrate_m3hr)
        self.catalog_result = catalog
        self.power_kW = catalog["total_power"] * p.power_multiplier("Screen")

        self.results = {
            "Q3 selenium, kg/hr": Q3.selenium_kghr,
            "Screen units required": catalog["n_units"],
            "Screen model selected": catalog["model_name"],
            "Screen power total, kW": self.power_kW,
            "Screen cost total, $": catalog["total_cost"],
        }
        self._solved = True


class Centrifuge(UnitProcess):
    """Disc-stack centrifuge: recovers solid Se (Q7), returns clarified water."""

    def __init__(self, Q3: Stream):
        super().__init__(
            name="Centrifuge",
            inputs=[Q3],
            outputs=[Stream("selenium_product", descriptor="Selenium product"),
                     Stream("clarified_water", descriptor="Clarified water")],
        )

    Q3 = property(lambda self: self.inputs[0])
    Q7 = property(lambda self: self.outputs[0])
    Q4 = property(lambda self: self.outputs[1])

    def solve(self, p: Parameters) -> None:
        Q3, Q7, Q4 = self.Q3, self.Q7, self.Q4

        # SI Eq. S23
        recovery = p.EQUIP["Disc Stack Centrifuge"]["Se recovery performance"]
        Q7.selenium_kghr = Q3.selenium_kghr * recovery
        Q4.flowrate_m3hr = Q3.flowrate_m3hr
        Q4.density_kg_m3 = p.INF["WW Density"]

        catalog = p.select_from_catalog("Centrifuge", Q3.flowrate_m3hr)
        self.catalog_result = catalog
        self.power_kW = catalog["total_power"] * p.power_multiplier("Centrifuge")

        self.results = {
            "Se recovered, kg/hr": Q7.selenium_kghr,
            "Se recovery efficiency": recovery,
            "Q4 flowrate, m3/hr": Q4.flowrate_m3hr,
            "Centrifuge units required": catalog["n_units"],
            "Centrifuge model selected": catalog["model_name"],
            "Centrifuge power total, kW": self.power_kW,
            "Centrifuge cost total, $": catalog["total_cost"],
        }
        self._solved = True


class SlurryMixer(UnitProcess):
    """Stirred tank: reconditions the slurry with Na2SO4 and sodium acetate."""

    def __init__(self, Q5: Stream):
        super().__init__(
            name="Slurry Mixer",
            inputs=[Q5,
                    Stream("Na2SO4_feed", descriptor="Na2SO4 feed"),
                    Stream("CH3COONa_feed", descriptor="CH3COONa feed")],
            outputs=[Stream("reconditioned_slurry",
                            descriptor="Recirculating GAC slurry")],
        )

    Q5 = property(lambda self: self.inputs[0])
    Na2SO4 = property(lambda self: self.inputs[1])
    CH3COONa = property(lambda self: self.inputs[2])
    Q1 = property(lambda self: self.outputs[0])

    def solve(self, p: Parameters) -> None:
        fcdi, uc, tea = p.PROC["FCDI-BES"], p.UC, p.TEA
        Q5, Q1 = self.Q5, self.Q1

        rated_volume_m3 = Q5.flowrate_m3hr * p.EQUIP["Slurry Mixer"]["Residence Time"]
        total_volume_m3 = rated_volume_m3 / p.EQUIP["Slurry Mixer"]["Utilization Ratio"]
        self.power_kW = mixer_power_kW(rated_volume_m3) * p.power_multiplier("Slurry Mixer")

        # Na2SO4 is a one-time charge amortised over the plant life; sodium
        # acetate is dosed every fifth day (SI Eq. S24-S25).
        litres = uc["1 m3"] / uc["1 kg"]
        self.Na2SO4.mass_kghr = (
            rated_volume_m3 * fcdi["Na2SO4 Loading"] * litres
            / (tea["Operating Factor"] * tea["Plant lifetime"]
               * uc["1 day"] * uc["1 year to day"])
        )
        self.CH3COONa.mass_kghr = (
            rated_volume_m3 * fcdi["NaCH3COO Loading"] * fcdi["NaCH3COO Dosing Rate"]
            * litres / (tea["Operating Factor"] * uc["1 day"])
        )

        Q1.flowrate_m3hr = Q5.flowrate_m3hr
        Q1.density_kg_m3 = Q5.density_kg_m3
        Q1.Na2SO4_kghr = self.Na2SO4.mass_kghr
        Q1.CH3COONa_kghr = self.CH3COONa.mass_kghr

        self.results = {
            "Na2SO4 dose, kg/hr": self.Na2SO4.mass_kghr,
            "CH3COONa dose, kg/hr": self.CH3COONa.mass_kghr,
            "Mixer rated volume, m3": rated_volume_m3,
            "Mixer total volume, m3": total_volume_m3,
        }
        self._solved = True


# ---------------------------------------------------------------------------
# Pumps
# ---------------------------------------------------------------------------

class Pump(UnitProcess):
    """Centrifugal pump sized on head, costed from the vendor catalogue."""

    def __init__(self, inlet: Stream, name: str, head_key: str, unit_id: str):
        super().__init__(name=name, inputs=[inlet],
                         outputs=[Stream(inlet.name + "_pumped")])
        self.head_key = head_key
        self.unit_id = unit_id

    inlet = property(lambda self: self.inputs[0])
    outlet = property(lambda self: self.outputs[0])

    def _head_m(self, p: Parameters) -> float:
        return p.PROC["Pump"][self.head_key]

    def solve(self, p: Parameters) -> None:
        uc, inlet, outlet = p.UC, self.inlet, self.outlet

        outlet.flowrate_m3hr = inlet.flowrate_m3hr
        density = inlet.get("density_kg_m3", 1000)
        outlet.density_kg_m3 = density

        head_m = self._head_m(p)
        flow_m3_s = inlet.flowrate_m3hr / uc["1 hr"]
        flow_gpm = inlet.flowrate_m3hr * uc["1 m3/hr"]

        min_flow_gpm = (p.EQUIP["Pumps"]["min flowrate to use pump efficiency formula"]
                        * uc["1 m3/hr"])
        min_power_hp = (p.EQUIP["Pumps"]["min power to use motor efficiency formula"]
                        * uc["1 kW to HP"])

        # SI Eq. S13-S15
        hydraulic_kW = hydraulic_power_kW(density, uc["gravity constant"],
                                          flow_m3_s, head_m)
        eta_pump = pump_efficiency(flow_gpm, min_flow_gpm)
        brake_kW = hydraulic_kW / eta_pump
        eta_motor = motor_efficiency(brake_kW * uc["1 kW to HP"], min_power_hp)

        self.power_kW = (brake_kW / eta_motor) * p.power_multiplier(self.unit_id)

        catalog = p.select_from_catalog("Pump", inlet.flowrate_m3hr)
        self.catalog_result = catalog
        self.purchase_cost = catalog["total_cost"]
        self.n_units = catalog["n_units"]

        self.results = {
            "Head, m": head_m,
            "Fluid density, kg/m3": density,
            "Flow, m3/hr": inlet.flowrate_m3hr,
            "Flow, gpm": flow_gpm,
            "Hydraulic power, kW": hydraulic_kW,
            "Pump efficiency": eta_pump,
            "Brake power, kW": brake_kW,
            "Motor efficiency": eta_motor,
            "Motor power, kW": self.power_kW,
            "Pump units required": catalog["n_units"],
            "Pump model selected": catalog["model_name"],
            "Pump purchase cost, $": catalog["total_cost"],
        }
        self._solved = True


class CentrifugePump(Pump):
    """P4: head derived at solve time from the actual filtrate density."""

    def __init__(self, inlet: Stream, name: str, unit_id: str):
        super().__init__(inlet, name, head_key="Centrifuge Input Head", unit_id=unit_id)

    def _head_m(self, p: Parameters) -> float:
        pump = p.PROC["Pump"]
        density = self.inlet.get("density_kg_m3", 1000)
        # Vendor inlet pressure (MPa) converted to head via dP / (rho g).
        static_head_m = (pump["Centrifuge Inlet Pressure"] * 1e6
                         / (density * p.UC["gravity constant"]))
        return (pump["Gravitational Head"] + static_head_m) * pump["Head Safety Margin"]


# ---------------------------------------------------------------------------
# Electrocultivation
# ---------------------------------------------------------------------------

class CultivationReactor(UnitProcess):
    """Batch electrocultivation reactor producing biofilm-coated GAC.

    Runs on the GAC replacement cycle (six months, so two batches a year).
    Every quantity is scaled from the lab recipe by the ratio of plant GAC
    inventory to lab GAC charge, then annualised to a continuous per-hour rate
    so it can be summed with the rest of the plant (SI Eq. S26-S27).
    """

    def __init__(self, gac_mass_in_reactor_kg: float):
        self.gac_mass_in_reactor_kg = gac_mass_in_reactor_kg
        super().__init__(
            name="Cultivation Reactor",
            inputs=[Stream("GAC_feed", descriptor="Granular activated carbon input"),
                    Stream("anode_sol", descriptor="Anode solution"),
                    Stream("cathode_sol", descriptor="Cathode solution")],
            outputs=[Stream("BioGAC_feed", descriptor="Biofilm-coated GAC"),
                     Stream("cultivation_WW", descriptor="Cultivation wastewater")],
        )
        self.membrane_mass_kg = None
        self.electrode_mass_kg = None
        self.pp_shell_mass_kg = None
        self.reactor_volume_m3 = None
        self.reactor_diameter_m = None
        self.chemicals_kghr: dict = {}

    GAC_feed = property(lambda self: self.inputs[0])
    anode_sol = property(lambda self: self.inputs[1])
    cathode_sol = property(lambda self: self.inputs[2])
    BioGAC = property(lambda self: self.outputs[0])
    WW_cult = property(lambda self: self.outputs[1])

    def solve(self, p: Parameters) -> None:
        uc, mat, equip, inv = p.UC, p.MAT, p.EQUIP, p.EC_INV
        gac = mat["Granular Activated Carbon (GAC)"]

        batches_per_year = 12.0 / gac["Biofilm GAC lifespan"]
        op_hr_per_yr = p.TEA["Operating Factor"] * uc["1 year to day"] * uc["1 day"]
        util_ratio = equip["Electrocultivation Mixer"]["Utilization Ratio"]
        mix_hrt = equip["Electrocultivation Mixer"]["Residence Time"]

        ec_in = inv["Electrocultivation"]["Input"]
        lab_gac_kg = ec_in["Granular activated carbon"]
        scale = self.gac_mass_in_reactor_kg / lab_gac_kg

        def annualised(qty_per_lab_batch: float) -> float:
            """Convert a lab-batch quantity to a continuous plant rate per hour."""
            return qty_per_lab_batch * scale * batches_per_year / op_hr_per_yr

        anode_L = ec_in["Anode Solution"]
        cathode_L = ec_in["Cathode Solution"]
        self.GAC_feed.mass_flowrate_kghr = annualised(lab_gac_kg)
        self.anode_sol.flowrate_m3hr = annualised(anode_L) / uc["1 m3"]
        self.cathode_sol.flowrate_m3hr = annualised(cathode_L) / uc["1 m3"]

        # -- solution recipes, expanded down to reagents --------------------
        anode = inv["Anode Solution"]["Input"]
        pbs_anode_L = anode["PBS"] * anode_L
        mineral_L = anode["Mineral Solution"] * anode_L
        acetate_L = anode["Sodium Acetate"] * anode_L
        bicarb_L = anode["Sodium Bicarbonate"] * anode_L
        di_anode_kg = anode["DI water"] * anode_L

        cathode = inv["Cathode Solution"]["Input"]
        pbs_cathode_L = cathode["PBS"] * cathode_L
        di_cathode_kg = cathode["DI water"] * cathode_L
        total_pbs_L = pbs_anode_L + pbs_cathode_L

        pbs = inv["PBS Solution"]["Input"]
        chem = {
            "NH4Cl": annualised(pbs["NH4Cl"] * total_pbs_L),
            "KH2PO4·H2O": annualised(pbs["KH2PO4·H2O"] * total_pbs_L),
            "Na2HPO4": annualised(pbs["Na2HPO4"] * total_pbs_L),
            "KCl": annualised(pbs["KCl"] * total_pbs_L),
        }
        di_pbs = annualised(pbs["DI water"] * total_pbs_L)

        mineral = inv["Mineral Solution"]["Input"]
        for reagent in ("NTA", "MgSO4", "MnSO4·H2O", "NaCl",
                        "FeSO4·7H2O", "CaCl2·2H2O", "CoCl2·6H2O"):
            chem[reagent] = annualised(mineral[reagent] * mineral_L)
        di_mineral = annualised(mineral["DI water"] * mineral_L)

        acetate = inv["Sodium Acetate Solution"]["Input"]
        chem["Acetic acid"] = annualised(acetate["acetic acid"] * acetate_L)
        chem["NaOH"] = annualised(acetate["Sodium Hydroxide"] * acetate_L)
        di_acetate = annualised(acetate["DI water"] * acetate_L)

        bicarb = inv["Sodium Bicarbonate Solution"]["Input"]
        chem["NaHCO3"] = annualised(bicarb["Sodium bicarbonate"] * bicarb_L)
        di_bicarb = annualised(bicarb["DI water"] * bicarb_L)

        # KH2PO4.H2O is synthesised on site. Its inventory is quoted per KP
        # batch, so normalise by that output before scaling to the plant.
        kp = inv["Potassium Phosphate Hydrate"]["Input"]
        kp_out = inv["Potassium Phosphate Hydrate"]["Output"]["KH2PO4·H2O"]
        kp_scale = (pbs["KH2PO4·H2O"] * total_pbs_L) / kp_out
        chem["H3PO4"] = annualised(kp["phosphoric acid"] * kp_scale)
        chem["KOH"] = annualised(kp["potassium hydroxide"] * kp_scale)
        di_kp = annualised(kp["DI water"] * kp_scale)

        chem["DI water"] = (annualised(di_anode_kg) + annualised(di_cathode_kg)
                            + di_pbs + di_mineral + di_acetate + di_bicarb + di_kp)

        # -- solution-preparation mixing ------------------------------------
        # One multipurpose vessel, sized for the largest solution. Aqueous, so
        # one litre is taken as one kilogram.
        solution_masses = {
            "Anode PBS": pbs_anode_L * scale,
            "Cathode PBS": pbs_cathode_L * scale,
            "Minerals": mineral_L * scale,
            "Acetate": acetate_L * scale,
            "Bicarb": bicarb_L * scale,
        }
        mixing_kW = {k: mixer_power_kW(m / 1000.0) for k, m in solution_masses.items()}
        mixer_rated_vol_L = max(solution_masses.values())
        mixer_total_vol_L = mixer_rated_vol_L / util_ratio

        ec_mixer = p.select_from_catalog("Electrocultivation Mixer", mixer_rated_vol_L,
                                         max_col="max_volume_L")
        self.ecmix_catalog_result = ec_mixer

        energy_mixing = sum(mixing_kW.values()) * mix_hrt
        energy_ec = ec_in["Electricity"] * scale
        energy_kp = kp["electricity"] * kp_scale * scale
        total_elec_kWh_hr = ((energy_mixing + energy_ec + energy_kp)
                             * batches_per_year / op_hr_per_yr)

        # -- reactor geometry (SI Eq. S27) -----------------------------------
        yield_kg_L = gac["Biofilm GAC cultivation yield"] / 1000.0
        reactor_vol_L = self.gac_mass_in_reactor_kg / yield_kg_L
        reactor_vol_m3 = reactor_vol_L / uc["1 m3"]
        # Cylinder with diameter equal to height, so V = pi/4 * D^3.
        diameter_m = (4.0 * reactor_vol_m3 / math.pi) ** (1.0 / 3.0)

        ec_proc = p.PROC["Electrocultivation"]
        membrane_mass_kg = ec_proc["Membrane mass per reactor vol"] * reactor_vol_L / 1000.0
        electrode_mass_kg = ec_proc["Electrode mass per reactor vol"] * reactor_vol_L / 1000.0

        shell = mat["Electrocultivation Reactor"]
        pp_thick_m = shell["Polypropylene thickness"] / uc["1 m"]
        pp_density = shell["Polypropylene density"] * uc["1 m3"]
        r_outer = diameter_m / 2.0 + pp_thick_m
        h_outer = diameter_m + 2.0 * pp_thick_m          # plus top and bottom caps
        pp_volume_m3 = math.pi / 4.0 * (2.0 * r_outer) ** 2 * h_outer - reactor_vol_m3
        pp_mass_kg = pp_volume_m3 * pp_density

        self.reactor_volume_m3 = reactor_vol_m3
        self.reactor_diameter_m = diameter_m
        self.membrane_mass_kg = membrane_mass_kg
        self.electrode_mass_kg = electrode_mass_kg
        self.pp_shell_mass_kg = pp_mass_kg

        ec_out = inv["Electrocultivation"]["Output"]
        self.BioGAC.mass_flowrate_kghr = annualised(ec_out["Biofilm coated GAC"])
        self.WW_cult.flowrate_m3hr = annualised(ec_out["Wastewater"]) / uc["1 m3"]
        self.power_kW = total_elec_kWh_hr
        self.chemicals_kghr = chem

        self.results = {
            "GAC input, kg/hr": self.GAC_feed.mass_flowrate_kghr,
            "Anode solution, m3/hr": self.anode_sol.flowrate_m3hr,
            "Cathode solution, m3/hr": self.cathode_sol.flowrate_m3hr,
            "BioGAC output, kg/hr": self.BioGAC.mass_flowrate_kghr,
            "WW output, m3/hr": self.WW_cult.flowrate_m3hr,
            "Electricity (total), kWh/hr": total_elec_kWh_hr,
            "Mixer rated vol (largest sol.), L": mixer_rated_vol_L,
            "Mixer total vol (rated/util), L": mixer_total_vol_L,
            "EC Mixer units required": ec_mixer["n_units"],
            "EC Mixer model selected": ec_mixer["model_name"],
            "EC Mixer cost total, $": ec_mixer["total_cost"],
            "Reactor volume, m3": reactor_vol_m3,
            "Reactor diameter (= height), m": diameter_m,
            "Membrane mass, kg": membrane_mass_kg,
            "Electrode mass, kg": electrode_mass_kg,
            "PP shell mass, kg": pp_mass_kg,
        }
        self.results.update({name + ", kg/hr": value for name, value in chem.items()})
        self._solved = True
