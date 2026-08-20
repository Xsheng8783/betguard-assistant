"""Allowlisted browser provider for the fixed local sandbox page only."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from betguard.webfill.local_sandbox_contracts import (
    LOCAL_SANDBOX_PATH,
    LocalSandboxContractError,
    require_loopback_url,
)


FIELDS = (
    "human_bet_id", "bet_type", "number_groups", "multiplier",
    "continuation", "special_play",
)


class SandboxPartialFillError(Exception):
    """The page may have been partially changed; callers must never retry."""

    def __init__(self, message: str, *, filled_field_pointers: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.filled_field_pointers = list(filled_field_pointers)


class SandboxBrowser(Protocol):
    def fill(self, operations: Sequence[Mapping[str, Any]]) -> dict[str, Any]: ...


def expected_readback(operations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "human_bet_id": operation["human_bet_id"],
            "bet_type": operation["bet_type"],
            "number_groups": deepcopy(operation["number_groups"]),
            "multiplier": deepcopy(operation["multiplier"]),
            "continuation": deepcopy(operation["continuation"]),
            "special_play": deepcopy(operation["special_play"]),
        }
        for operation in operations
    ]


@dataclass
class InMemorySandboxBrowser:
    """Deterministic test double with the same one-shot mutation semantics."""

    fail_after_fields: int | None = None
    crash: bool = False

    def __post_init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.mutation_count = 0
        self.submit_event_count = 0
        self.external_request_count = 0
        self.calls = 0

    def fill(self, operations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        filled: list[str] = []
        rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(expected_readback(operations)):
            target: dict[str, Any] = {}
            for field in FIELDS:
                pointer = f"/bets/{row_index}/{field}"
                target[field] = deepcopy(row[field])
                filled.append(pointer)
                self.mutation_count += 1
                if self.fail_after_fields is not None and len(filled) >= self.fail_after_fields:
                    self.rows.extend(rows + [target])
                    raise SandboxPartialFillError(
                        "injected partial fill", filled_field_pointers=filled
                    )
            rows.append(target)
        if self.crash:
            raise RuntimeError("injected browser crash")
        self.rows = rows
        return {
            "filled_field_pointers": filled,
            "readback": deepcopy(rows),
            "mutation_count": self.mutation_count,
            "submit_event_count": self.submit_event_count,
            "external_request_count": self.external_request_count,
            "current_url": f"http://127.0.0.1:1{LOCAL_SANDBOX_PATH}",
        }


class PlaywrightLocalSandboxProvider:
    """A narrow adapter over an already-open Playwright Page.

    There is intentionally no goto, click, evaluate, selector input, cookie,
    storage, submit, confirm, or window API on this class.
    """

    def __init__(self, page: Any, sandbox_url: str) -> None:
        parsed = urlsplit(require_loopback_url(sandbox_url))
        self._page = page
        self._url = sandbox_url
        self._port = parsed.port
        self._requests: list[str] = []
        page.on("request", lambda request: self._requests.append(request.url))

    def fill(self, operations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        require_loopback_url(self._page.url, expected_port=self._port)
        before = self._counter("sandbox-mutation-count")
        submit_before = self._counter("sandbox-submit-count")
        filled: list[str] = []
        try:
            for row_index, operation in enumerate(operations):
                slot = row_index + 1
                if slot > 100:
                    raise LocalSandboxContractError(
                        "SANDBOX_MAPPING_INVALID", "sandbox bet limit exceeded", 409
                    )
                values = expected_readback([operation])[0]
                for field in FIELDS:
                    locator = self._field(slot, field)
                    if locator.count() != 1:
                        raise LocalSandboxContractError(
                            "SANDBOX_FIELD_INVALID", "fixed sandbox field missing", 409
                        )
                    value = values[field]
                    if field == "bet_type":
                        locator.select_option(str(value))
                    else:
                        locator.fill(
                            str(value) if isinstance(value, str) else _json_value(value)
                        )
                    filled.append(f"/bets/{row_index}/{field}")
            readback = self._readback(len(operations))
            if readback != expected_readback(operations):
                raise SandboxPartialFillError(
                    "sandbox readback mismatch", filled_field_pointers=filled
                )
            require_loopback_url(self._page.url, expected_port=self._port)
            submit_after = self._counter("sandbox-submit-count")
            if submit_after != submit_before:
                raise SandboxPartialFillError(
                    "submit event observed", filled_field_pointers=filled
                )
            external = sum(not _is_allowed_request(url, self._port) for url in self._requests)
            if external:
                raise SandboxPartialFillError(
                    "external request observed", filled_field_pointers=filled
                )
            return {
                "filled_field_pointers": filled,
                "readback": readback,
                "mutation_count": self._counter("sandbox-mutation-count") - before,
                "submit_event_count": submit_after - submit_before,
                "external_request_count": external,
                "current_url": self._page.url,
            }
        except SandboxPartialFillError:
            raise
        except Exception as exc:
            if self._counter_safely("sandbox-mutation-count") > before:
                raise SandboxPartialFillError(
                    "browser failed after mutation", filled_field_pointers=filled
                ) from exc
            raise

    def _readback(self, count: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row_index in range(count):
            slot = row_index + 1
            raw = {field: self._field(slot, field).input_value() for field in FIELDS}
            rows.append({
                "human_bet_id": raw["human_bet_id"],
                "bet_type": raw["bet_type"],
                "number_groups": json.loads(raw["number_groups"]),
                "multiplier": json.loads(raw["multiplier"]),
                "continuation": json.loads(raw["continuation"]),
                "special_play": json.loads(raw["special_play"]),
            })
        return rows

    def _field(self, slot: int, field: str) -> Any:
        if field not in FIELDS or not isinstance(slot, int) or not 1 <= slot <= 100:
            raise LocalSandboxContractError(
                "SANDBOX_FIELD_INVALID", "field is not allowlisted", 400
            )
        suffix = field.replace("_", "-")
        return self._page.locator(f"#sandbox-bet-{slot:03d}-{suffix}")

    def _counter(self, element_id: str) -> int:
        raw = self._page.locator(f"#{element_id}").get_attribute("data-count")
        if raw is None or not raw.isdigit():
            raise LocalSandboxContractError(
                "SANDBOX_PAGE_INVALID", "sandbox counter missing", 409
            )
        return int(raw)

    def _counter_safely(self, element_id: str) -> int:
        try:
            return self._counter(element_id)
        except Exception:
            return -1


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _is_allowed_request(url: str, port: int | None) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port == port
        and parsed.path in {LOCAL_SANDBOX_PATH, "/sandbox-fill/execute"}
        and not parsed.query
        and not parsed.fragment
    )
