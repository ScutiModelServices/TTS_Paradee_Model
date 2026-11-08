# Deploying TTS_Paradee_Model

TTS_Paradee_Model is one HTTP service (port 29321). It runs as `tts_paradee_model` replicas behind an nginx load balancer,
`tts_paradee_model_lb`. Callers use `http://<host>:29321`; they never reach a replica directly.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `run.bat` (Deploy) | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Before the first call, download weights through Updates; startup does not download them. Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
