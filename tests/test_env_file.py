"""load_env_file: a real environment variable wins, and only names come back."""

import settings


def test_load_env_file_keeps_real_variables_and_returns_names(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP_ME=from-file\nNEW_ONE=secret-value\n", encoding="utf-8")
    monkeypatch.setenv("KEEP_ME", "from-environment")
    monkeypatch.delenv("NEW_ONE", raising=False)
    names = settings.load_env_file(env_file)
    assert names == ["NEW_ONE"]
    import os
    assert os.environ["KEEP_ME"] == "from-environment"
    assert os.environ["NEW_ONE"] == "secret-value"
    monkeypatch.delenv("NEW_ONE")


def test_load_env_file_without_a_file_loads_nothing(tmp_path):
    assert settings.load_env_file(tmp_path / "missing.env") == []


def test_env_local_overrides_env_and_compose_is_never_read(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SHARED=base\nONLY_BASE=base\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("SHARED=local\nONLY_LOCAL=local\n", encoding="utf-8")
    (tmp_path / ".env.compose").write_text("ONLY_COMPOSE=compose\n", encoding="utf-8")
    for name in ("SHARED", "ONLY_BASE", "ONLY_LOCAL", "ONLY_COMPOSE"):
        monkeypatch.delenv(name, raising=False)
    loaded = settings.load_env_files(tmp_path)
    import os
    assert list(loaded) == [".env.local", ".env"]
    assert loaded[".env"] == ["ONLY_BASE"]
    assert (os.environ["SHARED"], os.environ["ONLY_BASE"], os.environ["ONLY_LOCAL"]) == ("local", "base", "local")
    assert "ONLY_COMPOSE" not in os.environ
    for name in ("SHARED", "ONLY_BASE", "ONLY_LOCAL"):
        monkeypatch.delenv(name)


def test_env_files_without_any_file_loads_nothing(tmp_path):
    assert settings.load_env_files(tmp_path) == {}
