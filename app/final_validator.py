"""
Step 4: Final Validator.

Independently replays the optimizer's own output the same way the judge
will, so we never return a schedule we haven't double-checked ourselves.
Raises ValueError with a clear reason on the first violation found.
"""
from __future__ import annotations

from typing import List

from app.directives import ValidatedDirectives
from app.models import BatteryInput, HourInput, HourlyPlanEntry

TOL = 0.01


def replay_and_check(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: ValidatedDirectives,
    plan: List[HourlyPlanEntry],
) -> None:
    by_hour = {h.hour: h for h in hours}
    plan_by_hour = {p.hour: p for p in plan}

    if sorted(plan_by_hour.keys()) != list(range(24)):
        raise ValueError("hourly_plan must contain exactly 24 unique hours 0-23")

    prev_energy = battery.initial_energy_kwh
    for h in range(24):
        p = plan_by_hour[h]
        hd = by_hour[h]

        if p.grid_kwh < -TOL or p.solar_used_kwh < -TOL or p.battery_kwh < -TOL:
            raise ValueError(f"hour {h}: negative energy value")

        effective_solar = hd.solar_kwh * directives.effective_solar_factor(h)
        if p.solar_used_kwh > effective_solar + TOL:
            raise ValueError(f"hour {h}: solar_used_kwh exceeds effective solar")

        charge_amt = p.battery_kwh if p.battery_action == "charge" else 0.0
        discharge_amt = p.battery_kwh if p.battery_action == "discharge" else 0.0

        balance = p.grid_kwh + p.solar_used_kwh + discharge_amt - hd.demand_kwh - charge_amt
        if abs(balance) > TOL:
            raise ValueError(f"hour {h}: energy balance violated")

        if charge_amt > battery.max_charge_kwh_per_hour + TOL:
            raise ValueError(f"hour {h}: charge rate limit exceeded")
        if discharge_amt > battery.max_discharge_kwh_per_hour + TOL:
            raise ValueError(f"hour {h}: discharge rate limit exceeded")

        if charge_amt > TOL and not directives.charge_allowed(h):
            raise ValueError(f"hour {h}: charging occurred during a no_charge_window")
        if discharge_amt > TOL and not directives.discharge_allowed(h):
            raise ValueError(f"hour {h}: discharging occurred during a no_discharge_window")

        cap = directives.max_grid_for_hour(h)
        if cap is not None and p.grid_kwh > cap + TOL:
            raise ValueError(f"hour {h}: max_grid_window exceeded")

        expected_after = prev_energy + charge_amt - discharge_amt
        if abs(expected_after - p.battery_energy_after_kwh) > TOL:
            raise ValueError(f"hour {h}: battery_energy_after_kwh inconsistent with action")

        min_reserve = directives.min_reserve_for_hour(h, battery.minimum_energy_kwh)
        if p.battery_energy_after_kwh < min_reserve - TOL:
            raise ValueError(f"hour {h}: battery below required minimum reserve")
        if p.battery_energy_after_kwh > battery.capacity_kwh + TOL:
            raise ValueError(f"hour {h}: battery exceeds capacity")

        prev_energy = p.battery_energy_after_kwh

    if abs(prev_energy - battery.initial_energy_kwh) > TOL:
        raise ValueError("end-of-day battery energy does not return to initial level")
