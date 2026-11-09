"""Paradee text-to-speech run in this process on the CPU.

Paradee is an 8M-parameter model distilled from Kokoro-82M (Apache 2.0): one 8.5 MB ONNX file speaks four voices
(af_heart, bf_isabella, am_puck, bm_fable). The `paradee` package turns text into phonemes (misaki) and runs the
graph with onnxruntime. One generation runs at a time per replica; scale with replicas.

shortcut: the update check follows the release tag the package pins (v1.2), so it reports a change only when the
author moves that tag. Another version is the `version` setting."""

from __future__ import annotations

import asyncio
import io
import os
import re
import threading
import uuid
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

DEFAULT_VERSION = "1.2"
MODEL_FILE = "onnx/paradee_int8.onnx"
SAMPLE_RATE = 24000


class EngineError(RuntimeError):
    """The text could not be spoken. `status` is the HTTP status the service maps it to."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def repo_of(version: str) -> str:
    from paradee.tts import REPOS

    if version not in REPOS:
        raise ValueError(f"version must be one of {', '.join(REPOS)}, not {version!r}")
    return REPOS[version]


class Voices:
    """A loaded Paradee model that turns text into a WAV file."""

    rate = SAMPLE_RATE

    def __init__(self, tts: Any) -> None:
        self._tts = tts
        self.names: list[str] = list(tts.voices)

    def pieces(self, text: str, voice: str, speed: float) -> Iterator[bytes]:
        """16-bit mono audio, one piece per sentence, made as the caller asks for the next one."""
        import numpy

        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            audio = self._tts(sentence, speed=speed, voice=voice) if sentence else []
            if len(audio):
                yield (numpy.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    def speak(self, text: str, voice: str, speed: float) -> tuple[bytes, float]:
        import numpy

        audio = self._tts(text, speed=speed, voice=voice)
        pcm = (numpy.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm.tobytes())
        return buf.getvalue(), len(pcm) / SAMPLE_RATE


def load(version: str, threads: int | None) -> Voices:
    from huggingface_hub import hf_hub_download
    from paradee import Paradee

    repo, revision = repo_of(version), f"v{version}"
    model_path = hf_hub_download(repo, MODEL_FILE, revision=revision, local_files_only=True)
    config_path = hf_hub_download(repo, "config.json", revision=revision, local_files_only=True)
    tts = Paradee(version=version, threads=threads or 1, model_path=model_path, config_path=config_path)
    for text, voice in (("Warm up.", tts.voices[0]), ("Warm up.", tts.voices[-1])):
        tts(text, voice=voice)  # builds the American and British phonemizers now, not on the first request
    return Voices(tts)


def _remote_sha(version: str) -> str:
    from huggingface_hub import HfApi

    return HfApi().model_info(repo_of(version), revision=f"v{version}", token=False).sha


def _cached_sha(version: str) -> str | None:
    from huggingface_hub import hf_hub_download

    try:
        return Path(hf_hub_download(repo_of(version), MODEL_FILE, revision=f"v{version}", local_files_only=True)).parent.parent.name
    except OSError:
        return None


def _download(version: str) -> None:
    from huggingface_hub import hf_hub_download

    from progress import hub_blobs, hub_total, tracking

    repo, names = repo_of(version), [MODEL_FILE, "config.json"]
    with tracking(repo, hub_total(repo, f"v{version}", names), hub_blobs(repo)):
        for name in names:
            hf_hub_download(repo, name, revision=f"v{version}", token=False)


def default_marker() -> Path:
    """File in the shared models volume whose content changes when one replica downloads new weights."""
    configured = os.environ.get("SERVICE_WEIGHTS_MARKER")
    return Path(configured) if configured else Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser() / ".ttsparadee-weights-version"


class ParadeeEngine:
    """The `loader`, `remote_sha`, `cached_sha` and `download` callables are injectable for tests."""

    def __init__(self, version: str = DEFAULT_VERSION, *, timeout: float = 120.0, marker: Path | None = None,
                 loader: Callable[[str, int | None], Voices] = load,
                 remote_sha: Callable[[str], str] = _remote_sha, cached_sha: Callable[[str], str | None] = _cached_sha,
                 download: Callable[[str], None] = _download) -> None:
        self.version = version
        self.model = repo_of(version)  # also rejects an unknown version
        self._threads: int | None = None
        self._timeout = timeout
        self._marker = marker
        self._seen = self._read_marker()
        self._loader = loader
        self._remote_sha_of, self._cached_sha_of, self._download_of = remote_sha, cached_sha, download
        self._loaded: Voices | None = None
        self._load_lock = threading.Lock()
        self._gate = asyncio.Semaphore(1)
        self.generation = 0  # part of the answer cache key; changes whenever the loaded model is dropped

    @property
    def voices(self) -> list[str]:
        from paradee.tts import VOICES

        return list(self._loaded.names if self._loaded else VOICES[self.version])

    @property
    def default_voice(self) -> str:
        return self.voices[0]

    @property
    def num_thread(self) -> int | None:
        return self._threads

    @num_thread.setter
    def num_thread(self, count: int) -> None:
        if count != self._threads:
            self._threads = count
            self._forget()  # the ONNX session keeps the thread count it was built with

    @property
    def ready(self) -> bool:
        return self._loaded is not None

    async def close(self) -> None:
        self._loaded = None

    async def warm(self) -> None:
        """Load the model now so the first request does not pay for it."""
        await asyncio.to_thread(self._model)

    def _model(self) -> Voices:
        with self._load_lock:
            if self._loaded is None:
                try:
                    self._loaded = self._loader(self.version, self._threads)
                except OSError:
                    raise EngineError("Model weights are not downloaded. Ask an admin to press Download on the Updates card.", 503) from None
            return self._loaded

    def _name(self, voice: str | None) -> str:
        name = voice or self.default_voice
        if name not in self.voices:
            raise EngineError(f"Voice {name!r} is not served here; the voices are {', '.join(self.voices)}", 404)
        return name

    async def synthesize(self, text: str, voice: str | None, speed: float) -> dict[str, Any]:
        name = self._name(voice)
        async with self._gate:
            wav, seconds = await asyncio.to_thread(self._speak, text, name, speed)
        return {"wav": wav, "seconds": seconds, "voice": name}

    async def stream(self, text: str, voice: str | None, speed: float) -> AsyncIterator[tuple[int, bytes]]:
        """The audio as it is made: (sample rate, 16-bit mono PCM) per sentence. Holds the generation slot until
        the last piece, or until the caller closes the iterator."""
        name = self._name(voice)
        async with self._gate:
            loaded = await asyncio.to_thread(self._model)
            pieces = loaded.pieces(text, name, speed)
            made = False
            while (piece := await asyncio.to_thread(next, pieces, None)) is not None:
                made = True
                yield loaded.rate, piece
        if not made:
            raise EngineError("No speech could be made from that text.", 422)

    def _speak(self, text: str, name: str, speed: float) -> tuple[bytes, float]:
        return self._model().speak(text, name, speed)

    # Updates: the Hub revision of the model files, shared by all replicas through one models volume.

    async def cached_sha(self) -> str | None:
        return await asyncio.to_thread(self._cached_sha_of, self.version)

    async def remote_sha(self) -> str:
        return await asyncio.to_thread(self._remote_sha_of, self.version)

    async def pull(self) -> None:
        await asyncio.to_thread(self._download_of, self.version)

    async def unload(self) -> None:
        """Forget the loaded model here and tell the other replicas to do the same; the next call loads the new files."""
        if self._marker is not None:
            self._marker.parent.mkdir(parents=True, exist_ok=True)
            self._seen = uuid.uuid4().hex
            self._marker.write_text(self._seen, "utf-8")
        self._forget()

    def sync(self) -> None:
        """Called every few seconds: drop the loaded model when another replica downloaded new files."""
        current = self._read_marker()
        if current != self._seen:
            self._seen = current
            self._forget()

    def _forget(self) -> None:
        with self._load_lock:
            self._loaded = None
            self.generation += 1  # kept answers are from the old files or threads

    def _read_marker(self) -> str | None:
        try:
            return self._marker.read_text("utf-8") if self._marker else None
        except OSError:
            return None
