"""Versioned, local-only Prompt operations for B02."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.common import new_id
from ..domain.prompt_parser import (
    PARSER_VERSION,
    BilingualPrompt,
    merge_prompt_documents,
    parse_bilingual,
    serialize_prompt,
)
from .assets import AssetService
from .ports import PromptVersionRepositoryPort


@dataclass(frozen=True)
class PromptVersionDraft:
    kind: str
    language: str
    body: str
    parser_version: int = PARSER_VERSION


def extract_bilingual_versions(
    response: str, *, kind: str
) -> tuple[BilingualPrompt, list[PromptVersionDraft]]:
    parsed = parse_bilingual(response)
    return parsed, [
        PromptVersionDraft(kind=kind, language="zh", body=parsed.zh_prompt),
        PromptVersionDraft(kind=kind, language="en", body=parsed.en_prompt),
    ]


def merge_versions(content: BilingualPrompt, style: BilingualPrompt) -> list[PromptVersionDraft]:
    merged = merge_prompt_documents(content, style)
    return [
        PromptVersionDraft(
            kind="merged",
            language="zh",
            body=merged.zh_prompt,
        ),
        PromptVersionDraft(
            kind="merged",
            language="en",
            body=merged.en_prompt,
        ),
    ]


class PromptVersionService:
    """Creates immutable managed Prompt assets and their bilingual DB records."""

    def __init__(self, assets: AssetService, repository: PromptVersionRepositoryPort) -> None:
        self._assets = assets
        self._repository = repository

    def create_bilingual(
        self,
        root: Path,
        project_id: str,
        *,
        kind: str,
        bilingual: BilingualPrompt,
        request_id: str,
        parent_asset_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        text = serialize_prompt(bilingual)
        # request_id is an idempotency token, not a filesystem component.
        temporary = root / "temp" / f"prompt-{new_id()}.json"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(text, encoding="utf-8")
            asset = self._assets.register_derived(
                root,
                project_id,
                temporary,
                "prompt",
                request_id,
                parent_asset_id=parent_asset_id,
                input_asset_ids=[] if parent_asset_id is None else [parent_asset_id],
                name=f"{kind}-prompt.json",
                provenance={
                    # B01's frozen ProvenanceV1 deliberately uses a small
                    # closed vocabulary; analysis is represented by the Tool
                    # parameters rather than widening that published enum.
                    "source_kind": "tool",
                    "parameters": {"parser_version": PARSER_VERSION, "kind": kind},
                    **(provenance or {}),
                },
            )
        finally:
            temporary.unlink(missing_ok=True)
        asset_id = str(asset["id"])
        versions = [
            {
                "id": self._repository.append(
                    root / "project.sqlite3",
                    project_id=project_id,
                    asset_id=asset_id,
                    kind=kind,
                    language=language,
                    body=body,
                    parser_version=PARSER_VERSION,
                ),
                "language": language,
            }
            for language, body in (("zh", bilingual.zh_prompt), ("en", bilingual.en_prompt))
        ]
        return {"asset": asset, "versions": versions}

    def parse_asset(self, root: Path, project_id: str, prompt_asset_id: str) -> BilingualPrompt:
        _, content, mime_type, _ = self._assets.read_content(
            root, project_id, prompt_asset_id, None
        )
        if mime_type not in {"text/plain", "application/json"}:
            raise ValueError("prompt asset must be JSON text")
        return parse_bilingual(content.decode("utf-8"))

    def kind_for_asset(self, root: Path, project_id: str, prompt_asset_id: str) -> str:
        versions = self._repository.list_for_asset(
            root / "project.sqlite3",
            project_id=project_id,
            asset_id=prompt_asset_id,
        )
        kinds = {str(version["kind"]) for version in versions if version.get("kind")}
        if len(kinds) != 1:
            raise ValueError("prompt asset must have exactly one version kind")
        return next(iter(kinds))
