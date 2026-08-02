from __future__ import annotations

import pytest

from aipic_to_model.agent.extensions import ExtensionRegistry


class Extension:
    def __init__(
        self, extension_id: str, priority: int, log: list[str], *, fail: bool = False
    ) -> None:
        self.extension_id, self.priority, self.version, self.log, self.fail = (
            extension_id,
            priority,
            "1",
            log,
            fail,
        )

    def register(self, context) -> None:
        self.log.append(self.extension_id)
        if self.fail:
            raise RuntimeError("broken")
        context.add_lifecycle_hook("turn_end", lambda payload: {"seen": self.extension_id})

    async def close(self) -> None:
        self.log.append(f"close:{self.extension_id}")


def test_registry_orders_hooks_and_disables_a_failed_extension() -> None:
    log: list[str] = []
    registry = ExtensionRegistry()
    registry.register(
        (Extension("b", 1, log), Extension("a", 1, log), Extension("bad", 0, log, fail=True))
    )

    assert log == ["bad", "a", "b"]
    assert registry.disabled == {"bad"}
    assert "disabled" in registry.diagnostics[0]


def test_directory_extensions_require_explicit_enablement(tmp_path) -> None:
    directory = tmp_path / "extensions"
    directory.mkdir()
    module = directory / "sample.py"
    module.write_text(
        "class Extension:\n"
        "    extension_id='sample'\n    version='1'\n    priority=0\n"
        "    def register(self, context): context.add_prompt_template('sample', 'ok')\n"
        "    def close(self): return None\n"
        "extension=Extension()\n",
        encoding="utf-8",
    )
    registry = ExtensionRegistry()

    registry.load_directory(directory, enabled=False)
    assert registry.prompt_templates == {}
    registry.load_directory(directory, enabled=True)
    assert registry.prompt_templates == {"sample": "ok"}


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_ids_and_closes_in_reverse_order() -> None:
    log: list[str] = []
    registry = ExtensionRegistry()
    registry.register((Extension("one", 0, log), Extension("two", 0, log)))
    with pytest.raises(ValueError, match="Duplicate extension id"):
        registry.register((Extension("one", 0, log),))

    assert (await registry.emit("turn_end", {}))["seen"] == "two"
    await registry.close()
    assert log[-2:] == ["close:two", "close:one"]
