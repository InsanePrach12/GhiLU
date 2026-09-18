"""
Step 1 of the pipeline: LLM Interpreter.

Sends the operator notes to a language model and asks for a strict JSON
array of raw interpretation objects — one per note, in order. 
Updated to use Google GenAI SDK (Gemini) with Structured Outputs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from google import genai
from pydantic import BaseModel

from app.models import DirectiveInterpretation

logger = logging.getLogger("gridwise")

PRIMARY_MODEL = os.environ.get("LLM_MODEL", "gemini-3.5-flash")
FALLBACK_MODELS = [
    PRIMARY_MODEL,
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-3.5-flash"
]

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    return _client


class LLMResponse(BaseModel):
    directive_interpretation: List[DirectiveInterpretation]


SYSTEM_PROMPT = """\
You are an expert at interpreting campus operator notes into structured energy-directive JSON.

Allowed directive_type values ONLY:
- solar_reduction        {"hours":[int...], "factor": number 0..1}   (factor = usable fraction REMAINING)
- minimum_battery_reserve {"hours":[int...], "minimum_energy_kwh": number}
- no_charge_window       {"hours":[int...]}
- no_discharge_window    {"hours":[int...]}
- max_grid_window        {"hours":[int...], "max_grid_kwh": number}
- no_op                  structured_adjustment must be null

Rules:
- Return exactly one object per input note, in the same order (note_index 0..N-1).
- Time windows are start-inclusive, end-exclusive. "1 PM to 3 PM" means hours [13,14].
- hours must be unique integers 0-23, ascending.
- If a note does not affect the 24-hour energy schedule, use no_op with applies=false and structured_adjustment=null. Every other directive uses applies=true.
- Never invent demand, tariff, or battery parameters. Only use the six directive types above.

Examples:
Input note 0: "Solar output will drop to about 20% from 1 PM to 3 PM."
Output element: note_index: 0, applies: true, directive_type: "solar_reduction", structured_adjustment: {"hours": [13, 14], "factor": 0.2}, explanation: "..."

Input note 1: "Do not charge the battery between 2 PM and 4 PM."
Output element: note_index: 1, applies: true, directive_type: "no_charge_window", structured_adjustment: {"hours": [14, 15]}, explanation: "..."

Input note 2: "Keep at least 120 kWh in reserve from 6 PM until 9 PM."
Output element: note_index: 2, applies: true, directive_type: "minimum_battery_reserve", structured_adjustment: {"hours": [18, 19, 20], "minimum_energy_kwh": 120}, explanation: "..."

Input note 3: "The cafeteria menu changes tomorrow."
Output element: note_index: 3, applies: false, directive_type: "no_op", structured_adjustment: null, explanation: "..."
"""


def interpret_notes(operator_notes: List[str]) -> List[Dict[str, Any]]:
    """Calls the LLM once and returns the raw (untrusted) list of
    interpretation dicts, one per note. Never raises on bad LLM JSON —
    callers must treat a parse failure as "no directives extracted" and let
    guardrails / safe-failure logic take over.
    """
    notes_block = "\n".join(f"{i}: {n}" for i, n in enumerate(operator_notes))
    user_prompt = (
        f"There are {len(operator_notes)} operator notes:\n{notes_block}\n\n"
        "Return the structured JSON object now."
    )

    client = _get_client()
    
    for model_name in FALLBACK_MODELS:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=LLMResponse,
                )
            )
            
            parsed = json.loads(resp.text)
            return parsed.get("directive_interpretation", [])
        except Exception as e:
            logger.warning(f"LLM call failed for model {model_name}: {e}")
            continue
            
    # SAFE FAILURE (Problem Statement §08): malformed/unavailable LLM
    # output must not crash the service. Guardrails will fall back to
    # no_op for every note when this returns an empty list.
    return []
