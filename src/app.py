"""HTTP face of Paradee text to speech, run in this process on the CPU.

POST /v1/speak {"text", "voice", "speed"} returns a WAV file (audio/wav); X-Audio-Seconds, X-Duration-Ms and X-Voice
describe it. GET /v1/voices lists the voices. GET /health reports whether the default voice is loaded."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from pathlib import Path
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from audio import AudioCache, wav_bytes, wav_header
from engine import DEFAULT_VERSION, EngineError, ParadeeEngine, default_marker
from options import SETTINGS
import settings
from tokens import LatencyHistogram, TokenStore
from updates import Updates

_log = logging.getLogger(__name__)

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"  # a voice name; a token's "projects" list limits the voices it may use


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = Field(None, pattern=NAME_PATTERN)
    speed: float = Field(1.0, ge=0.5, le=2.0)  # above 1 speaks faster
    stream: bool = False  # true: the WAV is sent sentence by sentence, so audio starts sooner; its header has no length


class SettingsUpdate(BaseModel):
    values: dict[str, Any]  # None resets an option to its environment variable or default


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    projects: list[Annotated[str, Field(pattern=NAME_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)  # calls per minute
    expires_days: int | None = Field(None, ge=1, le=3650)


class TokenScope(BaseModel):
    projects: list[Annotated[str, Field(pattern=NAME_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)


class Threads(BaseModel):
    num_thread: int = Field(ge=1, le=256)


def create_app(client: ParadeeEngine | None = None, admin_password: str | None = None,
               tokens: TokenStore | None = None, warm: bool = False,
               updates: Updates | None = None, check_hours: float = 0, max_inflight: int | None = None,
               cache_size: int | None = None, config: settings.Config | None = None) -> FastAPI:
    """`admin_password` (SERVICE_ADMIN_PASSWORD) unlocks /admin/, where each caller gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the tokens (SERVICE_TOKENS_FILE). `cache_size` > 0 keeps that
    many answers per replica (SERVICE_CACHE_SIZE). `config` (the admin page's settings) replaces `warm`,
    `check_hours`, `max_inflight` and `cache_size` and keeps them live; without it they stay fixed."""
    tokens = tokens or TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    client = client or ParadeeEngine(config.get("version") if config else os.environ.get("SERVICE_VERSION", DEFAULT_VERSION),
                                   marker=default_marker())
    inflight = 0
    cache = AudioCache()

    def cache_limit() -> int:
        """Answers this replica keeps; 0 = off."""
        return config.get("cache_size") if config else (
            cache_size if cache_size is not None else int(os.environ.get("SERVICE_CACHE_SIZE") or 0))

    def inflight_limit() -> int:
        """Calls this replica lets wait for the model at once; more get 429. 0 = 4."""
        value = config.get("max_inflight") if config else (
            max_inflight if max_inflight is not None else int(os.environ.get("SERVICE_MAX_INFLIGHT") or 0))
        return value or 4

    updates = updates or Updates(client.model, cached_sha=lambda: client.cached_sha(), remote_sha=lambda: client.remote_sha(),
                                 download=lambda: client.pull(), unload=lambda: client.unload())

    async def check_forever() -> None:
        """Checks every `update_check_hours` (0 = off). The value is read again every second, so the admin page can change it."""
        last = None
        while True:
            wait = (config.get("update_check_hours") if config else check_hours) * 3600
            now = time.monotonic()
            if wait > 0 and (last is None or now - last >= wait):
                last = now
                try:
                    status = await updates.check()
                    if status["error"]:
                        _log.warning("Update check failed: %s", status["error"])
                except Exception:
                    _log.exception("Update check failed")
            await asyncio.sleep(min(wait, 1.0) if wait > 0 else 1.0)

    async def sync_forever() -> None:
        """Applies what another replica saved (CPU threads) and drops models it replaced with new files."""
        while True:
            saved = settings.load(tokens.settings_path)
            if saved.get("num_thread") and saved["num_thread"] != client.num_thread:
                client.num_thread = int(saved["num_thread"])
            client.sync()
            # A dropped model (download, threads change, sync) leaves /health "Starting" until a request loads it: reload once per drop.
            if config.get("warm") and not client.ready and client.generation != warmed:
                await warm_up()
            await asyncio.sleep(1)

    warmed = 0  # generation of the last warm-up attempt

    async def warm_up() -> None:
        nonlocal warmed
        warmed = client.generation
        try:
            await client.warm()
        except Exception:
            _log.exception("Model warm-up failed; the first request will try again")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background: list[asyncio.Task] = []
        try:
            if config.get("warm") if config else warm:
                # In the background: cached weights load while /health remains available.
                background.append(asyncio.create_task(warm_up()))
            if config or check_hours > 0:
                background.append(asyncio.create_task(check_forever()))
            if config:
                background.append(asyncio.create_task(sync_forever()))
            yield
        finally:
            for task in background:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()

    def authorize(request: Request, x_internal_token: str = Header("")) -> list[str] | None:
        """The voices the token is limited to, or None for all. Unset admin password = no auth."""
        if not admin_password:
            return None
        row = tokens.lookup(x_internal_token)
        if row is None:
            raise HTTPException(401, "Invalid token")
        wait = tokens.throttle(row)
        if wait:
            raise HTTPException(429, "Too many requests for this token", headers={"Retry-After": str(wait)})
        request.state.token_id = row["id"]
        return row.get("projects")

    def check_scope(allowed: list[str] | None, voice: str) -> None:
        if allowed is not None and voice not in allowed:
            raise HTTPException(403, f"This token may not use voice {voice!r}")

    def admin(x_internal_token: str = Header("")) -> None:
        if not admin_password or not hmac.compare_digest(x_internal_token, admin_password):
            raise HTTPException(401, "Admin password required (set SERVICE_ADMIN_PASSWORD)")

    app = FastAPI(title="Paradee text-to-speech service", lifespan=lifespan)

    async def slot():
        nonlocal inflight
        limit = inflight_limit()
        if limit and inflight >= limit:
            raise HTTPException(429, "Paradee is busy: too many requests waiting", headers={"Retry-After": "5"})
        inflight += 1
        try:
            yield
        finally:
            inflight -= 1

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "model": client.model, "ready": client.ready,
                "num_thread": client.num_thread, "voices": client.voices}

    @app.get("/v1/voices")
    async def voices(allowed: list[str] | None = Depends(authorize)) -> dict[str, Any]:
        names = [name for name in client.voices if allowed is None or name in allowed]
        return {"voices": names, "default": client.default_voice}

    @app.post("/v1/speak")
    async def speak(body: SpeakRequest, allowed: list[str] | None = Depends(authorize), _slot: None = Depends(slot)) -> Response:
        name = body.voice or client.default_voice
        check_scope(allowed, name)
        started = time.perf_counter()
        size = cache_limit()
        key = (client.generation, name, body.speed, body.text)
        audio = cache.get(key, size)
        if audio is not None:
            return wav_reply(audio, started, "hit")
        try:
            if body.stream:
                return await stream_reply(body, name, key, size)
            audio = await client.synthesize(body.text, body.voice, body.speed)
        except EngineError as error:
            raise HTTPException(error.status or 500, str(error)) from None
        except Exception:
            _log.exception("Paradee could not speak")
            raise HTTPException(500, "Paradee could not speak") from None
        cache.put(key, audio, size)
        return wav_reply(audio, started, "miss")

    def wav_reply(audio: dict[str, Any], started: float, cached: str) -> Response:
        return Response(audio["wav"], media_type="audio/wav", headers={
            "X-Audio-Seconds": f"{audio['seconds']:.3f}", "X-Voice": audio["voice"], "X-Cache": cached,
            "X-Duration-Ms": str(round((time.perf_counter() - started) * 1000))})

    async def stream_reply(body: SpeakRequest, name: str, key: tuple, size: int) -> StreamingResponse:
        """Starts the audio before the text is finished. The first sentence is made here, so an unknown voice or a
        failed model still gives a proper HTTP error; once the stream has begun an error can only cut it off."""
        pieces = client.stream(body.text, name, body.speed)
        try:
            rate, first = await anext(pieces)
        except BaseException:
            await pieces.aclose()
            raise

        async def chunks():
            kept: bytearray | None = bytearray(first) if size else None
            try:
                yield wav_header(rate)
                yield first
                async for _, piece in pieces:
                    if kept is not None:
                        kept += piece
                        if len(kept) > cache.MAX_BYTES:
                            kept = None  # too long to keep; stop collecting it
                    yield piece
            finally:
                await pieces.aclose()
            if kept is not None:  # only a stream that reached its end
                cache.put(key, {"wav": wav_bytes(bytes(kept), rate), "seconds": len(kept) / 2 / rate, "voice": name}, size)

        return StreamingResponse(chunks(), media_type="audio/wav", headers={"X-Voice": name, "X-Cache": "miss"})

    @app.put("/admin/threads", dependencies=[Depends(admin)])
    def set_threads(body: Threads) -> dict[str, Any]:
        """Applies to this replica now and is saved; the other replicas apply it within a few seconds. It beats SERVICE_NUM_THREAD."""
        client.num_thread = body.num_thread
        settings.save(tokens.settings_path, num_thread=body.num_thread)
        return {"num_thread": client.num_thread}

    @app.get("/admin/workers", dependencies=[Depends(admin)])
    def workers() -> dict[str, Any]:
        """Read-only: the replica count is a Docker Compose setting the service cannot change."""
        def number(name: str) -> int | None:
            value = os.environ.get(name, "").strip()
            return int(value) if value.isdigit() else None

        return {"replicas": number("SERVICE_REPLICAS"), "parallel": 1}

    @app.get("/admin/updates", dependencies=[Depends(admin)])
    async def update_status() -> dict[str, Any]:
        """Return the last check without I/O; zero check hours disables only the timer."""
        return {"enabled": True, **updates.status()}

    @app.post("/admin/updates/check", dependencies=[Depends(admin)])
    async def check_updates() -> dict[str, Any]:
        return {"enabled": True, **await updates.check()}

    @app.post("/admin/updates/weights", dependencies=[Depends(admin)])
    async def update_weights() -> dict[str, Any]:
        try:
            return {"enabled": True, **await updates.update_weights()}
        except Exception as error:
            _log.exception("Weight update failed")
            raise HTTPException(502, f"Weight update failed: {error}") from None

    @app.get("/admin/tokens", dependencies=[Depends(admin)])
    def list_tokens() -> dict[str, Any]:
        return {"tokens": tokens.list()}

    @app.post("/admin/tokens", dependencies=[Depends(admin)])
    def create_token(body: NewToken) -> dict[str, Any]:
        return tokens.create(body.name.strip(), body.projects, body.rate, body.expires_days)

    @app.get("/admin/settings", dependencies=[Depends(admin)])
    def get_settings() -> dict[str, Any]:
        return {"settings": config.describe() if config else []}

    @app.put("/admin/settings", dependencies=[Depends(admin)])
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        """Saves options; null resets one. Every replica reads the shared file, so a change applies everywhere within
        a second, except options marked `restart`."""
        if config is None:
            raise HTTPException(409, "Settings are fixed in this setup")
        try:
            config.set(body.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"settings": config.describe()}

    @app.put("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def update_token(token_id: str, body: TokenScope) -> dict[str, bool]:
        if not tokens.update(token_id, body.projects, body.rate):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    latency = LatencyHistogram()

    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            latency.observe(time.perf_counter() - started)
        token_id = getattr(request.state, "token_id", None)
        if token_id:
            await run_in_threadpool(tokens.record, token_id, (time.perf_counter() - started) * 1000, response.status_code >= 500)
        return response

    @app.get("/metrics", dependencies=[Depends(admin)])
    def metrics() -> PlainTextResponse:
        """Prometheus text per token, from the shared token file (up to a minute behind). Send the admin password."""
        return PlainTextResponse(tokens.metrics("ttsparadee") + latency.render("ttsparadee"))

    @app.delete("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def revoke_token(token_id: str) -> dict[str, bool]:
        if not tokens.revoke(token_id):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    web = Path(os.environ.get("SERVICE_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
    if web.is_dir():
        app.mount("/admin", StaticFiles(directory=web, html=True))

    return app


def main() -> None:
    import sys

    import uvicorn

    if "--dotenv" in sys.argv[1:]:  # run.bat asks for it; a plain `python src/app.py` and Docker never read .env
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.is_file():
            names = settings.load_env_file(env_file)
            print(f".env: loaded {', '.join(names) or 'no variables'} (names only; a real environment variable wins)", flush=True)
        else:
            print(".env: no file in the service folder", flush=True)
    settings.apply_models_dir()  # SERVICE_MODELS_DIR -> HF_HOME / OLLAMA_MODELS

    store = TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    config = settings.Config(store.settings_path, SETTINGS)
    client = ParadeeEngine(config.get("version"), marker=default_marker())
    saved = settings.load(store.settings_path)
    threads = saved.get("num_thread") or os.environ.get("SERVICE_NUM_THREAD", "").strip()
    if threads:
        client.num_thread = int(threads)
    app = create_app(client=client, admin_password=os.environ.get("SERVICE_ADMIN_PASSWORD") or None,
                     tokens=store, config=config)
    host, port = os.environ.get("SERVICE_HOST", "127.0.0.1"), int(os.environ.get("SERVICE_PORT", "29321"))

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)  # after uvicorn's own "running on" line
            shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            print(f"INFO:     Admin page: http://{shown}:{port}/admin/", flush=True)

    Server(uvicorn.Config(app, host=host, port=port)).run()


if __name__ == "__main__":
    main()
