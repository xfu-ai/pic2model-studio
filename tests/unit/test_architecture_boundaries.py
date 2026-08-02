"""B01-03 regression checks for the application/infrastructure boundary."""

from __future__ import annotations

import ast
from pathlib import Path

APPLICATION = Path(__file__).parents[2] / "src" / "aipic_to_model" / "application"
AGENT_CORE_AND_HARNESS = Path(__file__).parents[2] / "src" / "aipic_to_model" / "agent"


def test_application_does_not_import_infrastructure_adapters() -> None:
    violations: list[str] = []
    for path in APPLICATION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = getattr(node, "module", "") or ""
            if "infrastructure" in module:
                violations.append(f"{path.name}:{module}")
    assert not violations, violations


def test_application_services_do_not_issue_sql() -> None:
    violations: list[str] = []
    for path in APPLICATION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "execute":
                continue
            # A frozen application-level cross-file state-machine call is not
            # SQLite SQL.  All other execute calls are forbidden here.
            if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_operations":
                continue
            violations.append(f"{path.name}:{node.lineno}")
    assert not violations, violations


def test_application_does_not_own_sqlite_lifecycle_or_dynamic_repository_locator() -> None:
    violations = [
        path.name
        for path in APPLICATION.glob("*.py")
        if "connect(" in path.read_text(encoding="utf-8")
        or "transaction(" in path.read_text(encoding="utf-8")
        or "import sqlite3" in path.read_text(encoding="utf-8")
    ]
    assert not violations, violations


def test_ports_expose_no_string_keyed_repository_or_connection_lifecycle() -> None:
    ports = (APPLICATION / "ports.py").read_text(encoding="utf-8")
    forbidden = (
        "def connect(",
        "def transaction(",
        "def repository(",
        "repository_class",
        "_RepositoryPort",
        "_runtime",
        "def runtime(",
        "def register_runtime(",
        "-> object",
    )
    assert not [item for item in forbidden if item in ports]


def test_application_uses_explicit_ports_not_dynamic_any_dispatch() -> None:
    violations: list[str] = []
    for path in APPLICATION.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "cast(Any" in text or "type: ignore[attr-defined]" in text:
            violations.append(path.name)
    assert not violations, violations


def test_core_repository_ports_have_named_command_signatures() -> None:
    ports = (APPLICATION / "ports.py").read_text(encoding="utf-8")
    core_sections = (
        "class AssetRepositoryPort",
        "class SelectionRepositoryPort",
        "class ToolRepositoryPort",
    )
    for section in core_sections:
        body = ports[ports.index(section) :]
        next_class = body.find("\n\nclass ", len(section))
        if next_class >= 0:
            body = body[:next_class]
        assert "**kwargs" not in body
        assert "-> Any" not in body


def test_agent_core_and_harness_do_not_branch_on_provider_names() -> None:
    provider_names = (
        "anthropic",
        "bedrock",
        "cloudflare",
        "deepseek",
        "google",
        "mistral",
        "openai",
        "openrouter",
        "radius",
    )
    violations: list[str] = []
    for directory in (AGENT_CORE_AND_HARNESS / "core", AGENT_CORE_AND_HARNESS / "harness"):
        for path in directory.glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            if any(name in text for name in provider_names):
                violations.append(path.name)
    assert not violations, violations
