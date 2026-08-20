"""Server-owned orchestration for the loopback-only Betguard MVP.

The public UI supplies only a persisted Human Review identity and, after the
server mints an action, the three-field sandbox execution request.  Bet values
are reloaded through the existing Candidate -> Queue -> Claim -> Prepare ->
Mapping authority chain and never accepted from the browser.
"""

from __future__ import annotations

import hashlib
import queue
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from betguard.vision.candidate_authority import (
    VisionCandidateAuthorityStore,
    canonical_sha256,
)
from betguard.vision.validated_candidate_claims import ValidatedCandidateClaimStore
from betguard.vision.validated_candidate_queue import ValidatedCandidateQueueStore
from betguard.vision.webfill_mapping_preview import (
    WebfillAdapterTargetProfileStore,
    WebfillDomObservationStore,
    WebfillMappingPreviewStore,
    _field_identity_projection,
    _form_fingerprint_projection,
    _form_identity_projection,
    _page_fingerprint_projection,
)
from betguard.vision.webfill_prepare import WebfillPrepareStore
from betguard.vision.webfill_target_profiles import WebfillTargetProfileStore
from betguard.webfill.local_sandbox_contracts import (
    LocalSandboxContractError,
    decode_public_execute_request,
)
from betguard.webfill.local_sandbox_page import LocalSandboxPageServer
from betguard.webfill.local_sandbox_provider import (
    InMemorySandboxBrowser,
    PlaywrightLocalSandboxProvider,
    SandboxBrowser,
)
from betguard.webfill.local_sandbox_workflow import (
    LocalSandboxWorkflow,
    MappingPreviewAuthorityLoader,
)


ACTOR = "assist-panel-human"
PRINCIPAL = "mvp-local-sandbox-worker"
CONSUMER_ID = "vqcns-" + "7" * 32
SERVER_SESSION_ID = "mvp-local-sandbox-session-v1"
LOGICAL_PROFILE_ID = "wtp-betguard-local-sandbox"
ADAPTER_PROFILE_ID = "watp-betguard-local-sandbox"


class MvpWorkflowError(Exception):
    """Stable, user-safe MVP orchestration error."""

    def __init__(self, code: str, message: str, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class _BrowserProxy:
    def __init__(self, browser: SandboxBrowser | None = None) -> None:
        self._browser = browser
        self._lock = threading.RLock()

    def set_browser(self, browser: SandboxBrowser) -> None:
        with self._lock:
            self._browser = browser

    def fill(self, operations: list[Mapping[str, Any]]) -> dict[str, Any]:
        with self._lock:
            browser = self._browser
        if browser is None:
            raise LocalSandboxContractError(
                "SANDBOX_NOT_OPEN", "local sandbox page is not open", 409
            )
        return browser.fill(operations)


class LocalSandboxBrowserRuntime:
    """Own one visible Chromium page in a dedicated worker thread.

    Only the exact loopback sandbox URL is opened.  The narrow fill provider
    exposes no navigation, click, submit, arbitrary selector, or script API.
    """

    def __init__(self, *, headless: bool = False) -> None:
        self._commands: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._server: LocalSandboxPageServer | None = None
        self._provider: PlaywrightLocalSandboxProvider | None = None
        self._action_id: str | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._headless = bool(headless)

    @property
    def url(self) -> str | None:
        return self._server.url if self._server is not None else None

    def start(
        self,
        *,
        action_id: str,
        idempotency_key: str,
        execute_callback: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> SandboxBrowser:
        if self._thread is not None and self._action_id != action_id:
            self.close()
        if self._thread is not None:
            if self._provider is None:
                raise MvpWorkflowError("SANDBOX_OPEN_FAILED", "本機測試表單無法開啟。", 500)
            return self
        self._server = LocalSandboxPageServer(
            execute_callback,
            action_id=action_id,
            idempotency_key=idempotency_key,
        ).start()
        self._action_id = action_id
        self._thread = threading.Thread(
            target=self._worker,
            args=(self._server.url,),
            name="betguard-local-sandbox-browser",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=30) or self._provider is None:
            error = self._error
            self.close()
            raise MvpWorkflowError(
                "SANDBOX_OPEN_FAILED",
                "本機測試表單無法開啟，請確認 Playwright Chromium 已安裝。",
                500,
            ) from error
        return self

    def _worker(self, url: str) -> None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self._headless)
                context = browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                self._provider = PlaywrightLocalSandboxProvider(page, url)
                self._ready.set()
                while True:
                    command = self._commands.get()
                    if command[0] == "close":
                        break
                    if command[0] != "fill":
                        raise RuntimeError("unsupported sandbox runtime command")
                    _name, operations, response = command
                    try:
                        response.put((True, self._provider.fill(operations)))
                    except BaseException as exc:
                        response.put((False, exc))
                context.close()
                browser.close()
        except BaseException as exc:  # worker failure is returned safely
            self._error = exc
            self._ready.set()

    def close(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._commands.put(("close",))
            thread.join(timeout=10)
        if self._server is not None:
            self._server.close()
        self._thread = None
        self._server = None
        self._provider = None
        self._action_id = None

    def fill(self, operations: list[Mapping[str, Any]]) -> dict[str, Any]:
        if self._thread is None or not self._thread.is_alive() or self._provider is None:
            raise LocalSandboxContractError(
                "SANDBOX_NOT_OPEN", "local sandbox page is not open", 409
            )
        response: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._commands.put(("fill", deepcopy(operations), response))
        try:
            ok, value = response.get(timeout=30)
        except queue.Empty as exc:
            raise LocalSandboxContractError(
                "SANDBOX_FILL_TIMEOUT", "local sandbox fill timed out", 504
            ) from exc
        if ok:
            return value
        raise value


class MvpLocalSandboxOrchestrator:
    """Build and execute the hidden authority chain for one local MVP."""

    def __init__(
        self,
        base_dir: Path | str,
        authority_store: VisionCandidateAuthorityStore,
        queue_store: ValidatedCandidateQueueStore,
        *,
        browser: SandboxBrowser | None = None,
        browser_runtime: LocalSandboxBrowserRuntime | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self._authority = authority_store
        self._queue = queue_store
        self._claims = ValidatedCandidateClaimStore.from_authority_store(
            queue_store,
            authority_store,
            owner_session_validator=lambda principal, consumer, session: (
                principal == PRINCIPAL
                and consumer == CONSUMER_ID
                and session == SERVER_SESSION_ID
            ),
        )
        self._logical_profiles = WebfillTargetProfileStore(self.base_dir / "prepare")
        self._prepares = WebfillPrepareStore(
            self.base_dir / "prepare",
            self._queue,
            self._claims,
            self._logical_profiles,
        )
        self._observations = WebfillDomObservationStore(self.base_dir / "mapping")
        self._adapter_profiles = WebfillAdapterTargetProfileStore(self.base_dir / "mapping")
        self._previews = WebfillMappingPreviewStore(
            self.base_dir / "mapping",
            self._prepares,
            self._observations,
            self._adapter_profiles,
        )
        self._loader = MappingPreviewAuthorityLoader(
            self._previews, self._prepares, self._queue, self._claims
        )
        self._browser_proxy = _BrowserProxy(browser)
        self._workflow = LocalSandboxWorkflow(
            self.base_dir / "sandbox-actions", self._loader, self._browser_proxy
        )
        self._browser_runtime = browser_runtime
        self._lock = threading.RLock()
        self._action_owner: dict[str, str] = {}
        self._action_context: dict[str, dict[str, Any]] = {}

    @property
    def sandbox_url(self) -> str | None:
        return self._browser_runtime.url if self._browser_runtime is not None else None

    def create_fill_action(self, review_session_id: str) -> dict[str, Any]:
        if not isinstance(review_session_id, str) or not review_session_id:
            raise MvpWorkflowError("MVP_REQUEST_INVALID", "Human Review 不正確。", 400)
        with self._lock:
            review = self._authority.get_human_review(review_session_id)
            if review is None:
                raise MvpWorkflowError("REVIEW_NOT_FOUND", "找不到 Human Review。", 404)
            candidate_result = self._authority.create_candidate(
                review_session_id=review_session_id,
                expected_human_answer_revision=review["human_answer_revision"],
                expected_human_answer_hash=review["human_answer_hash"],
                idempotency_key=(
                    f"{review_session_id}:{review['human_answer_revision']}:"
                    f"{review['human_answer_hash']}"
                ),
                actor=ACTOR,
            )
            candidate = candidate_result["candidate"]
            entry = self._enqueue_candidate(candidate)
            queued = self._queue.list_entries(state="QUEUED")
            if not queued or queued[0]["queue_entry"]["queue_entry_id"] != entry["queue_entry_id"]:
                raise MvpWorkflowError(
                    "QUEUE_BUSY", "有較早的待處理工作，請稍後再試。", 409
                )
            claim = self._claim_entry()
            if (
                claim is None
                or claim["queue_entry_identity"]["queue_entry_id"]
                != entry["queue_entry_id"]
            ):
                raise MvpWorkflowError(
                    "QUEUE_BUSY", "有較早的待處理工作，請稍後再試。", 409
                )
            prepared = self._prepare(entry, claim, candidate, review["game"])
            preview = self._mapping(prepared, claim)
            if preview["status"] != "VALID_MAPPING_PREVIEW":
                raise MvpWorkflowError(
                    "SANDBOX_VERSION_MISMATCH",
                    "本機測試表單版本不符，請重新開啟。",
                    409,
                )
            artifact = preview["artifact"]
            # A fresh authority loader intentionally replaces the previous
            # review binding. Old bound actions then fail their snapshot check
            # instead of inheriting a new Candidate revision.
            self._loader = MappingPreviewAuthorityLoader(
                self._previews, self._prepares, self._queue, self._claims
            )
            self._workflow = LocalSandboxWorkflow(
                self.base_dir / "sandbox-actions",
                self._loader,
                self._browser_proxy,
            )
            self._loader.register_reference(
                review_session_id=review_session_id,
                mapping_preview_id=artifact["mapping_preview_id"],
                claim_generation=claim["claim_generation"],
                fencing_token=claim["fencing_token"],
                authenticated_principal=PRINCIPAL,
                consumer_id=CONSUMER_ID,
                server_session_id=SERVER_SESSION_ID,
            )
            action = self._workflow.bind_human_fill_action(
                review_session_id,
                authenticated_actor=PRINCIPAL,
                interactive_session_id=SERVER_SESSION_ID,
            )
            idempotency_key = "lsfi-" + uuid.uuid4().hex
            self._action_owner[action["action_id"]] = idempotency_key
            self._action_context[action["action_id"]] = {
                "claim_id": claim["claim_id"],
                "claim_generation": claim["claim_generation"],
                "queue_entry_id": entry["queue_entry_id"],
                "cleaned": False,
            }
            if self._browser_runtime is not None:
                provider = self._browser_runtime.start(
                    action_id=action["action_id"],
                    idempotency_key=idempotency_key,
                    execute_callback=self.execute_fill,
                )
                self._browser_proxy.set_browser(provider)
            return {
                "schema_version": "betguard-local-sandbox-execute-request-v1",
                "action_id": action["action_id"],
                "idempotency_key": idempotency_key,
                "sandbox_url": self.sandbox_url,
                "ready": True,
                "human_confirmation_required": True,
                "auto_submit": False,
            }

    def execute_fill(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = decode_public_execute_request(payload)
        expected = self._action_owner.get(request["action_id"])
        if expected != request["idempotency_key"]:
            raise MvpWorkflowError(
                "SANDBOX_ACTION_OWNER_INVALID", "輔助填入動作已失效。", 403
            )
        result = self._workflow.execute_public(
            request,
            authenticated_actor=PRINCIPAL,
            interactive_session_id=SERVER_SESSION_ID,
        )
        self._finalize_attempt(request["action_id"], result)
        return result

    def _finalize_attempt(self, action_id: str, result: Mapping[str, Any]) -> None:
        context = self._action_context.get(action_id)
        if context is None or context["cleaned"]:
            return
        transition = "RELEASE" if result.get("status") == "FILLED_VERIFIED" else "ABANDON"
        key = "qik-" + uuid.uuid4().hex
        if transition == "RELEASE":
            action = self._claims.bind_release_action(
                claim_id=context["claim_id"],
                claim_generation=context["claim_generation"],
                authenticated_principal=PRINCIPAL,
                consumer_id=CONSUMER_ID,
                server_session_id=SERVER_SESSION_ID,
                idempotency_key=key,
            )
            self._claims.release(
                {"action_id": action["action_id"], "idempotency_key": key},
                authenticated_principal=PRINCIPAL,
                consumer_id=CONSUMER_ID,
                server_session_id=SERVER_SESSION_ID,
            )
        else:
            action = self._claims.bind_abandon_action(
                claim_id=context["claim_id"],
                claim_generation=context["claim_generation"],
                authenticated_principal=PRINCIPAL,
                consumer_id=CONSUMER_ID,
                server_session_id=SERVER_SESSION_ID,
                idempotency_key=key,
            )
            self._claims.abandon(
                {"action_id": action["action_id"], "idempotency_key": key},
                authenticated_principal=PRINCIPAL,
                consumer_id=CONSUMER_ID,
                server_session_id=SERVER_SESSION_ID,
            )
        remove_key = "qik-" + uuid.uuid4().hex
        remove_action = self._queue.bind_human_remove_action(
            authenticated_actor=ACTOR,
            interactive_session_id=SERVER_SESSION_ID,
            queue_entry_id=context["queue_entry_id"],
            idempotency_key=remove_key,
        )
        self._queue.remove(
            {
                "queue_entry_id": context["queue_entry_id"],
                "human_remove_action_id": remove_action["action_id"],
                "idempotency_key": remove_key,
            },
            authenticated_actor=ACTOR,
            interactive_session_id=SERVER_SESSION_ID,
        )
        context["cleaned"] = True

    def _enqueue_candidate(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        identity = {
            "candidate_id": candidate["candidate_id"],
            "candidate_revision": candidate["revision"],
            "canonical_content_hash": candidate["canonical_content_hash"],
        }
        digest = hashlib.sha256(
            f"enqueue:{identity['candidate_id']}:{identity['candidate_revision']}:"
            f"{identity['canonical_content_hash']}".encode()
        ).hexdigest()[:32]
        key = "qik-" + digest
        action = self._queue.bind_human_enqueue_action(
            authenticated_actor=ACTOR,
            interactive_session_id=SERVER_SESSION_ID,
            candidate_id=identity["candidate_id"],
            candidate_revision=identity["candidate_revision"],
            canonical_content_hash=identity["canonical_content_hash"],
            idempotency_key=key,
        )
        return self._queue.enqueue(
            {
                "candidate_id": identity["candidate_id"],
                "expected_candidate_revision": identity["candidate_revision"],
                "expected_content_hash": identity["canonical_content_hash"],
                "human_enqueue_action_id": action["action_id"],
                "idempotency_key": key,
            },
            authenticated_actor=ACTOR,
            interactive_session_id=SERVER_SESSION_ID,
        )["queue_entry"]

    def _claim_entry(self) -> dict[str, Any] | None:
        key = "qik-" + uuid.uuid4().hex
        action = self._claims.bind_claim_action(
            authenticated_principal=PRINCIPAL,
            consumer_id=CONSUMER_ID,
            server_session_id=SERVER_SESSION_ID,
            idempotency_key=key,
        )
        return self._claims.claim_next(
            {"action_id": action["action_id"], "idempotency_key": key},
            authenticated_principal=PRINCIPAL,
            consumer_id=CONSUMER_ID,
            server_session_id=SERVER_SESSION_ID,
        )

    def _prepare(
        self,
        entry: Mapping[str, Any],
        claim: Mapping[str, Any],
        candidate: Mapping[str, Any],
        game: str,
    ) -> dict[str, Any]:
        profile = _logical_profile(str(game))
        self._logical_profiles.register_profile(profile, activate=True)
        request = {
            "schema_version": "vision-webfill-prepare-request-v1",
            "queue_entry_id": entry["queue_entry_id"],
            "claim_id": claim["claim_id"],
            "claim_session_id": SERVER_SESSION_ID,
            "claim_generation": claim["claim_generation"],
            "fencing_token": claim["fencing_token"],
            "expected_candidate_id": candidate["candidate_id"],
            "expected_canonical_content_hash": candidate["canonical_content_hash"],
            "expected_queue_revision": 1,
            "target_profile_id": profile["target_profile_id"],
            "target_profile_version": profile["target_profile_version"],
            "idempotency_key": "wpi-" + uuid.uuid4().hex,
        }
        return self._prepares.create_prepare(
            request,
            authenticated_principal=PRINCIPAL,
            consumer_id=CONSUMER_ID,
            server_session_id=SERVER_SESSION_ID,
        )["artifact"]

    def _mapping(
        self, prepared: Mapping[str, Any], claim: Mapping[str, Any]
    ) -> dict[str, Any]:
        observation = _observation_for(prepared)
        self._observations.register_observation(observation)
        profile = _adapter_profile(prepared, observation)
        self._adapter_profiles.register_profile(profile, activate=True)
        request = {
            "schema_version": "vision-webfill-mapping-preview-request-v1",
            "prepare_id": prepared["prepare_id"],
            "expected_prepare_record_integrity_hash": prepared["record_integrity_hash"],
            "expected_deterministic_plan_hash": prepared["deterministic_plan_hash"],
            "expected_authority_binding_hash": prepared["authority_binding_hash"],
            "claim_id": claim["claim_id"],
            "claim_generation": claim["claim_generation"],
            "claim_session_id": SERVER_SESSION_ID,
            "fencing_token": claim["fencing_token"],
            "adapter_target_profile_id": profile["adapter_target_profile_id"],
            "adapter_target_profile_version": profile["adapter_target_profile_version"],
            "expected_adapter_target_profile_integrity_hash": profile["profile_integrity_hash"],
            "dom_observation_id": observation["dom_observation_id"],
            "expected_observation_hash": observation["observation_hash"],
            "expected_page_fingerprint": observation["page_fingerprint"],
            "expected_page_instance_id": observation["page_instance_id"],
            "expected_navigation_epoch": observation["navigation_epoch"],
            "idempotency_key": "vwmi-" + uuid.uuid4().hex,
        }
        return self._previews.create_mapping_preview(
            request,
            authenticated_principal=PRINCIPAL,
            consumer_id=CONSUMER_ID,
            server_session_id=SERVER_SESSION_ID,
        )


def _logical_profile(game: str) -> dict[str, Any]:
    game_suffix = hashlib.sha256(game.encode("utf-8")).hexdigest()[:8]
    profile = {
        "schema_version": "vision-webfill-target-profile-v1",
        "target_profile_id": LOGICAL_PROFILE_ID + "-" + game_suffix,
        "target_profile_version": 1,
        "game": game,
        "capabilities": {
            "supported_bet_types": ["normal", "column"],
            "supported_special_play_kinds": [
                "none", "structured_human_play", "tail", "car", "half_car", "each"
            ],
            "supports_continuation": True,
            "supports_multiple_multiplier_rules": True,
            "maximum_active_bets": 100,
            "maximum_number_groups_per_bet": 100,
            "maximum_numbers_per_group": 100,
        },
        "ordering_contract": {
            "bet_order": "preserve_candidate_active_bets",
            "group_order": "preserve",
            "number_order": "preserve",
            "multiplier_rule_order": "preserve",
        },
        "adapter_boundary": {
            "logical_contract_only": True,
            "dom_mapping_present": False,
            "browser_execution_authorized": False,
            "submit_authorized": False,
        },
        "created_at": "2026-08-20T00:00:00+00:00",
        "profile_integrity_hash": "",
    }
    profile["profile_integrity_hash"] = canonical_sha256(
        {key: value for key, value in profile.items() if key != "profile_integrity_hash"}
    )
    return profile


def _selector_for(action: str, operation_index: int, **indices: int) -> str:
    values = {"operation_index": operation_index, **indices}
    templates = {
        "SET_BET_TYPE": "bet-{operation_index}-type",
        "SET_NUMBER": "bet-{operation_index}-g{group_index}-n{number_index}",
        "SET_MULTIPLIER_RULE": "bet-{operation_index}-m{multiplier_rule_index}-c1",
        "SET_SPECIAL_PLAY": "bet-{operation_index}-special-c1",
        "SET_CONTINUATION": "bet-{operation_index}-continuation-c1",
    }
    return templates[action].format(**values)


def _observation_for(prepared: Mapping[str, Any]) -> dict[str, Any]:
    selectors: list[str] = []
    for operation in prepared["logical_plan"]["operations"]:
        index = operation["operation_index"]
        selectors.append(_selector_for("SET_BET_TYPE", index))
        for group_index, group in enumerate(operation["number_groups"], 1):
            for number_index, _number in enumerate(group, 1):
                selectors.append(
                    _selector_for(
                        "SET_NUMBER", index,
                        group_index=group_index,
                        number_index=number_index,
                    )
                )
        for rule_index, _rule in enumerate(operation["multiplier"]["ordered_rules"], 1):
            selectors.append(
                _selector_for(
                    "SET_MULTIPLIER_RULE", index,
                    multiplier_rule_index=rule_index,
                )
            )
        if operation["special_play"]["kind"] != "none":
            selectors.append(_selector_for("SET_SPECIAL_PLAY", index))
        if operation["continuation"]["present"]:
            selectors.append(_selector_for("SET_CONTINUATION", index))
    form: dict[str, Any] = {
        "document_order": 1,
        "method_category": "POST",
        "structural_marker_hashes": ["a" * 64],
        "fields": [],
    }
    for order, selector in enumerate(selectors, 1):
        field = {
            "document_order": order,
            "tag_name": "input",
            "input_type": "text",
            "label_text": None,
            "accessible_name": None,
            "selector_candidates": [
                {
                    "selector_kind": "DATA_TESTID",
                    "selector_text": selector,
                    "structural_only": True,
                }
            ],
            "structural_marker_hashes": [],
            "visible": True,
            "disabled": False,
            "readonly": False,
            "occupancy": "EMPTY",
            "field_id": "",
            "field_identity_hash": "",
        }
        field["field_identity_hash"] = canonical_sha256(
            _field_identity_projection(form, field)
        )
        field["field_id"] = "vwf-" + field["field_identity_hash"][:32]
        form["fields"].append(field)
    form_hash = canonical_sha256(_form_identity_projection(form))
    form["form_id"] = "vwform-" + form_hash[:32]
    form["form_fingerprint"] = canonical_sha256(_form_fingerprint_projection(form))
    identity = canonical_sha256(
        {
            "prepare": prepared["prepare_id"],
            "plan": prepared["deterministic_plan_hash"],
            "form": form["form_fingerprint"],
        }
    )
    observation = {
        "schema_version": "vision-webfill-dom-observation-v1",
        "dom_observation_id": "vwdo-" + identity[:32],
        "observed_at": "2026-08-20T00:00:00+00:00",
        "page_instance_id": "vwpi-" + identity[32:64],
        "navigation_epoch": 1,
        "page_identity": {
            "site_id": "site-betguard-local-sandbox",
            "game": prepared["logical_plan"]["game"],
            "origin_identity_hash": hashlib.sha256(b"http://127.0.0.1").hexdigest(),
            "normalized_path_identity_hash": hashlib.sha256(b"/sandbox-fill").hexdigest(),
            "structural_marker_hashes": ["d" * 64],
        },
        "forms": [form],
        "page_fingerprint_algorithm": "sha256-canonical-dom-structure-v1",
        "page_fingerprint": "",
        "observation_hash": "",
        "safety": {
            "read_only_observation": True,
            "occupancy_class_derived": True,
            "raw_input_value_content_exposed": False,
            "raw_input_value_content_persisted": False,
            "query_or_fragment_persisted": False,
            "cookies_or_storage_read": False,
            "html_or_script_persisted": False,
            "dom_mutation_performed": False,
            "click_performed": False,
            "typing_performed": False,
            "events_dispatched": False,
            "navigation_performed": False,
            "submit_performed": False,
        },
    }
    observation["page_fingerprint"] = canonical_sha256(
        _page_fingerprint_projection(observation)
    )
    observation["observation_hash"] = canonical_sha256(
        {key: value for key, value in observation.items() if key != "observation_hash"}
    )
    return observation


def _adapter_profile(
    prepared: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    templates = {
        "SET_BET_TYPE": ("BET", "bet-{operation_index}-type", ["operation_index"]),
        "SET_NUMBER": (
            "NUMBER", "bet-{operation_index}-g{group_index}-n{number_index}",
            ["operation_index", "group_index", "number_index"],
        ),
        "SET_MULTIPLIER_RULE": (
            "MULTIPLIER_RULE", "bet-{operation_index}-m{multiplier_rule_index}-c1",
            ["operation_index", "multiplier_rule_index"],
        ),
        "SET_SPECIAL_PLAY": (
            "SPECIAL_PLAY", "bet-{operation_index}-special-c1", ["operation_index"],
        ),
        "SET_CONTINUATION": (
            "CONTINUATION", "bet-{operation_index}-continuation-c1", ["operation_index"],
        ),
    }
    rules = []
    for action, (scope, template, placeholders) in templates.items():
        rules.append(
            {
                "rule_id": "rule-" + action.lower().replace("_", "-"),
                "logical_action": action,
                "applicable_bet_types": ["normal", "column"],
                "source_scope": scope,
                "selector_kind": "DATA_TESTID",
                "selector_template": template,
                "allowed_placeholders": placeholders,
                "component_policy": "SINGLE_FIELD",
                "required_component_count": 1,
                "cardinality": "EXACTLY_ONE_PER_COMPONENT",
                "expected_field": {
                    "tag_name": "input",
                    "input_types": ["text"],
                    "label_text": None,
                    "required_structural_marker_hashes": [],
                    "must_be_visible": True,
                    "must_be_enabled": True,
                    "must_be_writable": True,
                    "must_be_empty": True,
                },
                "lossless_required": True,
            }
        )
    form = observation["forms"][0]
    game_suffix = hashlib.sha256(
        str(observation["page_identity"]["game"]).encode("utf-8")
    ).hexdigest()[:8]
    profile = {
        "schema_version": "vision-webfill-adapter-target-profile-v1",
        "adapter_target_profile_id": (
            ADAPTER_PROFILE_ID + "-" + game_suffix + "-"
            + observation["page_fingerprint"][:8]
        ),
        "adapter_target_profile_version": 1,
        "logical_target_profile_identity": deepcopy(prepared["authority"]["target_profile"]),
        "site_contract": {
            "site_id": observation["page_identity"]["site_id"],
            "game": observation["page_identity"]["game"],
            "origin_identity_hash": observation["page_identity"]["origin_identity_hash"],
            "normalized_path_identity_hash": observation["page_identity"]["normalized_path_identity_hash"],
            "page_fingerprint_algorithm": observation["page_fingerprint_algorithm"],
            "expected_page_fingerprint": observation["page_fingerprint"],
            "required_page_marker_hashes": observation["page_identity"]["structural_marker_hashes"],
        },
        "form_contract": {
            "expected_form_id": form["form_id"],
            "expected_form_fingerprint": form["form_fingerprint"],
            "required_form_marker_hashes": form["structural_marker_hashes"],
            "exact_form_match": True,
        },
        "logical_field_rules": rules,
        "created_at": "2026-08-20T00:00:00+00:00",
        "profile_integrity_hash": "",
        "safety": {
            "contains_bet_values": False,
            "logical_profile_extension_only": True,
            "read_only_mapping_only": True,
            "selector_values_are_structural_only": True,
            "browser_execution_authorized": False,
            "dom_mutation_authorized": False,
            "fill_authorized": False,
            "submit_authorized": False,
        },
    }
    profile["profile_integrity_hash"] = canonical_sha256(
        {key: value for key, value in profile.items() if key != "profile_integrity_hash"}
    )
    return profile


def build_test_orchestrator(
    base_dir: Path | str,
    authority_store: VisionCandidateAuthorityStore,
    queue_store: ValidatedCandidateQueueStore,
) -> MvpLocalSandboxOrchestrator:
    """Test composition with no browser process or external side effect."""

    return MvpLocalSandboxOrchestrator(
        base_dir,
        authority_store,
        queue_store,
        browser=InMemorySandboxBrowser(),
    )
