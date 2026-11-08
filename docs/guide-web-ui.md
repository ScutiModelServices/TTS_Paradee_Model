# TTS_Paradee_Model: the admin web UI

One page, `/admin/`, for tokens and CPU threads. It is the same for every way of running the service (Docker
on the same device, Docker on a deployment device, no Docker). Only the address differs:

| Run | Address |
|---|---|
| Docker or `run.bat` on this PC | `http://127.0.0.1:29321/admin/` |
| Docker on the deployment box | `http://<box-ip>:29321/admin/` |

If `SERVICE_PORT` is changed, use that port.

## Before you open it

The page needs the admin password: the value of `SERVICE_ADMIN_PASSWORD`.
- Docker: set it in `.env` next to `docker-compose.yml`, then `docker compose up -d`.
- No Docker: set it in `.env` in this folder and start with `run.bat` (it passes `--dotenv`), or set it in the environment first; a real environment variable wins over `.env`. A plain `python src/app.py` does not read `.env`.
- Empty password = no auth at all and the page answers `Admin password required (set SERVICE_ADMIN_PASSWORD)`. Set
  one to use the page.

## Sign in and service status

Type the admin password and press **Sign in**. A wrong password shows an inline `Wrong admin password` error.
The password stays in `sessionStorage` for this browser tab, so reloading restores the session. **Sign out** clears
the password and any freshly created token. If browser storage is blocked, sign in again after reloading.

The header shows the service address and a health badge with an icon and text: **Healthy**, **Starting**, or
**Unreachable**. Health is checked without authentication every 10 seconds while signed in. **Starting** means the model
is not loaded yet (download it through Updates before use). Tiles show model readiness, replicas, parallel generations, CPU threads, and active tokens.
Use **Theme** to cycle between system, light, and dark appearance; this preference is saved in the browser.

## Tokens

Each calling project gets its own token, so one can be revoked without touching the others.
- **Create:** type a project name (up to 64 characters) and press **Create token**. The token appears once in
  an accent-tinted box with a **Copy** button and the reminder “Shown once. Copy it before you leave.”
  The box is cleared on the next create attempt or sign-out. Clipboard access has a fallback for plain HTTP;
  if both methods are blocked, select the token and copy it manually.
- **Use:** the caller sends it in the `X-Internal-Token` header on every call except `/health`.
- **List:** the table shows **Project**, **Created**, **Last used**, and **Requests** (authenticated calls so far). Last used is **never** until a token
  authenticates a request, then a relative time; hover a timestamp for its exact local time. The list refreshes
  every 10 seconds. Token values are not shown again.
- **Revoke:** press the red-outlined **Revoke** pill and confirm in the in-app dialog, which names the project.
  **Cancel**, Escape, or a click on the backdrop closes the dialog. The caller gets `401 Invalid token` from then on. Lost
  token = revoke it and create a new one.

Last-used timestamps are Unix seconds or null. They are persisted at most once per minute per token, so they
are approximate. Request counts are added to the file with that same write; uses between writes are
counted in memory, so a crash can lose up to a minute of them. Old token files without the field still load and show **never** until next use. A companion
`tokens.json.lock` file serializes updates across replicas without adding dependencies.

Tokens live in `tokens.json` (`SERVICE_TOKENS_FILE`). In Docker that is the `tts_paradee_model_data` volume at `/data`, so they
survive `docker compose down` and rebuilds. Without Docker it is `tokens.json` in the folder the service starts from.
Tokens are enforced only while `SERVICE_ADMIN_PASSWORD` is set.

## CPU threads

Use the synchronized slider or number field, then **Apply**. Both accept 1 to 256. Apply is disabled and
shows a busy indicator during saving; a short **Saved** confirmation follows. The page states:
“Saved to settings.json. Other replicas apply it within a few seconds.”
- Sets `num_thread` used by onnxruntime for every generation: how many CPU threads it uses. Start near the
  number of physical cores the host can spare.
- Range 1 to 256. The field shows the value in use now (`num_thread` in `/health`; empty = onnxruntime decides).
- The change applies at once to the replica that answers the click and is saved to `settings.json` next to
  `tokens.json`. Other replicas pick it up within a few seconds; no restart is needed.
- The saved value beats `SERVICE_NUM_THREAD` from `.env`. Remove `settings.json` from the `/data` volume to go back
  to the `.env` value.

## Replicas and parallel requests (read-only)

The tiles show replicas and parallel generations from `SERVICE_REPLICAS`; each replica runs one generation at a time.
The muted line says **Replicas: N. Change SERVICE_REPLICAS in .env, then rebuild.** The
page cannot change them. To change, set them in `.env`, then:
```bash
docker compose up -d --build
docker compose restart tts_paradee_model_lb
```
`unknown` means the variable is not set, which is normal without Docker. Without Docker there is one process.

## Problems

| Symptom | Fix |
|---|---|
| Page does not load | Service not running, wrong port, or blocked by a firewall. Test `/health`. |
| `Wrong admin password` | Password differs from `SERVICE_ADMIN_PASSWORD` in the running container. Recreate it after editing `.env`. |
| `Admin password required (set SERVICE_ADMIN_PASSWORD)` | The password is empty. Set it and restart. |
| Token lost | Revoke it and create a new one. |
| `/health` shows `ready: false` | The model is not downloaded yet, is loading, or loading failed (see the container logs). |
| Never expose port 29321 to the internet | It is a LAN tool; use a firewall or VPN for remote access. |

## Limiting a token to voices

When you create a token you can list the voices it may use (comma separated, the field is labelled "projects"; empty = all). A request whose `voice` is not on the list is refused with `403`; a request without `voice` uses the default voice, which must be on the list. The list cannot be edited later: revoke the token and create a new one.

## Rate limit, expiry, edits, usage and metrics

- **Requests per minute** (optional, when creating or editing a token): over the limit a call gets `429` with `Retry-After`. The count is per replica, so with several replicas behind the balancer the real limit can be up to that many times higher.
- **Expires in (days)** (optional, only when creating): after that the token is refused like a revoked one, and the row shows "Expired". Create a new token to continue.
- **Edit** changes the allowed voices and the rate limit without revoking the token.
- **Usage:** each row shows errors (answers with status 500 or above) and the average time per call. The counters are written at most once a minute.
- **Metrics:** `GET /metrics` (send the admin password in `X-Internal-Token`) returns the same numbers as Prometheus text, one series per token. It reads the shared token file, so any replica answers with the same data. `ttsparadee_request_seconds` is a histogram of how long each `/v1` call took on the replica that answers (to the first byte for a streamed reply); it starts from 0 when the replica restarts.
- The **Project models** card shows nothing here: this service has no per-project models.

## Settings

The **Settings** card changes options without editing `.env` or restarting: update-check interval, caches and limits, and similar. Each row shows where its value comes from (saved, environment or default). A value saved here beats the environment variable; **Reset** removes it. Every replica reads the shared settings file, so a change applies within a few seconds. Rows marked "applies after a restart" are read once at start. The admin password, ports, replica count and other Docker settings stay in `.env` or the compose file, because the service cannot change its own container.

## Updating the model files

The **Updates** card compares the downloaded revision of the Hugging Face repository with its newest one. **Download
new weights** fetches them into the models volume; every replica drops its old copy within a few seconds and loads the
new one on its next request. Updating the service itself: `docker compose up -d --build`, then
`docker compose restart tts_paradee_model_lb`.

## Connect to this service

The **Connect to this service** card lists the addresses other services can use: the address you opened the page with, the same PC (`127.0.0.1`), another Docker container on the same network (the `_lb` name from the compose file), and a Docker container calling this PC (`host.docker.internal`), then every endpoint with a **Copy** button. Callers send a token from the tokens card in `X-Internal-Token`.

## Try it: send a real request

1. Sign in to `/admin/` and find **Try it**.
2. Paste a service token into the token field. This is the public client token, not the admin password. For a model service, create a token in the existing tokens card if needed; token values cannot be recovered from their stored hashes.
3. Choose an operation, complete its labeled fields and send it. Requests go to this service's public route, with the same token scope, rate/admission limits and cache as a separate client.
4. Read the HTTP status, elapsed time and response. NDJSON/SSE text appears while it arrives; JSON is formatted. HTTP errors, including 401, 422, 429 and 503, keep their response body visible. Use **Cancel** to abort an active request.
5. Copy the curl equivalent to repeat the request elsewhere. The token is always a placeholder; fill it locally. For an upload, provide the indicated input file. Multi-step operations show their follow-up request too.

Authentication choice is **(b), a pasted public token held only in memory**. The playground does not persist it in browser storage, put it in a URL, log it or include it in curl. Sign out or reset the panel to clear it. The existing admin sign-in is separate. A playground run consumes normal service quotas and can generate retained files where the public API normally does so; cancelling the browser fetch does not promise to undo work already accepted by a backend.

The browser caps uploads at **10 MiB**, displayed text at **256 KiB**, and binary audio/image responses at **32 MiB**. Server limits still apply and may be lower. Media uses bounded Blob URLs, revoked when replaced or cleared. Text is rendered as text, never executed as HTML. Audio players become available after the bounded audio download finishes. No new environment setting is needed.

### This service's request

Speak sends text, an installed voice and speed to `POST /v1/speak`, then displays WAV audio with playback controls and a download. Load voices with the public token: `GET /v1/voices` is filtered by its allowed projects list, which represents voice names for TTS. The installed Paradee version determines the available voices. An optional streaming request receives WAV chunks; the player is created when the bounded audio response finishes.

## Updates and first download

The service starts without weights: health and admin remain available, `ready` is false, and speech calls return 503. Sign in, open **Updates**, then press **Download**. Only this action fetches model files; warm-up and requests use the local cache. Updates checks run at startup and every `update_check_hours` when enabled, or with **Check now**; checks never download. The card shows the downloaded and online revisions and marks a newer version when they differ. Download replaces cached files; every replica drops its old model and loads the new files on its next request.

The equivalent admin request is:

```bash
curl -X POST http://127.0.0.1:29321/admin/updates/weights -H "X-Internal-Token: <ADMIN_PASSWORD>"
```
