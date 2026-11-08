# Changelog

## Unreleased

- Compose: optional `SERVICE_MEM_LIMIT` caps the RAM of each API container (default 0 = no cap), like the other CPU model services.
- Breaking: startup, warm-up and inference load downloaded weights only. Before the first call, open `/admin/` → Updates → Download (or POST `/admin/updates/weights` with the admin token). Missing weights leave health/admin available and speech returns 503. Checks never download; explicit Downloads invalidate every replica.

- `SERVICE_MODELS_DIR` sets where a local (no Docker) run keeps downloaded model weights: `settings.apply_models_dir()` maps it to `HF_HOME` and `OLLAMA_MODELS` at start.
- Docs: `docs/guide-cli.md`, a PowerShell-only way to test the service.

### Breaking: common service environment variables

Service-owned environment variables now use `SERVICE_*`. Old names are no longer read.
Rename old names once in your real `.env`, Compose `environment:` block, and CI/secret settings.

| Old name | New name |
|---|---|
| `TTSPARADEE_ADMIN_PASSWORD` | `SERVICE_ADMIN_PASSWORD` |
| `TTSPARADEE_CACHE_SIZE` | `SERVICE_CACHE_SIZE` |
| `TTSPARADEE_HOST` | `SERVICE_HOST` |
| `TTSPARADEE_MAX_INFLIGHT` | `SERVICE_MAX_INFLIGHT` |
| `TTSPARADEE_NUM_THREAD` | `SERVICE_NUM_THREAD` |
| `TTSPARADEE_PORT` | `SERVICE_PORT` |
| `TTSPARADEE_REPLICAS` | `SERVICE_REPLICAS` |
| `TTSPARADEE_TOKENS_FILE` | `SERVICE_TOKENS_FILE` |
| `TTSPARADEE_UPDATE_CHECK_HOURS` | `SERVICE_UPDATE_CHECK_HOURS` |
| `TTSPARADEE_VERSION` | `SERVICE_VERSION` |
| `TTSPARADEE_WARM` | `SERVICE_WARM` |
| `TTSPARADEE_WEB_DIR` | `SERVICE_WEB_DIR` |
| `TTSPARADEE_WEIGHTS_MARKER` | `SERVICE_WEIGHTS_MARKER` |

- Move the default service port from 9160 to 29321, including API replicas and the nginx load balancer. Update any explicit old port in your real `.env`, saved tool-service `backends.json` connections, and browser or MCP client configuration once. With Compose defaults, recreate the stack to use the new host and container ports; a custom host-port override stays as configured, while container ports still change to 29321.

- Renamed `TTSParadeeModel` to `TTS_Paradee_Model`; Docker project, API image, services, load balancer and volumes now use `tts_paradee_model` names. Callers on `citadel_core` connect to `tts_paradee_model_lb` at the existing port.
- Docker volume migration: existing volumes with old names are orphaned; renaming does not move data. Stop the old stack before copying; never use `down -v`. The commands below assume the previous default Compose project name; substitute actual volume names if `-p` or `COMPOSE_PROJECT_NAME` overrode it.
  - `ttsparadee_models` → `tts_paradee_model_models`: `docker volume inspect ttsparadee_models && docker volume create tts_paradee_model_models && docker run --rm -v ttsparadee_models:/from:ro -v tts_paradee_model_models:/to alpine:3.21 sh -c "cd /from && cp -a . /to/"`
  - `ttsparadee_tokens` → `tts_paradee_model_data`: `docker volume inspect ttsparadee_tokens && docker volume create tts_paradee_model_data && docker run --rm -v ttsparadee_tokens:/from:ro -v tts_paradee_model_data:/to alpine:3.21 sh -c "cd /from && cp -a . /to/"`
- To retain an old volume instead, set its top-level volume definition to `external: true` and `name: <old-volume-name>` in a local Compose override. Rollback: stop the renamed stack, restore the previous checkout and folder name, and restart the old stack with its original volumes; copy back newer data first if the renamed stack accepted writes.

- `run.bat` loads `.env` from the service folder for a local run: it passes `--dotenv`, the service prints which variable names it loaded (never the values), and a real environment variable still wins. A plain `python src/app.py` and Docker never read `.env`.
- Untracked folders now start with `.` or `_`: `zip_versions/` is `_zip_versions/`. A local `voices`, `tem_code` or `zip_versions` folder from an older checkout needs renaming by hand.
- `run.bat` is now a menu (run the service, deploy, build the release ZIP) and `run.bat run|deploy|zip` skips it. `tools/deploy.bat`, `tools/make_zip.bat` and `tools/deploy.local.example.bat` are gone; the local deploy target is `tools/deploy.local.ini` (copy `tools/deploy.local.example.ini`).
- Admin page: **Connect to this service** card with the addresses other services can use (this page's address, same PC, Docker network, `host.docker.internal`) and every endpoint, each with a Copy button.
- Admin **Try it** panel: descriptor-driven public API requests, live text streams, JSON/media output, status and latency, cancellation and redacted curl. Auth choice (b): pasted public token stays in memory; normal scope, limits and caches apply. Shared `web/playground.js` is copied across all model and tool services.

- `run.bat`: runs `uv sync` directly; `python -m uv` failed when uv was installed only as a program (winget).
- Docker: `.dockerignore` keeps `.git`, `.venv`, tests and local state out of the build context (the same file in every model service).
- Docs: set CPU threads to the number of physical cores, not logical CPUs.
- `/metrics` also returns `ttsparadee_request_seconds`, a histogram of how long `/v1` calls took on that replica.

- Answer cache: the last answers for the same text, voice and speed are sent again without speaking (`SERVICE_CACHE_SIZE`, default 64, 0 = off; `X-Cache` header).
- `POST /v1/speak` accepts `"stream": true`: the WAV is sent sentence by sentence, so the first audio arrives sooner.
- `tools/bench.py` varies the speed per run (to skip the cache) and has `--stream`.
- First version: Paradee text to speech, an 8M-parameter model distilled from Kokoro-82M (Apache 2.0) served on the CPU, with the admin page, tokens (scope, rate limit, expiry), metrics,
  Settings card, update checks and file downloads shared with the other model services.
- `POST /v1/speak` takes `text`, `voice` and `speed` and returns a WAV file. `GET /v1/voices` lists the voices.
- `tools/bench.py` times a service so `TTS_Piper_Model` and `TTS_Paradee_Model` can be compared.
