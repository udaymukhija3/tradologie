# TradeVoice

[![Verify](https://github.com/udaymukhija3/tradologie/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/udaymukhija3/tradologie/actions/workflows/ci.yml)

TradeVoice is a small working slice of a multi-tenant voice-support platform. I built it to answer a narrow question: what has to sit around a voice model before you can safely let it read business data and create records?

The interesting part is the control plane rather than a scripted chatbot. A signed-in user can inspect distributors and enquiries, simulate a call, talk to the support panel, and create an enquiry only after confirming the exact details. Workspace identity comes from the server, writes are idempotent, and post-call summaries run through a Redis worker.

Read this as control-plane work rather than a shipped voice agent, and be clear on what runs by default: `VOICE_MODE` defaults to `mock`, and **the default path invokes no model at all**. It is a deterministic NLU layer with real grammars and intent scoring, not a generative one. An OpenAI Realtime WebRTC adapter is wired in and the server holds the key, but I have not run it against a live project key: the realtime tests exercise a simulated upstream, and there are no latency measurements in this repository. The Twilio endpoint verifies carrier signatures; no live carrier audio has reached it.

## What is in the project

```text
Browser -> Nginx -> FastAPI -> PostgreSQL
                         |-> Redis queue -> summary worker
                         |-> Prometheus metrics + JSON logs
Voice UI -> authenticated WebSocket or OpenAI Realtime adapter
Carrier  -> signed lifecycle webhook -> call state machine
```

The repository includes:

- a deterministic NLU layer with intent scoring, entity grammars, and multi-turn slot filling;
- workspace-scoped authentication and admin, agent, and viewer roles;
- an allowlisted tool gateway with server-owned user and workspace context;
- durable, expiring confirmations with exact-payload hashes and one-time consumption;
- idempotent call and enquiry creation, audit events, and row-locked public ID allocation;
- Alembic migrations, PostgreSQL integration tests, Redis retries and dead-letter handling;
- health checks, worker heartbeat, low-cardinality Prometheus metrics, request IDs, structured JSON logs, and bounded inputs;
- non-root read-only containers, a one-shot migration job, restore-tested backups, and resource limits;
- immutable GHCR release images with SBOM/provenance attestations and weekly dependency updates;
- a React dashboard and Playwright tests for the demo flow and for session expiry;
- ruff, mypy, and oxlint gating every push alongside the test suites.

## Run it locally

Requires Docker Desktop with Compose v2. Nothing else.

```bash
git clone https://github.com/udaymukhija3/tradologie.git
cd tradologie
docker compose up --build -d
```

The first build takes a few minutes. When it finishes, confirm the stack is healthy:

```bash
curl -fsS http://127.0.0.1:8080/api/health/ready
```

```json
{"status":"ready","database":"ok","redis":"ok","summary_worker":"ok","summary_queue_depth":0,"summary_processing_depth":0,"summary_dead_letter_depth":0}
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) and press **Open demo workspace**. The demo account is prefilled:

```text
arjun@horizon.example
TradeVoice123!
```

If port 8080 is taken, publish on another one. `APP_PORT` also drives the WebSocket origin allowlist, so pass it to every compose command in the session:

```bash
APP_PORT=8081 docker compose up --build -d
```

Stop the stack when you are done. This keeps the database and queue volumes; add `-v` to erase the demo data:

```bash
docker compose down
```

## Try the main flow

1. Sign in and look at the workspace dashboard.
2. Click **Simulate call**. The call moves through the provider-neutral lifecycle and the worker writes its summary.
3. Select **Eastern Grain Trading**, then open **Talk to Support**.
4. Start the conversation. The example prompts are shortcuts, not a menu, so type your own: `We need two hundred kg of cardamom for export to Muscat` works, and so does `who sells tea in Kerala`.
5. Notice that no enquiry is created until the exact action is read back.
6. Send `confirm`. The new `ENQ` ID appears in both the marketplace and dashboard.

Leave a detail out and the agent asks for it rather than giving up: `I need 50 tonnes of rice` gets you `Where should it be delivered?`, and answering `Dubai` completes the request.

The mock flow uses the same authorization, confirmation, tool, persistence, and audit boundaries as the optional realtime adapter. It is intentionally labelled as a simulator.

## The conversational engine

The default engine runs without any provider credentials, so it has to earn its behaviour rather than call a model. Language understanding lives in [`backend/app/voice/nlu.py`](backend/app/voice/nlu.py), which is pure and has no database or network access; [`mock.py`](backend/app/voice/mock.py) owns the socket, the tools, and the confirmation lifecycle.

It is a small NLU layer rather than a pattern match:

- entities come from real grammars, so spelled-out quantities (`two hundred kg`), grouped digits (`1,000`), and twenty unit synonyms including the `MT` used in commodity trading all parse;
- destinations survive intervening clauses and trailing politeness, so `rice for export to Dubai` yields `Dubai` and the product `Rice`, not `Export To Dubai`;
- intent is scored on weighted evidence at token boundaries, so `personal protective equipment` cannot route to the human handoff and `yes, I need 50 tonnes of rice` is an enquiry rather than a bare confirmation;
- below a confidence floor the engine asks instead of guessing, so `I can't find my invoice` does not run a distributor search;
- search resolves against the tenant's own distributor categories and locations, so it is not limited to the products in the demo script.

Around 60 unit tests in [`test_nlu.py`](backend/tests/test_nlu.py) cover the phrasings, including regressions for the two cases that used to write corrupted destinations. Deterministic is the point; scripted is not.

## Run the checks

Install the local development dependencies once:

```bash
# Python 3.12 and Node 20.19+ are required for the no-Docker toolchain
make install
cd frontend && npx playwright install chromium && cd ..
```

Then run:

```bash
make verify             # ruff, mypy, backend tests, oxlint, build, and Playwright
make verify-postgres    # the backend suite against an isolated PostgreSQL container
make docker-verify      # validate Compose and build the production images
make verify-infrastructure # boot an isolated stack and prove backup recovery
```

GitHub Actions runs the backend suite against PostgreSQL, the frontend lint/build/browser test, dependency audits, and the isolated infrastructure smoke test on every push and pull request.

## Production shape

The production path is intentionally small: an HTTPS load balancer or host proxy points at the frontend container; only Nginx can reach the API; the API and worker use managed PostgreSQL and Redis. `compose.production.yaml` does not quietly start production data stores on the application host.

Every push to `main` publishes backend and frontend images to GitHub Container Registry using immutable `sha-<commit>` tags. A `v*` tag also creates versioned image tags. The workflow attaches an SBOM, BuildKit provenance, and a GitHub artifact attestation to each image. Publishing images is not the same as claiming a live deployment; this repository currently has no public demo environment.

To deploy those images on a Docker host behind TLS, provide a full commit tag plus managed service URLs and a unique secret:

```bash
export IMAGE_TAG=sha-<full-git-commit>
export DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>
export REDIS_URL=rediss://<host>:6380/0
export JWT_SECRET=<at-least-32-random-characters>
export ALLOWED_ORIGINS=https://tradevoice.example.com
export PUBLIC_BASE_URL=https://tradevoice.example.com

docker compose -f compose.production.yaml pull
docker compose -f compose.production.yaml up -d
docker compose -f compose.production.yaml ps
```

The one-shot `migrate` service must finish before the API and worker start. Production startup fails closed if the database is SQLite, the JWT secret is weak, or the public URLs are not HTTPS. The API exposes Prometheus data at `/internal/metrics`; Nginx deliberately does not publish that route, so it is available only to a collector on the application network.

For the local Compose database, create and verify a portable custom-format backup with:

```bash
make backup
make verify-backup
```

`make verify-backup` restores the archive into a temporary database, checks its contents, and removes the temporary database. In a hosted environment, use the managed PostgreSQL provider's automated snapshots as the primary recovery mechanism and test restores separately.

## Run without Docker

For faster backend or frontend development, SQLite is the default and Redis is optional. When Redis is unavailable, summaries are completed synchronously so local work does not silently lose data.

```bash
make install
make migrate
make seed
make backend
```

In another terminal:

```bash
make frontend
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Copy values from `.env.example` when you need to change ports, origins, storage, or provider settings.

## Optional OpenAI Realtime mode

The deterministic mock is the supported no-credential demo. To exercise the WebRTC adapter, set these backend environment variables before starting the stack:

```bash
export VOICE_MODE=openai
export OPENAI_API_KEY=your-project-key
docker compose up --build -d
```

The permanent project key stays in the backend. The browser receives only the negotiated session response. Live audio quality, microphone behavior, and provider latency depend on the account, browser, and hardware and are not represented by the mock tests.

## Deliberate limits

The largest gap is the one nearest the title: the realtime path has never been exercised against a live model, so nothing here measures turn latency, interruption behaviour, or transcription quality. The instrument exists -- the panel reports input-committed to first-audible-audio per turn -- but it has only ever measured the browser speech fallback.

This is a portfolio-scale vertical slice, not a complete contact-centre product. It does not include live PSTN media streaming, recording consent, DTMF, warm transfer, billing, RAG, multilingual evaluation, a public cloud environment, or production traffic claims. TLS termination, managed PostgreSQL/Redis provisioning, DNS, and automated provider snapshots remain host responsibilities. Realtime session state and rate limiting are still process-local, so the production topology deliberately runs one API replica until those move to shared storage.
