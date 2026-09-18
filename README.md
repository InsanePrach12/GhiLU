# GridWise — Our LLM-Assisted Smart Campus Energy Optimizer By GhiLU

This is our FastAPI service for the **BUP CSE Fest 2026 Hackathon (Preliminary)**. It takes 24 hours of campus energy data plus 1–3 free-form operator notes, uses an LLM only to interpret the notes, and then solves a deterministic MILP to produce the minimum-cost schedule — complete with a self-check replay that mirrors the judge's validator.

---

## 1. Our Architecture

We designed the service as a single FastAPI process. Every `/optimize-energy` request flows through four strictly separated stages. Only stage 1 (the LLM) is untrusted; stages 2–4 are pure, deterministic Python and never see raw LLM output.

```
                       Operator Notes
                            |
                            v
+---------------------------------------------------------------+
|  Stage 1 - LLM Interpreter        app/llm_interpreter.py      |
|  Gemini (google-genai, structured output, JSON schema).       |
|  One call per request. Returns raw interpretation dicts.      |
|  On any failure (timeout, parse error, missing key) we make   |
|  it return [] so the request continues safely.                |
+---------------------------------------------------------------+
                            |
                            v
+---------------------------------------------------------------+
|  Stage 2 - Guardrail Validator     app/guardrails.py          |
|  We re-validate every field, enforce hours 0..23, factors 0..1,|
|  required keys per directive type. Anything that fails is     |
|  downgraded to no_op. Always emits one DirectiveInterpretation|
|  per input note.                                              |
+---------------------------------------------------------------+
                            |
                            v
+---------------------------------------------------------------+
|  Stage 3 - Math Optimizer          app/optimizer.py           |
|  PuLP/CBC MILP over 24 hours.                                 |
|  Decision vars: grid[h], solar_used[h], charge[h],            |
|                 discharge[h], is_charging[h], energy_after[h].|
|  Objective: minimize sum(grid[h] * tariff_bdt_per_kwh).       |
|  Constraints: energy balance, effective solar, battery        |
|  capacity/min-reserve, charge & discharge rate limits,        |
|  no_charge / no_discharge windows, max_grid caps,             |
|  end-of-day battery neutrality.                               |
+---------------------------------------------------------------+
                            |
                            v
+---------------------------------------------------------------+
|  Stage 4 - Final Validator         app/final_validator.py     |
|  We replay the produced plan against the same rules with a    |
|  0.01 kWh tolerance. Returns 500 "produced schedule failed    |
|  validation" if any constraint is violated.                   |
+---------------------------------------------------------------+
                            |
                            v
                  OptimizeResponse (JSON)
```

### File map

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app, routes, error handling, response aggregation |
| `app/models.py` | Pydantic v2 request/response schemas (matches Problem Statement §07 & §10) |
| `app/llm_interpreter.py` | Stage 1 - Gemini structured-output call, system prompt, safe-failure wrapper |
| `app/guardrails.py` | Stage 2 - untrusted-output validator + downgrade-to-no_op logic |
| `app/directives.py` | Internal dataclasses for validated directives (`ValidatedDirectives`, `SolarReduction`, ...) |
| `app/optimizer.py` | Stage 3 - PuLP/CBC MILP |
| `app/final_validator.py` | Stage 4 - independent replay with 0.01 kWh tolerance |
| `scripts/run_public_samples.py` | Driver for the hackathon's public sample-cases JSON |

### LLM scope (Problem Statement §08)

We **only** use the LLM to convert `operator_notes` into structured JSON. It never sees demand, tariff, or battery numbers, and it never touches the math. The six allowed `directive_type` values are:

| Type | Structured fields |
|------|-------------------|
| `solar_reduction` | `hours: int[]`, `factor: 0..1` (fraction *remaining*) |
| `minimum_battery_reserve` | `hours: int[]`, `minimum_energy_kwh: number` |
| `no_charge_window` | `hours: int[]` |
| `no_discharge_window` | `hours: int[]` |
| `max_grid_window` | `hours: int[]`, `max_grid_kwh: number` |
| `no_op` | `structured_adjustment: null` |

---

## 2. API Endpoints

### `GET /health`

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

### `POST /optimize-energy`

The request body matches `app.models.OptimizeRequest` - `scenario_id`, `operator_notes` (1-3 strings), `hours` (exactly 24 entries, one per hour 0..23), and `battery`.

```bash
curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Our response includes `directive_interpretation` (one entry per input note, always), `hourly_plan` (24 entries with `grid_kwh`, `solar_used_kwh`, `battery_action`, `battery_kwh`, `battery_energy_after_kwh`), `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`, and a one-line `plan_summary`.

Error responses we mapped out:

| Code | Reason |
|------|--------|
| `400` | Request fails Pydantic validation (e.g. hours not 0..23) |
| `500` | Optimizer failure or the produced plan fails the final replay |

---

## 3. Local Development

### 3.1 Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3.2 Environment variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `GEMINI_API_KEY` | yes | API key for our Gemini LLM provider |
| `LLM_MODEL` | no | Model name (default: `gemini-2.5-flash`) |

We load `python-dotenv` at startup, so a local `.env` file works perfectly.

```bash
export GEMINI_API_KEY=AIzaSy...
export LLM_MODEL=gemini-2.5-flash
```

### 3.3 Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

OpenAPI docs are auto-served at `http://127.0.0.1:8000/docs`.

### 3.4 Public sample cases

```bash
python scripts/run_public_samples.py /path/to/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
```

---

## 4. Docker

### 4.1 Image

Here's how we set up the `Dockerfile`:

- Base: `python:3.11-slim`
- Working dir: `/srv`
- Installs `requirements.txt` with `--no-cache-dir`
- Copies `app/` only (no `.env`, no test fixtures baked in)
- Exposes `8000`, launches with `uvicorn app.main:app --host 0.0.0.0 --port 8000`

We made sure no secrets are baked into any layer. `GEMINI_API_KEY` and `LLM_MODEL` must be supplied at `docker run` time.

### 4.2 Build & run

```bash
docker build -t gridwise:latest .

docker run --rm -p 8000:8000 \
  -e GEMINI_API_KEY=AIzaSy... \
  -e LLM_MODEL=gemini-2.5-flash \
  gridwise:latest
```

Smoke-test:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

---

## 5. Deployment (Our Hackathon Setup)

Our service is a stateless FastAPI app, which makes it perfect for free PaaS hosting like Render or Railway. 

### 5.1 How we deployed on Render (Recommended)

We used Render to build and deploy directly from our GitHub repository:
1. Go to [Render](https://render.com) and create a **Web Service**.
2. Connect the GitHub repository.
3. Select **Docker** as the environment (Render will use our included `Dockerfile`).
4. Under **Advanced**, add the required environment variables:
   - `GEMINI_API_KEY`: Our API key
   - `LLM_MODEL`: `gemini-2.5-flash`
5. Click **Create Web Service**. Once live, the endpoints `/health` and `/optimize-energy` are available at the `onrender.com` URL.

### 5.2 Deploying on Railway (Alternative)

If using [Railway.app](https://railway.app/):
1. Click **New Project** -> **Deploy from GitHub repo**.
2. Select the repository.
3. Under the **Variables** tab, add `GEMINI_API_KEY` and `LLM_MODEL`.
4. Railway will automatically build via Docker and provide a public URL.

---

## 6. Dependencies We Used

| Package | Version | Why we chose it |
|---------|---------|-----|
| `fastapi` | `0.115.0` | HTTP framework |
| `uvicorn[standard]` | `0.30.6` | ASGI server |
| `pydantic` | `2.9.2` | Request/response schema enforcement |
| `google-genai` | `>=0.1.2` | Gemini LLM client (structured output) |
| `python-dotenv` | `>=1.0.1` | Local `.env` loading |
| `pulp` | `2.9.0` | MILP modelling + bundled CBC solver (no external solver needed) |

`pulp` ships with the CBC binary inside the wheel, so our Docker image needs no extra apt packages for the optimizer.

---

## 7. Known Limitations

- We stacked multiple overlapping `solar_reduction` directives on the same hour **multiplicatively** (conservative; never lets an hour un-reduce). Behaviour is not explicitly specified by the problem statement.
- The LLM call has a **20-second** timeout; a slow or unavailable provider degrades that scenario's notes to `no_op` rather than blocking the request indefinitely.
- We haven't added caching or a retry layer on the LLM call yet - recommended if provider latency is inconsistent under load.
- It's a single in-process uvicorn worker; horizontal scaling should be done at the container/replica level, not via `--workers N`.

---

## 8. Secret Handling

We made sure no secrets are committed to this repository or baked into the Docker image. `GEMINI_API_KEY` is read from the environment only - at startup (`load_dotenv()`) in `app/main.py` and at our first LLM call in `app/llm_interpreter.py`.
