"""Deterministic extension registration without ambient process capabilities."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from ..core.models import Message
from ..core.tool import AgentTool
from ..providers.base import AgentModelProvider

LifecycleHook = Callable[
    [dict[str, object]], Awaitable[dict[str, object] | None] | dict[str, object] | None
]
ContextTransform = Callable[[tuple[Message, ...]], tuple[Message, ...]]
CustomMessageProjector = Callable[[dict[str, object]], tuple[Message, ...]]


class AgentExtension(Protocol):
    extension_id: str
    version: str
    priority: int

    def register(self, context: ExtensionContext) -> None: ...

    def close(self) -> Awaitable[None] | None: ...


@dataclass
class ExtensionContext:
    extension_id: str
    _registry: ExtensionRegistry

    def add_tool(self, tool: AgentTool) -> None:
        self._registry._add_tool(self.extension_id, tool)

    def add_provider(self, provider_id: str, provider: AgentModelProvider) -> None:
        self._registry._add_provider(self.extension_id, provider_id, provider)

    def add_skill_root(self, path: str) -> None:
        self._registry._skill_roots.append(path)

    def add_prompt_template(self, name: str, template: str) -> None:
        self._registry._add_prompt_template(self.extension_id, name, template)

    def add_context_transform(self, transform: ContextTransform) -> None:
        self._registry._context_transforms.append((self.extension_id, transform))

    def add_lifecycle_hook(self, name: str, hook: LifecycleHook) -> None:
        self._registry._hooks[name].append((self.extension_id, hook))

    def add_custom_message_projector(self, name: str, projector: CustomMessageProjector) -> None:
        self._registry._add_projector(self.extension_id, name, projector)


@dataclass
class ExtensionRegistry:
    """Owns registered extension resources in a stable, auditable ordering."""

    _extensions: list[AgentExtension] = field(default_factory=list)
    _tools: dict[str, AgentTool] = field(default_factory=dict)
    _tool_owners: dict[str, str] = field(default_factory=dict)
    _providers: dict[str, AgentModelProvider] = field(default_factory=dict)
    _skill_roots: list[str] = field(default_factory=list)
    _templates: dict[str, str] = field(default_factory=dict)
    _context_transforms: list[tuple[str, ContextTransform]] = field(default_factory=list)
    _hooks: dict[str, list[tuple[str, LifecycleHook]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _projectors: dict[str, CustomMessageProjector] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    disabled: set[str] = field(default_factory=set)

    def register(self, extensions: tuple[AgentExtension, ...]) -> None:
        seen = {extension.extension_id for extension in self._extensions}
        ordered = sorted(
            enumerate(extensions),
            key=lambda item: (item[1].priority, item[1].extension_id, item[0]),
        )
        for _order, extension in ordered:
            if not extension.extension_id or extension.extension_id in seen:
                raise ValueError(f"Duplicate extension id: {extension.extension_id!r}")
            seen.add(extension.extension_id)
            try:
                extension.register(ExtensionContext(extension.extension_id, self))
            except Exception as error:  # noqa: BLE001 - failed trusted code is disabled, not retried.
                self.disabled.add(extension.extension_id)
                self.diagnostics.append(f"Extension {extension.extension_id} disabled: {error}")
                continue
            self._extensions.append(extension)

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        return tuple(
            tool
            for name, tool in self._tools.items()
            if self._tool_owners.get(name) not in self.disabled
        )

    @property
    def providers(self) -> dict[str, AgentModelProvider]:
        return dict(self._providers)

    @property
    def skill_roots(self) -> tuple[str, ...]:
        return tuple(self._skill_roots)

    @property
    def prompt_templates(self) -> dict[str, str]:
        return dict(self._templates)

    def load_builtin_module(self, module_name: str) -> None:
        module = importlib.import_module(module_name)
        self.register((_extension_from_module(module, module_name),))

    def load_directory(self, directory: Path, *, enabled: bool) -> None:
        """Load only explicitly enabled trusted local Python extensions."""

        if not enabled:
            return
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        for path in sorted(directory.glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"aipic_extension_{path.stem}", path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load extension module: {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.register((_extension_from_module(module, str(path)),))

    def transform_context(self, messages: tuple[Message, ...]) -> tuple[Message, ...]:
        value = messages
        for extension_id, transform in self._context_transforms:
            if extension_id not in self.disabled:
                value = transform(value)
        return value

    async def emit(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        current = dict(payload)
        for extension_id, hook in self._hooks.get(name, []):
            if extension_id in self.disabled:
                continue
            try:
                result = hook(dict(current))
                patch = (
                    await result
                    if asyncio.iscoroutine(result)
                    else cast(dict[str, object] | None, result)
                )
                if patch is not None:
                    current.update(patch)
            except Exception as error:  # noqa: BLE001
                self.disabled.add(extension_id)
                self.diagnostics.append(f"Extension {extension_id} disabled: {error}")
        return current

    async def close(self) -> None:
        for extension in reversed(self._extensions):
            try:
                result = extension.close()
                if result is not None:
                    await result
            except Exception as error:  # noqa: BLE001
                self.diagnostics.append(f"Extension {extension.extension_id} close failed: {error}")

    def _add_tool(self, extension_id: str, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool id: {tool.name!r}")
        self._tools[tool.name] = tool
        self._tool_owners[tool.name] = extension_id

    def _add_provider(
        self, extension_id: str, provider_id: str, provider: AgentModelProvider
    ) -> None:
        del extension_id
        if provider_id in self._providers:
            raise ValueError(f"Duplicate provider id: {provider_id!r}")
        self._providers[provider_id] = provider

    def _add_prompt_template(self, extension_id: str, name: str, template: str) -> None:
        del extension_id
        if name in self._templates:
            raise ValueError(f"Duplicate prompt template: {name!r}")
        self._templates[name] = template

    def _add_projector(
        self, extension_id: str, name: str, projector: CustomMessageProjector
    ) -> None:
        del extension_id
        if name in self._projectors:
            raise ValueError(f"Duplicate custom message projector: {name!r}")
        self._projectors[name] = projector


def _extension_from_module(module: object, source: str) -> AgentExtension:
    extension = getattr(module, "extension", None)
    if extension is None:
        raise ValueError(f"Extension module {source!r} must export 'extension'.")
    return cast(AgentExtension, extension)
