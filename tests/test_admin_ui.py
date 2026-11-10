"""Browser smoke test for /admin/: sign in, create a token, revoke it.

Needs `pip install playwright && playwright install chromium`; skipped when Playwright is missing.
"""
import socket
import threading
import time

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")

import os
from pathlib import Path

from app import create_app
from conftest import make_engine
from options import SETTINGS
from settings import Config

PASSWORD = "secret"


@pytest.fixture
def base_url():
    state = {"downloaded": False}
    engine = make_engine(cached_sha=lambda repo: "new" if state["downloaded"] else "old", remote_sha=lambda repo: "new",
                         download=lambda repo: state.update(downloaded=True))
    config = Config(Path(os.environ["SERVICE_TOKENS_FILE"]).with_name("settings.json"), SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    app = create_app(engine, PASSWORD, config=config)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            server.should_exit = True
            pytest.fail("Browser test server did not start within 15 seconds")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/admin/"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page(base_url):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        yield page
        browser.close()


def test_wrong_password_is_rejected(page):
    page.fill("#admin", "nope")
    page.click("#signin-button")
    sync_api.expect(page.locator("#login-error")).not_to_be_empty()
    assert page.locator("#dashboard").is_hidden()


def test_create_and_revoke_a_token(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    page.fill("#name", "Garrison")
    page.click("#create-button")
    sync_api.expect(page.locator("#fresh-token")).to_contain_text("ttsparadee_")
    row = page.locator("#rows tr:not(:has-text(\"playground (temporary)\"))")
    sync_api.expect(row).to_contain_text("Garrison")
    sync_api.expect(row.locator('td[data-label="Requests"]')).to_have_text("0")
    row.get_by_role("button", name="Revoke").click()
    page.click("#confirm-revoke")
    sync_api.expect(page.locator("#rows tr", has_text="Garrison")).to_have_count(0)


def test_health_keeps_backend_specific_model_display(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#model-value")).to_have_text("Starting")
    sync_api.expect(page.locator("#health-text")).to_have_text("Starting")


def test_updates_card_checks_and_downloads_without_replica_restart(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#updates"]')
    sync_api.expect(page.locator("#updates-card")).to_be_visible()
    sync_api.expect(page.locator("#updates-description")).to_contain_text("without downloading")
    sync_api.expect(page.locator("#update-note")).to_have_text("Not checked yet.")
    page.click("#check-updates")
    sync_api.expect(page.locator("#update-weights")).to_be_visible()
    sync_api.expect(page.locator("#update-list")).to_contain_text("m")
    page.click("#update-weights")
    sync_api.expect(page.locator("#update-note")).to_contain_text("loads the new files on its next request.")
    sync_api.expect(page.locator("#update-weights")).to_be_visible()
    assert "Restart" not in page.locator("#update-note").inner_text()


def test_token_can_be_limited_to_projects(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    page.fill("#name", "Garrison")
    page.fill("#projects", "Bad Name")
    page.click("#create-button")
    sync_api.expect(page.locator("#token-error")).to_contain_text("not valid")
    page.fill("#projects", "app-a, app-b")
    page.click("#create-button")
    sync_api.expect(page.locator("#rows tr:not(:has-text(\"playground (temporary)\"))")).to_contain_text("Only: app-a, app-b")


def test_token_can_be_edited_and_shows_rate_and_expiry(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    sync_api.expect(page.locator("#project-models-card")).to_be_hidden()  # no project-models tag
    page.fill("#name", "Garrison")
    page.fill("#rate", "5")
    page.fill("#expires", "30")
    page.click("#create-button")
    row = page.locator("#rows tr:not(:has-text(\"playground (temporary)\"))")
    sync_api.expect(row).to_contain_text("5/min")
    sync_api.expect(row).to_contain_text("Expires")
    row.get_by_role("button", name="Edit").click()
    page.fill("#edit-projects", "app-a")
    page.fill("#edit-rate", "")
    page.click("#save-edit")
    sync_api.expect(row).to_contain_text("Only: app-a")
    sync_api.expect(row).not_to_contain_text("5/min")


def test_settings_can_be_saved_and_reset(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#runtime"]')
    sync_api.expect(page.locator("#settings-card")).to_be_visible()
    box = page.locator("#setting-max_inflight")
    sync_api.expect(box).to_have_value("0")
    box.fill("3")
    page.click("#save-settings")
    sync_api.expect(page.locator("#settings-note")).to_have_text("Saved")
    sync_api.expect(page.locator("#setting-max_inflight")).to_have_value("3")
    page.get_by_role("button", name="Reset Calls allowed to wait for the model").click()
    sync_api.expect(page.locator("#setting-max_inflight")).to_have_value("0")


def _open_playground(page):
    """Use separate admin and public credentials."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#playground"]')
    sync_api.expect(page.locator("#playground")).to_be_visible()
    sync_api.expect(page.locator("#pg-token")).to_be_hidden()
    sync_api.expect(page.locator("#pg-token")).not_to_have_value("", timeout=15000)  # filled from a temporary token
    return page.locator("#pg-token").input_value()


def _assert_playground_finished(page, token):
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 200", timeout=15000)
    sync_api.expect(page.locator("#pg-send")).to_be_enabled(timeout=15000)
    assert "<SERVICE_TOKEN>" in page.locator("#pg-curl").text_content()
    assert token not in page.locator("#pg-curl").text_content()
    assert PASSWORD not in page.locator("#pg-curl").text_content()
    stored = page.evaluate("() => [...Object.values(localStorage), ...Object.values(sessionStorage)]")
    assert not any(token in value for value in stored)


def test_playground_uses_public_route_and_renders_answer(page):
    """Exercise real public auth and the family fake engine."""
    token = _open_playground(page)
    page.fill("#pg-text", "Hello world.")
    page.click("#pg-send")
    _assert_playground_finished(page, token)
    sync_api.expect(page.locator("#pg-media audio")).to_be_visible()
    assert page.locator("#pg-media audio").get_attribute("src").startswith("blob:")
    sync_api.expect(page.locator("#pg-media a[download]")).to_be_visible()


def test_connect_card_lists_the_addresses_other_services_can_use(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#connections"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    card = page.locator("#connect-card")
    sync_api.expect(card).to_contain_text("The address you opened this page with")
    sync_api.expect(card).to_contain_text("host.docker.internal")
    sync_api.expect(card).to_contain_text("/health")
    port = page.url.split(":")[2].split("/")[0]
    sync_api.expect(card).to_contain_text("http://127.0.0.1:" + port)


def test_sidebar_pages_keep_one_header_and_preserve_form_drafts(page):
    """Navigation changes the visible page without rebuilding active forms."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#page-title")).to_have_text("Overview")
    sync_api.expect(page.locator("#create")).to_be_hidden()
    page.click('#admin-nav a[href="#tokens"]')
    page.fill("#name", "Keep this draft")
    page.click("#sidebar-toggle")
    sync_api.expect(page.locator("#sidebar-toggle")).to_have_attribute("aria-expanded", "false")
    sync_api.expect(page.locator("#name")).to_have_value("Keep this draft")
    page.click('#admin-nav a[href="#overview"]')
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#name")).to_have_value("Keep this draft")
    sync_api.expect(page.locator("#page-title")).to_have_text("Access tokens")
    assert page.locator("#dashboard h1").count() == 1
    page.reload()
    sync_api.expect(page.locator("#page-title")).to_have_text("Access tokens")
    sync_api.expect(page.locator("#sidebar-toggle")).to_have_attribute("aria-expanded", "false")


def test_sidebar_capabilities_and_history(page):
    """Unsupported pages cannot be opened through a manually entered hash."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    tags = page.evaluate("CONFIG.tags")
    assert page.locator('#admin-nav a[href="#models"]').count() == int("project-models" in tags)
    assert page.locator('#admin-nav a[href="#voices"]').count() == int("voice-cloning" in tags)
    page.click('#admin-nav a[href="#connections"]')
    sync_api.expect(page.locator("#page-title")).to_have_text("Connections")
    page.go_back()
    sync_api.expect(page.locator("#page-title")).to_have_text("Overview")
    page.go_forward()
    sync_api.expect(page.locator("#page-title")).to_have_text("Connections")
    page.evaluate("location.hash = '#unsupported'")
    sync_api.expect(page.locator("#page-title")).to_have_text("Overview")


def test_sidebar_responsive_layout_and_keyboard_toggle(page):
    """Both sidebar states retain navigation without horizontal page overflow."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    for width in (1280, 768, 375, 320):
        page.set_viewport_size({"width": width, "height": 900})
        for expanded in (False, True):
            page.locator("#sidebar-toggle").focus()
            page.keyboard.press("Enter")
            sync_api.expect(page.locator("#sidebar-toggle")).to_have_attribute("aria-expanded", str(expanded).lower())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator("#admin-sidebar").bounding_box()["x"] < page.locator("#page-content").bounding_box()["x"]
