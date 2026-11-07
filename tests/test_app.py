import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import settings
from app import create_app
from conftest import make_engine
from engine import EngineError
from options import SETTINGS
from tokens import TokenStore

BODY = {"text": "Hello there."}
VOICES = ["af_heart", "bf_isabella", "am_puck", "bm_fable"]


def client(engine=None, admin_password=None, **options):
    return TestClient(create_app(engine or make_engine(), admin_password, **options))


def test_speak_returns_a_wav_with_its_length_and_voice():
    engine = make_engine()
    reply = client(engine).post("/v1/speak", json={**BODY, "speed": 1.2})
    assert reply.status_code == 200 and reply.headers["content-type"] == "audio/wav" and reply.content[:4] == b"RIFF"
    assert reply.headers["X-Audio-Seconds"] == "0.500" and reply.headers["X-Voice"] == "af_heart"
    assert int(reply.headers["X-Duration-Ms"]) >= 0
    assert engine.made[0].calls == [{"text": "Hello there.", "voice": "af_heart", "speed": 1.2}]


def test_the_same_request_is_answered_from_the_cache():
    engine = make_engine()
    c = client(engine, cache_size=4)
    first, second = c.post("/v1/speak", json=BODY), c.post("/v1/speak", json=BODY)
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("miss", "hit")
    assert second.content == first.content and second.headers["X-Voice"] == "af_heart"
    assert len(engine.made[0].calls) == 1
    c.post("/v1/speak", json={**BODY, "speed": 1.1})  # another speed is another answer
    c.post("/v1/speak", json={**BODY, "voice": "bf_isabella"})
    assert len(engine.made[0].calls) == 3


def test_the_cache_keeps_only_the_newest_and_can_be_off():
    c = client(make_engine(), cache_size=1)
    for text in ("one", "two", "one"):
        assert c.post("/v1/speak", json={"text": text}).headers["X-Cache"] == "miss"
    off = client(make_engine())  # no cache_size: off
    assert [off.post("/v1/speak", json=BODY).headers["X-Cache"] for _ in range(2)] == ["miss", "miss"]


def test_the_cache_is_not_used_after_the_model_was_reloaded():
    engine = make_engine()
    c = client(engine, cache_size=4)
    c.post("/v1/speak", json=BODY)
    asyncio.run(engine.unload())  # new weights
    assert c.post("/v1/speak", json=BODY).headers["X-Cache"] == "miss"


def test_cache_size_is_a_setting_that_applies_live(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    c = client(make_engine(), admin_password="admin", tokens=store, config=config)
    admin = {"X-Internal-Token": "admin"}
    use = {"X-Internal-Token": c.post("/admin/tokens", json={"name": "t"}, headers=admin).json()["token"]}
    assert [c.post("/v1/speak", json=BODY, headers=use).headers["X-Cache"] for _ in range(2)] == ["miss", "hit"]
    assert c.put("/admin/settings", json={"values": {"cache_size": 0}}, headers=admin).status_code == 200
    assert c.post("/v1/speak", json=BODY, headers=use).headers["X-Cache"] == "miss"


def test_stream_sends_a_wav_without_a_length_and_the_audio_sentence_by_sentence():
    reply = client().post("/v1/speak", json={"text": "One. Two. Three.", "stream": True})
    assert reply.status_code == 200 and reply.headers["content-type"] == "audio/wav" and reply.headers["X-Voice"] == "af_heart"
    assert reply.content[:4] == b"RIFF" and int.from_bytes(reply.content[24:28], "little") == 24000
    assert len(reply.content[44:]) == 3 * 4800
    assert reply.content[4:8] == b"\xff\xff\xff\xff" and reply.content[40:44] == b"\xff\xff\xff\xff"  # length unknown


def test_stream_errors_are_still_http_errors():
    c = client()
    assert c.post("/v1/speak", json={**BODY, "voice": "zz_nobody", "stream": True}).status_code == 404
    assert c.post("/v1/speak", json={"text": "...", "stream": True}).status_code == 422


def test_a_finished_stream_fills_the_cache_with_a_complete_wav():
    engine = make_engine()
    c = client(engine, cache_size=4)
    text = {"text": "One. Two.", "stream": True}
    streamed = c.post("/v1/speak", json=text)
    kept = c.post("/v1/speak", json=text)
    assert (streamed.headers["X-Cache"], kept.headers["X-Cache"]) == ("miss", "hit")
    assert int.from_bytes(kept.content[40:44], "little") == 2 * 4800 and kept.content[44:] == streamed.content[44:]
    assert float(kept.headers["X-Audio-Seconds"]) == 0.2
    assert len(engine.made[0].calls) == 1


def test_request_is_validated():
    c = client()
    assert c.post("/v1/speak", json={"text": ""}).status_code == 422
    assert c.post("/v1/speak", json={"text": "x" * 5001}).status_code == 422
    assert c.post("/v1/speak", json={**BODY, "speed": 5}).status_code == 422
    assert c.post("/v1/speak", json={**BODY, "voice": "../etc"}).status_code == 422
    assert c.post("/v1/speak", json={**BODY, "format": "mp3"}).status_code == 422  # only WAV; unknown fields are refused


def test_unknown_voice_is_404_and_a_crash_is_500():
    assert client().post("/v1/speak", json={**BODY, "voice": "zz_nobody"}).status_code == 404

    class Broken:
        async def synthesize(self, *args):
            raise error

        async def close(self):
            pass

        model, num_thread, ready, voices, default_voice, generation = "m", None, False, [], "v", 0

    for error, status in ((EngineError("too long", 413), 413), (RuntimeError("boom"), 500)):
        assert client(Broken()).post("/v1/speak", json=BODY).status_code == status


def test_health_and_voices():
    c = client()
    assert c.get("/health").json() == {"status": "ok", "model": "sahilmahendrakar/Paradee-8M-v1.2", "ready": False, "num_thread": None,
                                       "voices": VOICES}
    assert c.get("/v1/voices").json() == {"voices": VOICES, "default": "af_heart"}
    c.post("/v1/speak", json=BODY)
    assert c.get("/health").json()["ready"] is True


def test_each_caller_gets_its_own_revocable_token(tmp_path):
    c = client(admin_password="admin", tokens=TokenStore(tmp_path / "t.json"))
    admin = {"X-Internal-Token": "admin"}
    assert c.post("/v1/speak", json=BODY).status_code == 401
    made = c.post("/admin/tokens", json={"name": "Garrison"}, headers=admin).json()
    assert made["token"].startswith("ttsparadee_")
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/speak", json=BODY, headers=use).status_code == 200
    assert c.delete(f"/admin/tokens/{made['id']}", headers=admin).status_code == 200
    assert c.post("/v1/speak", json=BODY, headers=use).status_code == 401
    assert c.get("/health").status_code == 200  # /health needs no token


def test_a_token_limited_to_voices_refuses_the_others(tmp_path):
    c = client(admin_password="admin", tokens=TokenStore(tmp_path / "t.json"))
    admin = {"X-Internal-Token": "admin"}
    assert c.post("/admin/tokens", json={"name": "x", "projects": ["bad name"]}, headers=admin).status_code == 422
    made = c.post("/admin/tokens", json={"name": "p", "projects": ["bf_isabella"]}, headers=admin).json()
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/speak", json={**BODY, "voice": "bf_isabella"}, headers=use).status_code == 200
    assert c.post("/v1/speak", json={**BODY, "voice": "af_heart"}, headers=use).status_code == 403
    assert c.post("/v1/speak", json=BODY, headers=use).status_code == 403  # the default voice is not in its list
    assert c.get("/v1/voices", headers=use).json()["voices"] == ["bf_isabella"]


def test_rate_limit_edit_stats_and_metrics(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    c = client(admin_password="admin", tokens=store)
    admin = {"X-Internal-Token": "admin"}
    made = c.post("/admin/tokens", json={"name": "p", "rate": 1}, headers=admin).json()
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/speak", json=BODY, headers=use).status_code == 200
    limited = c.post("/v1/speak", json=BODY, headers=use)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert c.put(f"/admin/tokens/{made['id']}", json={"projects": None, "rate": None}, headers=admin).status_code == 200
    assert c.post("/v1/speak", json=BODY, headers=use).status_code == 200  # the limit is gone
    assert c.put("/admin/tokens/nope", json={}, headers=admin).status_code == 404
    assert store.list()[0]["timed"] >= 1
    assert c.get("/metrics").status_code == 401
    assert 'ttsparadee_requests_total{token="p"}' in c.get("/metrics", headers=admin).text
    assert 'ttsparadee_request_seconds_bucket{le="+Inf"}' in c.get("/metrics", headers=admin).text


def test_requests_over_the_in_flight_limit_get_429():
    async def main():
        engine = make_engine()
        release = asyncio.Event()
        real = engine.synthesize

        async def slow(*args, **kwargs):
            await release.wait()
            return await real(*args, **kwargs)

        engine.synthesize = slow
        app = create_app(engine, max_inflight=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc") as http:
            first = asyncio.create_task(http.post("/v1/speak", json=BODY))
            await asyncio.sleep(0.2)
            second = await http.post("/v1/speak", json=BODY)
            release.set()
            return second, await first

    second, first = asyncio.run(main())
    assert second.status_code == 429 and second.headers["Retry-After"] == "5"
    assert first.status_code == 200


def test_workers_are_read_only_and_admin_only(monkeypatch):
    monkeypatch.setenv("SERVICE_REPLICAS", "3")
    c = client(admin_password="admin")
    assert c.get("/admin/workers").status_code == 401
    assert c.get("/admin/workers", headers={"X-Internal-Token": "admin"}).json() == {"replicas": 3, "parallel": 1}


def test_num_thread_is_reported_and_set_by_the_admin(tmp_path):
    engine = make_engine()
    c = client(engine, "admin", tokens=TokenStore(tmp_path / "t.json"))
    admin = {"X-Internal-Token": "admin"}
    assert c.put("/admin/threads", json={"num_thread": 0}, headers=admin).status_code == 422
    assert c.put("/admin/threads", json={"num_thread": 3}, headers=admin).json() == {"num_thread": 3}
    assert c.get("/health").json()["num_thread"] == 3
    assert settings.load(tmp_path / "settings.json")["num_thread"] == 3


def test_settings_are_admin_only_checked_and_apply_live(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    c = client(admin_password="admin", tokens=store, config=config)
    admin = {"X-Internal-Token": "admin"}
    assert c.get("/admin/settings").status_code == 401
    assert c.put("/admin/settings", json={"values": {"max_inflight": -1}}, headers=admin).status_code == 422
    assert c.put("/admin/settings", json={"values": {"max_inflight": 7}}, headers=admin).status_code == 200
    saved = {item["key"]: item for item in c.get("/admin/settings", headers=admin).json()["settings"]}
    assert saved["max_inflight"]["value"] == 7
    assert client(admin_password="admin").put("/admin/settings", json={"values": {}}, headers=admin).status_code == 409


def test_what_another_replica_saved_reaches_this_one(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    engine = make_engine()
    with TestClient(create_app(engine, "admin", tokens=store, config=config)):
        settings.save(store.settings_path, num_thread=5)
        for _ in range(300):  # the loop looks every second
            if engine.num_thread == 5:
                break
            time.sleep(0.02)
    assert engine.num_thread == 5


def test_startup_warms_the_model():
    engine = make_engine()
    with TestClient(create_app(engine, warm=True)) as c:
        for _ in range(100):
            if c.get("/health").json()["ready"]:
                break
            time.sleep(0.02)
        assert engine.loads == [None] and c.get("/health").json()["ready"] is True


def test_health_answers_while_the_voice_is_still_loading():
    import threading
    release = threading.Event()

    def slow(version, threads):
        release.wait(5)
        return make_engine()._loader(version, threads)

    with TestClient(create_app(make_engine(loader=slow), warm=True)) as c:
        assert c.get("/health").json()["ready"] is False
        release.set()


def test_warmup_failure_is_logged_without_stopping_service(caplog):
    def failing(version, threads):
        raise RuntimeError("no voice")

    with TestClient(create_app(make_engine(loader=failing), warm=True)) as c:
        assert c.get("/health").status_code == 200
        for _ in range(100):
            if "warm-up failed" in caplog.text:
                break
            time.sleep(0.02)
    assert "warm-up failed" in caplog.text


def test_warmup_is_optional():
    engine = make_engine()
    with TestClient(create_app(engine)):
        assert engine.loads == []


def test_main_builds_the_engine_from_settings(monkeypatch, tmp_path):
    import app as module

    monkeypatch.setenv("SERVICE_TOKENS_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("SERVICE_NUM_THREAD", "2")
    monkeypatch.setenv("SERVICE_VERSION", "1.1")
    monkeypatch.setenv("SERVICE_PORT", "9999")
    seen, started = [], {}
    monkeypatch.setattr(module, "create_app", lambda client, **kwargs: seen.append(client) or "app")
    monkeypatch.setattr("uvicorn.Server.run", lambda self: started.update(app=self.config.app, host=self.config.host, port=self.config.port))
    module.main()
    assert started["port"] == 9999 and started["host"] == "127.0.0.1"
    assert seen[0].num_thread == 2 and seen[0].version == "1.1" and seen[0].voices == ["af_heart"]


