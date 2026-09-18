"""
GridWise — LLM-Assisted Smart Campus Energy Optimization

Pipeline:  Energy Data + Notes -> LLM Interpreter -> Guardrail Validator
           -> Math Optimizer -> Final Validator -> API Response
"""
from __future__ import annotations

import logging
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import final_validator, guardrails, llm_interpreter, optimizer
from app.models import HealthResponse, OptimizeRequest, OptimizeResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise")

app = FastAPI(title="GridWise Optimizer", version="1.0.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.exception_handler(ValidationError)
def handle_validation_error(request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "invalid request schema"})


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(payload: OptimizeRequest) -> OptimizeResponse:
    # --- Step 1: LLM interpretation (untrusted) ---------------------------
    raw_entries = llm_interpreter.interpret_notes(payload.operator_notes)

    # --- Step 2: Guardrail validation --------------------------------------
    interpretations, directives = guardrails.validate(payload.operator_notes, raw_entries)

    # --- Step 3: Optimize ---------------------------------------------------
    try:
        plan = optimizer.solve(payload.hours, payload.battery, directives)
    except Exception as exc:  # noqa: BLE001 - controlled 500, no stack trace leaked
        logger.exception("optimizer failed for scenario %s", payload.scenario_id)
        raise HTTPException(status_code=500, detail="optimization failed") from exc

    # --- Step 4: Final replay / validation -----------------------------------
    try:
        final_validator.replay_and_check(payload.hours, payload.battery, directives, plan)
    except ValueError as exc:
        logger.error("final validation failed for scenario %s: %s", payload.scenario_id, exc)
        raise HTTPException(status_code=500, detail="produced schedule failed validation") from exc

    tariff_by_hour = {h.hour: h.tariff_bdt_per_kwh for h in payload.hours}
    total_grid = round(sum(p.grid_kwh for p in plan), 4)
    total_cost = round(sum(p.grid_kwh * tariff_by_hour[p.hour] for p in plan), 4)
    peak_grid = round(max(p.grid_kwh for p in plan), 4)

    applied = [i.directive_type for i in interpretations if i.applies]
    summary = (
        f"Optimized 24-hour schedule for {payload.scenario_id}: "
        f"{len(applied)} operator directive(s) applied ({', '.join(applied) or 'none'}); "
        f"total grid cost {total_cost} BDT, peak grid draw {peak_grid} kWh."
    )

    return OptimizeResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=interpretations,
        hourly_plan=plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=summary,
    )
