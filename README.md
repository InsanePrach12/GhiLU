# GridWise — LLM-Assisted Smart Campus Energy Optimizer

FastAPI service for the BUP CSE Fest 2026 Hackathon preliminary.

## Architecture

```
Energy Data + Operator Notes
        |
        v
  LLM Interpreter        (app/llm_interpreter.py) -- reads notes, emits raw JSON directives
        |
        v
  Guardrail Validator     (app/guardrails.py)      -- rejects/downgrades bad LLM output to no_op
        |
        v
  Math Optimizer          (app/optimizer.py)        -- MILP (PuLP/CBC), minimizes grid cost
        |
        v
  Final Validator          (app/final_validator.py)  -- independently replays the plan
        |
        v
  API Response
```

LLM role: interprets `operator_notes` into structured directives only. It never
touches the optimization math directly — everything it returns is validated
deterministically first (Problem Statement §08).

Optimizer: a mixed-integer linear program (PuLP + CBC, bundled with PuLP — no
external solver install needed) minimizing `sum(grid_kwh * tariff)` subject to
energy balance, effective solar, battery bounds/rate limits, all five
directive types, and end-of-day battery neutrality.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Required environment variables

| Variable       | Required | Meaning                                   |
|----------------|----------|--------------------------------------------|
| `LLM_API_KEY`  | yes      | API key for the LLM provider (OpenAI-compatible) |
| `LLM_MODEL`    | no       | Model name (default: `gpt-4o-mini`)        |

```bash
export LLM_API_KEY=sk-...
export LLM_MODEL=gpt-4o-mini
```

## Run locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Health check

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

### Sample request

```bash
curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

### Public sample cases

```bash
python scripts/run_public_samples.py /path/to/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
```

## Docker fallback

```bash
docker build -t gridwise:latest .
docker run -p 8000:8000 -e LLM_API_KEY=sk-... -e LLM_MODEL=gpt-4o-mini gridwise:latest
curl http://127.0.0.1:8000/health
```

The image binds to `0.0.0.0:8000` and contains no baked-in secrets — the API
key is supplied at `docker run` time.

## Dependencies

- FastAPI / Pydantic v2 — request/response schema enforcement
- `openai` client — LLM calls (swap `app/llm_interpreter.py` for a different
  provider/local model; the rest of the pipeline is provider-agnostic)
- PuLP (CBC) — MILP solver for the 24-hour schedule

## Known limitations

- Multiple overlapping `solar_reduction` directives on the same hour are
  stacked multiplicatively (conservative), which is not explicitly specified.
- The LLM call has a 20s timeout; a slow/unavailable provider degrades that
  scenario's notes to `no_op` rather than blocking the request indefinitely.
- No caching/retry layer on the LLM call yet — recommended before submission
  if provider latency is inconsistent.

## Secret handling

No secrets are committed to this repository or baked into the Docker image.
`LLM_API_KEY` is read from the environment only.
