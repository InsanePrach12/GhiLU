"""
Step 3 of the pipeline: Math Optimizer.

Builds a mixed-integer linear program over the 24 hours and solves for the
minimum-cost schedule that satisfies:
  - energy balance every hour
  - effective solar (after solar_reduction directives)
  - battery capacity / reserve (incl. minimum_battery_reserve directives)
  - hourly charge/discharge rate limits
  - no_charge_window / no_discharge_window
  - max_grid_window
  - end-of-day battery neutrality

Uses PuLP (CBC solver, bundled — no external service needed).
"""
from __future__ import annotations

from typing import List

import pulp

from app.directives import ValidatedDirectives
from app.models import BatteryInput, HourInput, HourlyPlanEntry


def solve(
    hours: List[HourInput], battery: BatteryInput, directives: ValidatedDirectives
) -> List[HourlyPlanEntry]:
    prob = pulp.LpProblem("gridwise", pulp.LpMinimize)
    H = range(24)
    by_hour = {h.hour: h for h in hours}

    grid = {h: pulp.LpVariable(f"grid_{h}", lowBound=0) for h in H}
    solar_used = {h: pulp.LpVariable(f"solar_used_{h}", lowBound=0) for h in H}
    charge = {h: pulp.LpVariable(f"charge_{h}", lowBound=0) for h in H}
    discharge = {h: pulp.LpVariable(f"discharge_{h}", lowBound=0) for h in H}
    is_charging = {h: pulp.LpVariable(f"is_charging_{h}", cat="Binary") for h in H}
    energy_after = {h: pulp.LpVariable(f"energy_after_{h}", lowBound=0) for h in H}

    # Objective: minimize total grid cost.
    prob += pulp.lpSum(grid[h] * by_hour[h].tariff_bdt_per_kwh for h in H)

    prev_energy = battery.initial_energy_kwh
    for h in H:
        hour_data = by_hour[h]
        factor = directives.effective_solar_factor(h)
        effective_solar = hour_data.solar_kwh * factor

        # Energy balance: grid + solar_used + discharge = demand + charge
        prob += grid[h] + solar_used[h] + discharge[h] == hour_data.demand_kwh + charge[h]

        # Solar usage bounded by effective solar for the hour.
        prob += solar_used[h] <= effective_solar

        # Charge/discharge rate limits, mutually exclusive via the binary.
        max_c = battery.max_charge_kwh_per_hour
        max_d = battery.max_discharge_kwh_per_hour
        prob += charge[h] <= max_c * is_charging[h]
        prob += discharge[h] <= max_d * (1 - is_charging[h])

        if not directives.charge_allowed(h):
            prob += charge[h] == 0
        if not directives.discharge_allowed(h):
            prob += discharge[h] == 0

        cap = directives.max_grid_for_hour(h)
        if cap is not None:
            prob += grid[h] <= cap

        # Battery state transition.
        prob += energy_after[h] == prev_energy + charge[h] - discharge[h]

        # Bounds: base minimum, raised by any active minimum_battery_reserve.
        min_reserve = directives.min_reserve_for_hour(h, battery.minimum_energy_kwh)
        prob += energy_after[h] >= min_reserve
        prob += energy_after[h] <= battery.capacity_kwh

        prev_energy = energy_after[h]

    # End-of-day neutrality.
    prob += energy_after[23] == battery.initial_energy_kwh

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        # Should not happen for a feasible organizer scenario, but fail
        # controlled rather than raising a raw exception to the caller.
        raise RuntimeError(f"optimizer did not find an optimal solution (status={pulp.LpStatus[status]})")

    plan: List[HourlyPlanEntry] = []
    for h in H:
        c = charge[h].value() or 0.0
        d = discharge[h].value() or 0.0
        if c > 1e-6:
            action, magnitude = "charge", c
        elif d > 1e-6:
            action, magnitude = "discharge", d
        else:
            action, magnitude = "idle", 0.0

        plan.append(HourlyPlanEntry(
            hour=h,
            grid_kwh=round(grid[h].value() or 0.0, 4),
            solar_used_kwh=round(solar_used[h].value() or 0.0, 4),
            battery_action=action,
            battery_kwh=round(magnitude, 4),
            battery_energy_after_kwh=round(energy_after[h].value() or 0.0, 4),
        ))
    return plan
