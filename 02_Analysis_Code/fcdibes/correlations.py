"""Design and costing correlations, in one place with named constants.

Source: Seider, Seader, Lewin and Widagdo, *Product and Process Design
Principles*, 3rd ed., Chapters 16-17.

Each function here corresponds to a numbered equation in the Supporting
Information (the SI document built by the MakeSIDocx script); the mapping is:

    pump_efficiency        SI Eq. S14
    motor_efficiency       SI Eq. S15
    hydraulic_power_kW     SI Eq. S13
    mixer_power_kW         SI Eq. S16
    mixer_purchase_cost    SI Eq. S17
    replacement_npv        SI Eq. S29
    capital_recovery_factor SI Eq. S34

In the original notebook these lived inline: the pump and motor efficiency
polynomials were duplicated between two pump classes, the agitator power law
appeared in two more, and the mixer cost constants were bare numbers. Keeping
them here means a correction happens once and is testable.
"""

from __future__ import annotations

import math

# Seider Ch.16 Eqn 16.17 - pump efficiency, Q in gpm.
_PUMP_ETA = (-0.316, 0.24015, -0.01199)
# Seider Ch.16 Eqn 16.18 - motor efficiency, brake power in HP.
_MOTOR_ETA = (0.80, 0.0319, -0.00182)
# Seider Ch.16 - agitator shaft power, working volume in m3.
_MIXER_POWER = (0.2181, -0.5489)
# Seider Ch.16 - agitator purchase cost, power in HP, at CEPCI 567.
_MIXER_COST_BASE = 3740.0
_MIXER_COST_EXP = 0.17
_MIXER_COST_CEPCI = 567.0


def pump_efficiency(flow_gpm: float, min_flow_gpm: float) -> float:
    """Fractional pump efficiency (SI Eq. S14).

    The correlation is only valid above a minimum flow; below it, Seider's
    guidance is to evaluate at the minimum rather than extrapolate, which
    would otherwise drive the efficiency negative at pilot-scale flows.
    """
    q = max(flow_gpm, min_flow_gpm)
    ln_q = math.log(q)
    a, b, c = _PUMP_ETA
    return a + b * ln_q + c * ln_q ** 2


def motor_efficiency(brake_power_hp: float, min_power_hp: float) -> float:
    """Fractional motor efficiency, floored at the valid range (SI Eq. S15)."""
    p = max(brake_power_hp, min_power_hp)
    ln_p = math.log(p)
    a, b, c = _MOTOR_ETA
    return a + b * ln_p + c * ln_p ** 2


def hydraulic_power_kW(density_kg_m3: float, gravity: float,
                       flow_m3_s: float, head_m: float) -> float:
    """Hydraulic power, rho g Q H / 1000 (SI Eq. S13)."""
    return density_kg_m3 * gravity * flow_m3_s * head_m / 1000.0


def mixer_power_kW(rated_volume_m3: float) -> float:
    """Agitator shaft power for a rated working volume (SI Eq. S16)."""
    if rated_volume_m3 <= 0:
        return 0.0
    a, b = _MIXER_POWER
    return (10 ** (a + b * math.log10(rated_volume_m3))) * rated_volume_m3


def mixer_purchase_cost(power_kW: float, hp_per_kW: float, cepci: float) -> float:
    """Agitator purchase cost, escalated from its CEPCI basis (SI Eq. S17)."""
    return (_MIXER_COST_BASE
            * (power_kW * hp_per_kW) ** _MIXER_COST_EXP
            * (cepci / _MIXER_COST_CEPCI))


def replacement_npv(purchase_cost: float, bare_module_factor: float,
                    lifetime_yr: float, plant_life_yr: float,
                    interest_rate: float) -> float:
    """Present value of replacing an item over the plant life (SI Eq. S29).

    Replacements happen at t = lifetime, 2*lifetime, ... while they still fall
    strictly inside the plant life.
    """
    if lifetime_yr <= 0:
        return 0.0
    n_replacements = int((plant_life_yr - 1) / lifetime_yr)
    if n_replacements <= 0:
        return 0.0
    installed = purchase_cost * bare_module_factor
    return sum(installed / (1 + interest_rate) ** (lifetime_yr * k)
               for k in range(1, n_replacements + 1))


def capital_recovery_factor(interest_rate: float, lifetime_yr: float) -> float:
    """CRF = i(1+i)^N / ((1+i)^N - 1)  (SI Eq. S34).

    Also used to re-levelize published benchmark costs onto this model's basis.
    """
    if interest_rate == 0:
        return 1.0 / lifetime_yr
    growth = (1 + interest_rate) ** lifetime_yr
    return interest_rate * growth / (growth - 1)
