from __future__ import annotations

from copy import deepcopy

import pytest

from betguard.webfill.local_sandbox_contracts import LocalSandboxContractError
from betguard.webfill.local_sandbox_page import LocalSandboxPageServer
from betguard.webfill.local_sandbox_provider import PlaywrightLocalSandboxProvider
from tests.fixtures.local_sandbox_profiles import sample_case


pytestmark = pytest.mark.e2e_local


@pytest.fixture
def chromium_page():
    playwright = pytest.importorskip("playwright.sync_api")
    manager = playwright.sync_playwright().start()
    try:
        try:
            browser = manager.chromium.launch(headless=True)
        except Exception as exc:  # browser binary is optional in minimal dev installs
            pytest.skip(f"local Chromium unavailable: {exc}")
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        yield page
        context.close()
        browser.close()
    finally:
        manager.stop()


def test_local_page_button_posts_only_opaque_tokens(chromium_page):
    received = []
    action_id = "lsfa-" + "a" * 32
    key = "lsfi-playwright-click-0001"
    with LocalSandboxPageServer(
        lambda request: received.append(dict(request)) or {"status": "ACK"},
        action_id=action_id,
        idempotency_key=key,
    ) as server:
        chromium_page.goto(server.url, wait_until="domcontentloaded")
        chromium_page.locator("#sandbox-fill-action").click()
        chromium_page.locator("#status").wait_for(state="visible")
        chromium_page.wait_for_timeout(100)
        assert received == [{"schema_version": "betguard-local-sandbox-execute-request-v1", "action_id": action_id, "idempotency_key": key}]
        assert chromium_page.locator("#sandbox-submit-count").get_attribute("data-count") == "0"


def test_exact_local_fill_readback_nested_structures_and_no_submit(chromium_page):
    with LocalSandboxPageServer(lambda _request: {"status": "UNUSED"}) as server:
        chromium_page.goto(server.url, wait_until="domcontentloaded")
        normal, _ = sample_case("sample-010")
        column, _ = sample_case("sample-011")
        operations = deepcopy(normal + column)
        operations[1]["operation_index"] = 2
        provider = PlaywrightLocalSandboxProvider(chromium_page, server.url)
        receipt = provider.fill(operations)
        assert receipt["readback"][0]["multiplier"]["ordered_rules"] == ["2X2", "3X5"]
        assert receipt["readback"][1]["number_groups"] == [["30"], ["35", "36", "38"]]
        assert receipt["readback"][1]["continuation"]["present"] is True
        assert receipt["submit_event_count"] == 0
        assert receipt["external_request_count"] == 0
        assert chromium_page.locator("#sandbox-submit-count").get_attribute("data-count") == "0"


def test_provider_rejects_non_exact_host_before_any_mutation(chromium_page):
    with pytest.raises(LocalSandboxContractError) as raised:
        PlaywrightLocalSandboxProvider(chromium_page, "http://localhost:8123/sandbox-fill")
    assert raised.value.code == "SANDBOX_EXTERNAL_URL_REJECTED"
