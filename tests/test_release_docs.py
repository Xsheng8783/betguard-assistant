from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_release_docs_exist() -> None:
    assert (ROOT / "README.md").exists()
    assert (ROOT / "docs" / "architecture.md").exists()
    assert (ROOT / "docs" / "cli_reference.md").exists()
    assert (ROOT / "docs" / "demo_e2e.md").exists()
    assert (ROOT / "docs" / "daily_workflow.md").exists()


def test_readme_contains_safety_guarantees() -> None:
    readme = _read("README.md")

    assert "Safety Guarantees" in readme
    assert "real_site_operation=false" in readme
    assert "auto_submit=false" in readme
    assert "danger_buttons_clicked=[]" in readme
    assert "No submit" in readme


def test_cli_reference_lists_release_commands() -> None:
    cli_reference = _read("docs/cli_reference.md")

    assert "--demo-e2e" in cli_reference
    assert "--review-report-html" in cli_reference
    assert "--batch-audit-export" in cli_reference
    assert "No real website operation" in cli_reference


def test_architecture_doc_states_no_live_site_operation() -> None:
    architecture = _read("docs/architecture.md")

    assert "No live site operation" in architecture
    assert "No submit" in architecture
    assert "Readonly snapshot code must remain read-only" in architecture
