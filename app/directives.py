"""
Internal, already-validated directive objects that the optimizer consumes.
This is deliberately separate from the LLM's raw output: nothing here is
trusted until guardrails.py has checked it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SolarReduction:
    hours: List[int]
    factor: float


@dataclass
class MinimumBatteryReserve:
    hours: List[int]
    minimum_energy_kwh: float


@dataclass
class NoChargeWindow:
    hours: List[int]


@dataclass
class NoDischargeWindow:
    hours: List[int]


@dataclass
class MaxGridWindow:
    hours: List[int]
    max_grid_kwh: float


@dataclass
class ValidatedDirectives:
    """Aggregated, guardrail-passed directives ready for the optimizer."""

    solar_reductions: List[SolarReduction]
    min_reserves: List[MinimumBatteryReserve]
    no_charge_windows: List[NoChargeWindow]
    no_discharge_windows: List[NoDischargeWindow]
    max_grid_windows: List[MaxGridWindow]

    def effective_solar_factor(self, hour: int) -> float:
        """If multiple solar_reduction directives hit the same hour, stack
        multiplicatively (conservative — never lets an hour un-reduce)."""
        factor = 1.0
        for sr in self.solar_reductions:
            if hour in sr.hours:
                factor *= sr.factor
        return factor

    def min_reserve_for_hour(self, hour: int, base_minimum: float) -> float:
        best = base_minimum
        for r in self.min_reserves:
            if hour in r.hours:
                best = max(best, r.minimum_energy_kwh)
        return best

    def charge_allowed(self, hour: int) -> bool:
        return not any(hour in w.hours for w in self.no_charge_windows)

    def discharge_allowed(self, hour: int) -> bool:
        return not any(hour in w.hours for w in self.no_discharge_windows)

    def max_grid_for_hour(self, hour: int) -> Optional[float]:
        caps = [w.max_grid_kwh for w in self.max_grid_windows if hour in w.hours]
        return min(caps) if caps else None
