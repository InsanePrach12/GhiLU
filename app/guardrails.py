"""
Step 2 of the pipeline: Guardrail Validator.

Treats everything from llm_interpreter.py as untrusted. Produces:
  1. A clean List[DirectiveInterpretation] (always exactly one entry per
     note, always schema-valid) for the API response.
  2. A ValidatedDirectives bundle containing only the directives that passed
     every check, for the optimizer to apply.

Any note whose LLM output is missing, malformed, out of range, or an
unsupported type is safely downgraded to no_op rather than guessed at or
allowed to crash the service.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.directives import (
    MaxGridWindow,
    MinimumBatteryReserve,
    NoChargeWindow,
    NoDischargeWindow,
    SolarReduction,
    ValidatedDirectives,
)
from app.models import DirectiveInterpretation, StructuredAdjustment

ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _no_op(note_index: int, reason: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation=reason,
    )


def _valid_hours(hours: Any) -> List[int] | None:
    if not isinstance(hours, list) or not hours:
        return None
    try:
        ints = [int(h) for h in hours]
    except (TypeError, ValueError):
        return None
    if any(h < 0 or h > 23 for h in ints):
        return None
    if len(set(ints)) != len(ints):
        return None
    if ints != sorted(ints):
        return None
    return ints


def validate(
    operator_notes: List[str], raw_entries: List[Dict[str, Any]]
) -> tuple[List[DirectiveInterpretation], ValidatedDirectives]:
    n = len(operator_notes)

    # Index whatever the LLM returned by note_index; ignore duplicates /
    # out-of-range indices rather than trusting order.
    by_index: Dict[int, Dict[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("note_index")
        if isinstance(idx, int) and 0 <= idx < n and idx not in by_index:
            by_index[idx] = entry

    interpretations: List[DirectiveInterpretation] = []
    solar_reductions: List[SolarReduction] = []
    min_reserves: List[MinimumBatteryReserve] = []
    no_charge_windows: List[NoChargeWindow] = []
    no_discharge_windows: List[NoDischargeWindow] = []
    max_grid_windows: List[MaxGridWindow] = []

    for i in range(n):
        entry = by_index.get(i)
        if entry is None:
            interpretations.append(_no_op(i, "No usable interpretation returned; treated as no_op."))
            continue

        dtype = entry.get("directive_type")
        adj = entry.get("structured_adjustment")
        explanation = str(entry.get("explanation", ""))[:500]

        if dtype not in ALLOWED_TYPES:
            interpretations.append(_no_op(i, "Unsupported directive_type; treated as no_op."))
            continue

        if dtype == "no_op":
            interpretations.append(_no_op(i, explanation or "Note does not affect the energy schedule."))
            continue

        if not isinstance(adj, dict):
            interpretations.append(_no_op(i, "Missing structured_adjustment; treated as no_op."))
            continue

        hours = _valid_hours(adj.get("hours"))
        if hours is None:
            interpretations.append(_no_op(i, "Invalid hours array; treated as no_op."))
            continue

        if dtype == "solar_reduction":
            factor = adj.get("factor")
            if not isinstance(factor, (int, float)) or not (0 <= factor <= 1):
                interpretations.append(_no_op(i, "Invalid solar factor; treated as no_op."))
                continue
            solar_reductions.append(SolarReduction(hours=hours, factor=float(factor)))
            interpretations.append(DirectiveInterpretation(
                note_index=i, applies=True, directive_type=dtype,
                structured_adjustment=StructuredAdjustment(hours=hours, factor=float(factor)),
                explanation=explanation or "Solar output reduced for listed hours.",
            ))

        elif dtype == "minimum_battery_reserve":
            min_kwh = adj.get("minimum_energy_kwh")
            if not isinstance(min_kwh, (int, float)) or min_kwh < 0:
                interpretations.append(_no_op(i, "Invalid minimum_energy_kwh; treated as no_op."))
                continue
            min_reserves.append(MinimumBatteryReserve(hours=hours, minimum_energy_kwh=float(min_kwh)))
            interpretations.append(DirectiveInterpretation(
                note_index=i, applies=True, directive_type=dtype,
                structured_adjustment=StructuredAdjustment(hours=hours, minimum_energy_kwh=float(min_kwh)),
                explanation=explanation or "Minimum battery reserve enforced for listed hours.",
            ))

        elif dtype == "no_charge_window":
            no_charge_windows.append(NoChargeWindow(hours=hours))
            interpretations.append(DirectiveInterpretation(
                note_index=i, applies=True, directive_type=dtype,
                structured_adjustment=StructuredAdjustment(hours=hours),
                explanation=explanation or "Battery charging disabled for listed hours.",
            ))

        elif dtype == "no_discharge_window":
            no_discharge_windows.append(NoDischargeWindow(hours=hours))
            interpretations.append(DirectiveInterpretation(
                note_index=i, applies=True, directive_type=dtype,
                structured_adjustment=StructuredAdjustment(hours=hours),
                explanation=explanation or "Battery discharging disabled for listed hours.",
            ))

        elif dtype == "max_grid_window":
            max_kwh = adj.get("max_grid_kwh")
            if not isinstance(max_kwh, (int, float)) or max_kwh < 0:
                interpretations.append(_no_op(i, "Invalid max_grid_kwh; treated as no_op."))
                continue
            max_grid_windows.append(MaxGridWindow(hours=hours, max_grid_kwh=float(max_kwh)))
            interpretations.append(DirectiveInterpretation(
                note_index=i, applies=True, directive_type=dtype,
                structured_adjustment=StructuredAdjustment(hours=hours, max_grid_kwh=float(max_kwh)),
                explanation=explanation or "Grid import capped for listed hours.",
            ))

    bundle = ValidatedDirectives(
        solar_reductions=solar_reductions,
        min_reserves=min_reserves,
        no_charge_windows=no_charge_windows,
        no_discharge_windows=no_discharge_windows,
        max_grid_windows=max_grid_windows,
    )
    return interpretations, bundle
