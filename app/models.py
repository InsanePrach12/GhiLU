"""
Pydantic models mirroring the exact request/response contract from the
Problem Statement (sections 07 and 10).
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

BatteryAction = Literal["charge", "discharge", "idle"]


# --------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------

class HourInput(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float


class BatteryInput(BaseModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float


class OptimizeRequest(BaseModel):
    scenario_id: str
    operator_notes: List[str] = Field(min_length=1, max_length=3)
    hours: List[HourInput]
    battery: BatteryInput

    @field_validator("hours")
    @classmethod
    def hours_must_be_0_23(cls, v: List[HourInput]) -> List[HourInput]:
        seen = sorted(h.hour for h in v)
        if seen != list(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour 0..23")
        return v


# --------------------------------------------------------------------------
# Response
# --------------------------------------------------------------------------

class StructuredAdjustment(BaseModel):
    hours: Optional[List[int]] = None
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None


class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: str


class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
