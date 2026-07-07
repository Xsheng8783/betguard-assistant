"""Betguard local web workbench (v1).

Pure-stdlib HTTP server providing a small dashboard and a batch-creation
form.  Intentionally narrow scope: it only runs the existing
``--new-batch-from-file`` and ``--review-report-html`` CLI flows.  It never
calls ``--batch-review-accept-valid`` / ``--real-site-assisted-fill`` /
``--batch-mock-next`` / ``--batch-human-confirm-current-done``.

See ``docs/webui_workbench.md`` for the user-facing manual.
"""
from __future__ import annotations

__all__ = ["main", "build_workbench_handler"]
