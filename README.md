# TradeVoice

TradeVoice is a small working slice of a multi-tenant voice-support platform. I built it to answer a narrow question: what has to sit around a voice model before you can safely let it read business data and create records?

The interesting part is the control plane rather than a scripted chatbot. A signed-in user can inspect distributors and enquiries, simulate a call, talk to the support panel, and create an enquiry only after confirming the exact details. Workspace identity comes from the server, writes are idempotent, and post-call summaries run through a Redis worker.

The default demo is deterministic and does not need paid credentials. There is also an OpenAI Realtime WebRTC adapter and a signature-checked Twilio lifecycle endpoint, but this repository does not claim that live telephone audio has been tested.

## What is in the project

```text
Browser -> Nginx -> FastAPI -> PostgreSQL
                         |-> Redis queue -> summary worker
Voice UI -> authenticated WebSocket or OpenAI Realtime adapter
Carrier  -> signed lifecycle webhook -> call state machine
```

The repository includes:

- workspace-scoped authentication and admin, agent, and viewer roles;
- an allowlisted tool gateway with server-owned user and workspace context;
- durable, expiring confirmations with exact-payload hashes and one-time consumption;
- idempotent call and enquiry creation, audit events, and row-locked public ID allocation;
- Alembic migrations, PostgreSQL integration tests, Redis retries and dead-letter handling;
- health checks, worker heartbeat, request IDs, structured logs, and bounded inputs;
- a React dashboard and a Playwright test for the complete demo flow.

## Run it locally

The easiest path is Docker Desktop with Compose v2:

```bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/health/ready
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). The fictional demo account is prefilled, or you can enter:

```text
arjun@horizon.example
TradeVoice123!
```

The readiness response should report PostgreSQL, Redis, and the summary worker as healthy:

```json
{"status":"ready","database":"ok","redis":"ok","summary_worker":"ok","summary_queue_depth":0}
```

When you are finished:

```bash
docker compose down
```

That keeps the local database and queue volumes. Use `docker compose down -v` only when you intentionally want to erase the demo data and start over.

## Try the main flow

1. Sign in and look at the workspace dashboard.
2. Click **Simulate call**. The call moves through the provider-neutral lifecycle and the worker writes its summary.
3. Select **Eastern Grain Trading**, then open **Talk to Support**.
4. Start the conversation and choose `I need 50 tonnes of basmati rice for Dubai.`
5. Notice that no enquiry is created until the exact action is read back.
6. Send `confirm`. The new `ENQ` ID appears in both the marketplace and dashboard.

The mock flow uses the same authorization, confirmation, tool, persistence, and audit boundaries as the optional realtime adapter. It is intentionally labelled as a simulator.

## Run the checks

Install the local development dependencies once:

```bash
make install
cd frontend && npx playwright install chromium && cd ..
```

Then run:

```bash
make verify             # backend tests, lint, build, and Playwright
make verify-postgres    # the backend suite against an isolated PostgreSQL container
make docker-verify      # validate Compose and build the production images
```

GitHub Actions runs the backend suite against PostgreSQL and runs the frontend lint, build, and browser test on every push and pull request.

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

This is a portfolio-scale vertical slice, not a complete contact-centre product. It does not include live PSTN media streaming, recording consent, DTMF, warm transfer, billing, RAG, multilingual evaluation, cloud infrastructure, or production traffic claims. Realtime session state and rate limiting are still process-local, so they would need to move to shared storage before horizontally scaling the API.
