from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..execution.local import LocalExecutionEnv


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    source: str
    root: Path
    version_hash: str
    required_tools: tuple[str, ...]
    instructions: str | None = None
    resources: tuple[Path, ...] = ()


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    content: str
    source: str


class SkillLoader:
    """Discovers metadata first; only activation reads SKILL.md body/resources."""

    def __init__(
        self,
        env: LocalExecutionEnv,
        *,
        application_roots: tuple[Path, ...] = (),
        user_roots: tuple[Path, ...] = (),
        project_roots: tuple[Path, ...] = (),
    ) -> None:
        self.env = env
        self.roots = (
            ("application", application_roots),
            ("user", user_roots),
            ("project", project_roots),
        )
        self.diagnostics: list[str] = []
        self._skills: dict[str, Skill] = {}

    async def discover(self) -> tuple[Skill, ...]:
        self._skills.clear()
        for source, roots in self.roots:
            for root in roots:
                resolved = self.env.resolve(root)
                if not resolved.exists():
                    continue
                for file in resolved.rglob("SKILL.md"):
                    try:
                        skill = _metadata(file, source)
                    except ValueError as error:
                        self.diagnostics.append(f"{file}: {error}")
                        continue
                    if skill.name in self._skills:
                        self.diagnostics.append(
                            f"Skill {skill.name!r} from {source} overrides lower-priority source."
                        )
                    self._skills[skill.name] = skill
        return tuple(self._skills.values())

    async def activate(self, name: str, available_tools: tuple[str, ...]) -> Skill:
        skill = self._skills[name]
        missing = set(skill.required_tools) - set(available_tools)
        if missing:
            raise ValueError(
                f"Skill {name!r} requires unavailable tools: {', '.join(sorted(missing))}"
            )
        content = await self.env.read_text(skill.root / "SKILL.md")
        metadata, instructions = _frontmatter(content)
        resource_names = tuple(
            item.strip() for item in metadata.get("resources", "").split(",") if item.strip()
        )
        resources = tuple(self.env.resolve(skill.root / item) for item in resource_names)
        for resource in resources:
            if not resource.is_file():
                raise FileNotFoundError(f"Skill resource not found: {resource}")
        return Skill(
            skill.name,
            skill.description,
            skill.source,
            skill.root,
            skill.version_hash,
            skill.required_tools,
            instructions,
            resources,
        )


def render_template(template: PromptTemplate, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Prompt template variable is missing: {key}")
        return values[key]

    return re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", replace, template.content)


def _metadata(file: Path, source: str) -> Skill:
    raw = file.read_text(encoding="utf-8")
    metadata, _body = _frontmatter(raw)
    name = metadata.get("name", file.parent.name)
    description = metadata.get("description")
    if not name or not description:
        raise ValueError("SKILL.md requires name and description front matter.")
    required = tuple(
        item.strip() for item in metadata.get("required_tools", "").split(",") if item.strip()
    )
    return Skill(
        name, description, source, file.parent, hashlib.sha256(raw.encode()).hexdigest(), required
    )


def _frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---\n"):
        raise ValueError("missing YAML-style front matter")
    end = content.find("\n---", 4)
    if end < 0:
        raise ValueError("unterminated front matter")
    values: dict[str, str] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            raise ValueError("invalid front matter")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values, content[end + 4 :].strip()
