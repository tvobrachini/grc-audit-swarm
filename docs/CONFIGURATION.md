# Configuration

Set these in `.env` or in the environment. Compose and `run_monitor.py` read `.env`; a bare `uvicorn` run does not, so export the variables there.

## LLM provider

The factory (`src/swarm/llm_factory.py`) uses the first provider that is configured, in this order. If none is set, starting a crew raises a configuration error that lists these variables (`DEMO_MODE` does not need any).

| Order | Variable | Model used |
|---|---|---|
| 1 | `OLLAMA_MODEL` (plus optional `OLLAMA_BASE_URL`, default `http://localhost:11434`) | the local Ollama model you name |
| 2 | `NVIDIA_API_KEY` (plus optional `NVIDIA_BASE_URL`) | `meta/llama-3.3-70b-instruct` |
| 3 | `GEMINI_API_KEY` | `gemini-2.5-flash`, or `GEMINI_MODEL` if set |
| 4 | `OPENAI_API_KEY` | `gpt-4o-mini` |
| 5 | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |

Gemini model names are retired regularly; set `GEMINI_MODEL` if the default stops working.

## QA reviewer model (optional)

| Variable | Purpose |
|---|---|
| `QA_LLM_MODEL` | Optional LiteLLM model string (for example `openai/gpt-4o-mini`) for the QA reviewer agents. Unset: QA uses the same provider as the other agents. |
| `QA_LLM_API_KEY`, `QA_LLM_BASE_URL` | Optional key and endpoint passed only to the QA model's client. |

## Other settings

| Variable | Purpose |
|---|---|
| `API_AUTH_TOKEN` | Shared bearer token for all `/api/*` routes. Required. |
| `VITE_API_AUTH_TOKEN` | Dev only: lets `npm run dev` send the token. It is built into the JS bundle, so never set it for a Compose or production build. |
| `REVIEWER_TOKENS_FILE` | Optional. Path to the per-reviewer tokens file (names and SHA-256 digests of their tokens; see below). When set, audit creation and every reviewer action need the person's token in the `X-Reviewer-Token` header, and their name is recorded as authenticated. Unset: names are declared, as typed. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated origin allow-list (default `http://localhost:5173`; `*` is ignored). Used for CORS and for the cross-site write check: a POST/PUT/PATCH/DELETE whose `Origin` is not on this list (or the API's own host) is refused with 403 `origin_not_allowed`. Add the UI's origin here if it is served from another host. |
| `DEMO_MODE` | `1` (or `true`, `yes`, `on`) replaces the crews with fixed demo artifacts. |
| `DEMO_QA_REJECT_PHASE` | `1`, `2` or `3`: in demo mode, QA rejects that phase until a person retries it. |
| `DEMO_STEP_DELAY` | Seconds between demo steps (default 0.4, range 0 to 5). |
| `ENVIRONMENT` | `production`/`prod`/`staging`/`stage` make the API refuse to start with `DEMO_MODE` on. |
| `VAULT_ENCRYPTION_KEY` | Base64-encoded 32-byte key. Turns on Fernet encryption and keyed digests in the vault. |
| `EVIDENCE_VAULT_PATH` | Vault directory (default `evidence_vault/` at the repo root; Compose uses `/app/data/evidence_vault` in the `app-data` volume). |
| `SESSIONS_PATH` | Session file (default `data/audit_sessions.json`). |
| `TRAIL_ANCHORS_PATH` | Approval-trail anchor file: entry count and last hash per audit (default `trail_anchors.json` next to the sessions file). It only adds protection if it is stored where someone who can edit the sessions file cannot edit it — see [Security and data handling](SECURITY-AND-DATA.md#approval-trail-hash-chain-and-anchor-file). |
| `PHASE_EXECUTOR_MAX_WORKERS` | Worker threads for phase jobs in the API (default 10). |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | Standard boto3 credentials for live evidence collection. Any boto3 credential source works. `run_monitor.py`'s environment check also accepts `AWS_REGION` in place of `AWS_DEFAULT_REGION`. |

Generate a vault key:

```bash
python -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Issue a per-reviewer token (the token is printed once; the file stores only its digest):

```bash
export REVIEWER_TOKENS_FILE=data/reviewer_tokens.json
PYTHONPATH=src uv run python -m api.reviewer_tokens add "Ivan In-Charge"
PYTHONPATH=src uv run python -m api.reviewer_tokens add --replace "Ivan In-Charge"   # rotate
PYTHONPATH=src uv run python -m api.reviewer_tokens remove "Ivan In-Charge"          # revoke
PYTHONPATH=src uv run python -m api.reviewer_tokens list                             # names only
```

In Compose, keep the file on the `app-data` volume, set `REVIEWER_TOKENS_FILE` for the API service, and run the same commands with `docker compose exec api python -m api.reviewer_tokens …`. Everyone who creates or reviews audits needs a token, preparers included. The file is re-read when it changes, so no restart is needed. If the variable is set but the file is missing or malformed, reviewer actions return 503 rather than falling back to declared names. See [Security and data handling](SECURITY-AND-DATA.md#per-reviewer-tokens) for what this does and does not protect against.

If you enabled encryption before keyed digests were introduced, re-seal older encrypted records. The command exits non-zero if any record fails its integrity check, and it never re-seals a record whose payload no longer matches its stored hash.

```bash
PYTHONPATH=src uv run python -m swarm.evidence migrate-digests
```
