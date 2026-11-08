# TTS_Paradee_Model

Paradee text to speech, an 8M-parameter model distilled from Kokoro-82M (Apache 2.0), served over HTTP so projects send text instead of loading a model themselves. It has the same admin page,
tokens, rate limits, metrics, settings and update checks as `DSC_Laya_Model`, `CNV_MiniCPM52B_Model` and `CNV_BitNet2B_Model`.
`TTS_Piper_Model` is the same service with the other engine, so the two can be compared on the same sentences
(`tools/bench.py`).

The engine runs in this process on the CPU with onnxruntime. No GPU, no Ollama.

## API

| Call | Purpose |
|---|---|
| `POST /v1/speak` | `{"text", "voice", "speed", "stream"}` returns a WAV file (`audio/wav`, 16-bit mono). Headers: `X-Audio-Seconds`, `X-Duration-Ms`, `X-Voice`, `X-Cache` (`hit` when the kept answer was sent). |
| `GET /v1/voices` | `{"voices", "default"}`; with a token limited to voices, only those. |
| `GET /health` | `{"status": "ok", "model", "ready", "num_thread", "voices"}`; `ready` is true once the model is loaded. No token needed. |

`text` is 1 to 5000 characters; `speed` 0.5 to 2 (default 1; above 1 is faster). Speed is passed to the model's `speed` input. Other fields are refused
with 422, so a caller asking for `mp3` finds out instead of getting WAV silently.

**Streaming.** With `"stream": true` the WAV is sent sentence by sentence while the rest is still being made, so the
first audio arrives after the first sentence instead of after all of the text. The header cannot know the length yet:
its size fields are `0xFFFFFFFF` (read "until the connection ends"), and there is no `X-Audio-Seconds`. Players and
`ffmpeg` read it; Python's `wave` module does not. Errors that can be known early (unknown voice, no speech in the
text, model not loaded) are still normal HTTP errors; one after the first sentence cuts the stream off. Without
`stream` the reply is the complete file with its length.

**Cache.** The *Answers kept per replica* setting (`SERVICE_CACHE_SIZE`, default 64, 0 = off) keeps the last answers for
the same text, voice and speed; a repeat is sent again without speaking (`X-Cache: hit`, a few milliseconds). An
answer over 1 MB (about 20 seconds of audio) is not kept, so 64 entries use at most 64 MB per replica. The cache is
per replica, so with N replicas the hit rate is about 1/N, and it is emptied when the voices are reloaded
(new weights or a changed thread count). A finished stream fills it too. `tools/bench.py` varies the speed a little
so it measures speaking, not the cache.

**Voices.** Version 1.2 speaks `af_heart` (default), `bf_isabella`, `am_puck` and `bm_fable` from one 8.5 MB file; the
British voices use British phonemes. Versions 1.1 and 1.0 (the *Paradee version* setting) speak `af_heart` only.

A token's *projects* list on the admin page limits the voices it may use (the page calls them projects; here they
are voice names). A token without a list may use every served voice.

## Admin page

`/admin/` (password `SERVICE_ADMIN_PASSWORD`): tokens with voice scope, rate limit and expiry, usage numbers, CPU
threads, the Settings card (update check hours, in-flight cap, answers kept, Paradee version, load at start), and the Updates card (**Check now** and **Download new weights**).
New files are downloaded once into the shared models volume; the other replicas notice within a few seconds and
load them on their next request. `/metrics` (admin password) is Prometheus text per token. `ttsparadee_request_seconds` is a histogram of how long each `/v1` call took on the replica that answers (to the first byte for a streamed reply); it starts from 0 when the replica restarts.

## Run

Startup, warm-up and inference never download weights. With no downloaded weights, `/health` answers `ready: false` and speech calls return 503. Open `/admin/` → **Updates** → **Download** before the first call; see the [admin guide](docs/guide-web-ui.md#updates-and-first-download) for the curl alternative.

```bash
docker compose up -d --build        # http://<host>:29321
python tools/bench.py http://127.0.0.1:29321 --save out
```

Without Docker: `run.bat` (uv, listens on `127.0.0.1:29321`). Startup loads downloaded weights only; open `/admin/`, then **Updates → Download** before the first call. Memory:
about 650 MB per replica, measured with both phonemizers loaded, so `SERVICE_REPLICAS` multiplies it. See `deployment.md`.

## Tests

```bash
uv sync
uv run pytest
```

## Limits worth knowing

- One generation at a time per replica; extra calls wait, and more than the in-flight cap get 429.
- The rate limit is per replica.
- Output is WAV only (no ffmpeg in the image). Convert on the caller's side for MP3.
- There is no streaming: the whole clip is generated, then sent.
- The update check follows the release tag the `paradee` package pins (`v1.2`), so it reports a change only when the author moves that tag.

## Try it from the admin page

Open `/admin/` and use **Try it** to call the public API with a service token held only in memory. Runs use normal token scopes, limits and caches; the panel shows HTTP status, latency, responses, cancellation and a curl equivalent with a token placeholder. See [the web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request).

Speak sends text, an installed voice and speed to `POST /v1/speak`, then displays WAV audio with playback controls and a download. Load voices with the public token: `GET /v1/voices` is filtered by its allowed projects list, which represents voice names for TTS. The installed Paradee version determines the available voices. An optional streaming request receives WAV chunks; the player is created when the bounded audio response finishes.

## Documentation

- [Purpose: input, output and what it is for](docs/purpose.md)
- [Deployment](deployment.md)
- [Changelog](CHANGELOG.md)
- [Docker on another machine](docs/guide-docker-deployment-device.md)
- [Docker on this PC](docs/guide-docker-same-device.md)
- [Run without Docker](docs/guide-no-docker-same-device.md)
- [Test it from the command line](docs/guide-cli.md)
