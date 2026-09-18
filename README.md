# GridWise — LLM-Assisted Smart Campus Energy Optimizer

FastAPI service for the **BUP CSE Fest 2026 Hackathon (Preliminary)**. Takes
24 hours of campus energy data plus 1–3 free-form operator notes, uses an
LLM only to interpret the notes, then solves a deterministic MILP to produce
the minimum-cost schedule — with a self-check replay that mirrors the
judge's validator.

---

## 1. Architecture

The service is a single FastAPI process. Every `/optimize-energy` request
flows through four strictly separated stages. Only stage 1 (the LLM) is
untrusted; stages 2–4 are pure, deterministic Python and never see raw LLM
output.

```
                       Operator Notes
                            |
                            v
+---------------------------------------------------------------+
|  Stage 1 - LLM Interpreter        app/llm_interpreter.py      |
|  Gemini (google-genai, structured output, JSON schema).       |
|  One call per request. Returns raw interpretation dicts.      |
|  On any failure (timeout, parse error, missing key) it        |
|  returns [] and the request continues safely.                 |
+---------------------------------------------------------------+
                            |
                            v
+---------------------------------------------------------------+
|  Stage 2 - Guardrail Validator     app/guardrails.py          |
|  Re-validates every field, enforces hours 0..23, factors 0..1,|
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
|  Objective: minimize sum(grid[h] * tariff_bdt_per_kwh).      |
|  Constraints: energy balance, effective solar, battery        |
|  capacity/min-reserve, charge & discharge rate limits,        |
|  no_charge / no_discharge windows, max_grid caps,             |
|  end-of-day battery neutrality.                               |
+---------------------------------------------------------------+
                            |
                            v
+---------------------------------------------------------------+
|  Stage 4 - Final Validator         app/final_validator.py     |
|  Replays the produced plan against the same rules with a       |
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

The LLM is **only** used to convert `operator_notes` into structured JSON.
It never sees demand, tariff, or battery numbers, and it never touches the
math. The six allowed `directive_type` values are:

| Type | Structured fields |
|------|-------------------|
| `solar_reduction` | `hours: int[]`, `factor: 0..1` (fraction *remaining*) |
| `minimum_battery_reserve` | `hours: int[]`, `minimum_energy_kwh: number` |
| `no_charge_window` | `hours: int[]` |
| `no_discharge_window` | `hours: int[]` |
| `max_grid_window` | `hours: int[]`, `max_grid_kwh: number` |
| `no_op` | `structured_adjustment: null` |

---

## 2. API

### `GET /health`

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

### `POST /optimize-energy`

Request body matches `app.models.OptimizeRequest` - `scenario_id`,
`operator_notes` (1-3 strings), `hours` (exactly 24 entries, one per hour
0..23), and `battery`.

```bash
curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Response includes `directive_interpretation` (one entry per input note,
always), `hourly_plan` (24 entries with `grid_kwh`, `solar_used_kwh`,
`battery_action`, `battery_kwh`, `battery_energy_after_kwh`),
`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`, and a one-line
`plan_summary`.

Error responses:

| Code | Reason |
|------|--------|
| `400` | Request fails Pydantic validation (e.g. hours not 0..23) |
| `500` | Optimizer failure or the produced plan fails the final replay |

---

## 3. Local development

### 3.1 Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3.2 Environment variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `GEMINI_API_KEY` | yes | API key for the Gemini LLM provider |
| `LLM_MODEL` | no | Model name (default: `gemini-2.5-flash`) |

`python-dotenv` is loaded at startup, so a local `.env` file works.

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

`Dockerfile`:

- Base: `python:3.11-slim`
- Working dir: `/srv`
- Installs `requirements.txt` with `--no-cache-dir`
- Copies `app/` only (no `.env`, no test fixtures baked in)
- Exposes `8000`, launches with `uvicorn app.main:app --host 0.0.0.0 --port 8000`

No secrets are baked into any layer. `GEMINI_API_KEY` and `LLM_MODEL` must
be supplied at `docker run` time.

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

## 5. Deployment

The service is a stateless FastAPI app behind a single uvicorn worker, so
any of the standard patterns work. Pick the one that matches the host.

### 5.1 Bare container (single host)

```bash
docker run -d --name gridwise --restart unless-stopped -p 8000:8000 \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e LLM_MODEL=gemini-2.5-flash \
  gridwise:latest
```

Put nginx, Caddy, or a cloud load-balancer in front for TLS and rate
limiting. Forward `/health` to the container as the health-check URL.

### 5.2 Docker Compose

A minimal `compose.yaml` (create next to the project):

```yaml
services:
  gridwise:
    build: .
    image: gridwise:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      LLM_MODEL: gemini-2.5-flash
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
```

```bash
docker compose up -d --build
docker compose logs -f gridwise
```

### 5.3 Production notes

- **Process model.** A single uvicorn worker is sufficient - PuLP/CBC
  holds the GIL during solve and the LLM call is a network round-trip;
  horizontal scaling is via container replicas, not `--workers N`.
- **Resources.** MILP solve for 24 hours is light (sub-second on a
  single core), but peak memory spikes with the LP matrix. `512 MiB`
  RAM and `0.5` CPU are a safe minimum per replica.
- **Timeouts.** The Gemini client has a 20s timeout (see
  `llm_interpreter.py`); the upstream proxy should give each request
  at least 30s.
- **Secrets.** Inject `GEMINI_API_KEY` via the platform's secret store
  (Docker secret, k8s `Secret`, ECS SSM, etc.). Never commit it; never
  bake it into the image.
- **Observability.** Logs go to stdout in the standard uvicorn format;
  pipe them into the platform's log sink. Add a `/metrics` endpoint
  later if Prometheus scraping is needed.
- **Versioning.** `app.main:app` declares `version="1.0.0"`. Bump it
  with every release and rebuild the image - tags are the deployment
  unit.

### 5.4 Rollback

Re-tag the previous image and re-deploy:

```bash
docker tag gridwise:1.0.0 gridwise:latest
docker compose up -d
```

---

## 6. Dependencies

| Package | Version | Why |
|---------|---------|-----|
| `fastapi` | `0.115.0` | HTTP framework |
| `uvicorn[standard]` | `0.30.6` | ASGI server |
| `pydantic` | `2.9.2` | Request/response schema enforcement |
| `google-genai` | `>=0.1.2` | Gemini LLM client (structured output) |
| `python-dotenv` | `>=1.0.1` | Local `.env` loading |
| `pulp` | `2.9.0` | MILP modelling + bundled CBC solver (no external solver needed) |

`pulp` ships with the CBC binary inside the wheel, so the Docker image
needs no extra apt packages for the optimizer.

---

## 7. Known limitations

- Multiple overlapping `solar_reduction` directives on the same hour are
  stacked **multiplicatively** (conservative; never lets an hour
  un-reduce). Behaviour is not explicitly specified by the problem
  statement.
- The LLM call has a **20-second** timeout; a slow or unavailable
  provider degrades that scenario's notes to `no_op` rather than
  blocking the request.
- No caching or retry layer on the LLM call yet - recommended if
  provider latency is inconsistent under load.
- Single in-process uvicorn worker; horizontal scaling is done at the
  container/replica level, not via `--workers N`.

---

## 8. Secret handling

No secrets are committed to this repository or baked into the Docker
image. `GEMINI_API_KEY` is read from the environment only - at startup
(`load_dotenv()`) in `app/main.py` and at first LLM call in
`app/llm_interpreter.py`.
