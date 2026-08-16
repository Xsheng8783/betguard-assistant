from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _read_flat(path: str) -> str:
    """讀檔並壓平換行/多餘空白，避免斷言被文件重排版破壞。"""
    return " ".join(_read(path).split())


def test_release_docs_exist() -> None:
    assert (ROOT / "README.md").exists()
    # NOTE: 檔名大小寫敏感（Linux CI）；不是 docs/architecture.md
    assert (ROOT / "docs" / "ARCHITECTURE.md").exists()
    assert (ROOT / "docs" / "cli_reference.md").exists()
    assert (ROOT / "docs" / "demo_e2e.md").exists()
    assert (ROOT / "docs" / "daily_workflow.md").exists()


def test_readme_contains_safety_guarantees() -> None:
    readme = _read_flat("README.md")

    assert "cannot create an executable value authority" in readme
    assert "Human Confirmed Answer is the only Candidate value authority" in readme
    assert "auto-submit" in readme
    assert "real-site DOM capture" in readme
    assert "DOM mutation" in readme


def test_cli_reference_lists_release_commands() -> None:
    cli_reference = _read("docs/cli_reference.md")

    assert "--demo-e2e" in cli_reference
    assert "--review-report-html" in cli_reference
    assert "--batch-audit-export" in cli_reference
    assert "No real website operation" in cli_reference


def test_architecture_doc_states_no_live_site_operation() -> None:
    architecture = _read_flat("docs/ARCHITECTURE.md")

    assert "does not implement real-site capture, DOM mutation, fill, or submit" in architecture
    assert "Machine output is never executable authority" in architecture
    assert "Synthetic Read-only Browser Observation" in architecture


def test_version_consistent_across_build_sources() -> None:
    """build_info / pyproject / Inno Setup 三處版本必須一致。"""
    build_info = _read("src/betguard/build_info.py")
    iss = _read("installer/betguard-assistant.iss")
    pyproject = tomllib.loads(_read("pyproject.toml"))

    version = pyproject["project"]["version"]  # e.g. "0.5.40-beta"
    assert f'VERSION = "v{version}"' in build_info
    assert f'#define MyAppVersion "{version}"' in iss
