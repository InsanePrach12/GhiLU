"""
Step 1 of the pipeline: LLM Interpreter.

Sends the operator notes to a language model and asks for a strict JSON
array of raw interpretation objects — one per note, in order. The output of
this module is UNTRUSTED. guardrails.py must validate it before anything
here reaches the optimizer.

Swap OPENAI/local model wiring here without touching the rest of the app.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from openai import OpenAI

MODEL_NAME = os.environ.get("LLM_MODEL", "gpt-4o-mini")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["LLM_API_KEY"])
    return _client


SYSTEM_PROMPT = """\
You convert short campus-operator notes into structured energy-directive JSON.

Allowed directive_type values ONLY:
- solar_reduction        {"hours":[int...], "factor": number 0..1}   (factor = usable fraction REMAINING)
- minimum_battery_reserve {"hours":[int...], "minimum_energy_kwh": number}
- no_charge_window       {"hours":[int...]}
- no_discharge_window    {"hours":[int...]}
- max_grid_window        {"hours":[int...], "max_grid_kwh": number}
- no_op                  structured_adjustment must be null

Rules:
- Return exactly one object per input note, in the same order (note_index 0..N-1).
- Time windows are start-inclusive, end-exclusive. "1 PM to 3 PM" -> hours [13,14].
- hours must be unique integers 0-23, ascending.
- If a note does not affect the 24-hour energy schedule, use no_op with
  applies=false and structured_adjustment=null. Every other directive uses applies=true.
- Never invent demand, tariff, or battery parameters. Only use the six directive types above.
- Output ONLY a JSON object: {"directive_interpretation": [ ... ]}. No prose, no markdown fences.
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
        "Return the JSON object now."
    )

    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            timeout=20,
        )
        raw = resp.choices[0].message.content
        parsed = json.loads(raw)
        entries = parsed.get("directive_interpretation", [])
        if not isinstance(entries, list):
            return []
        return entries
    except Exception:
        # SAFE FAILURE (Problem Statement §08): malformed/unavailable LLM
        # output must not crash the service. Guardrails will fall back to
        # no_op for every note when this returns an empty list.
        return []
