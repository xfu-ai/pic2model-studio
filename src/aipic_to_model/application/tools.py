from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from ..domain.common import (
    DomainErrorV1,
    ErrorCode,
    RiskLevel,
    canonical_json,
    idempotency_key,
    new_id,
)
from ..domain.tools import JobRefV1, ToolManifestV1, ToolResultV1
from .ports import FilesystemPort, ToolRepositoryPort


class JobSubmitter(Protocol):
    def submit(self, project_id: str, job_type: str, arguments: dict[str, Any]) -> JobRefV1: ...


class InMemoryJobSubmitter:
    """Deterministic local queue reference used by B01 composition only."""

    def submit(self, project_id: str, job_type: str, arguments: dict[str, Any]) -> JobRefV1:
        del project_id, arguments
        return JobRefV1(
            job_id=new_id(),
            status="queued",
            job_type=job_type,
            stage="queued",
            elapsed_seconds=0,
            provider="local",
            can_cancel=False,
            can_stop_waiting=True,
        )


class ToolRegistry:
    def __init__(self, repository: ToolRepositoryPort, filesystem: FilesystemPort) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self.manifests: dict[tuple[str, str], ToolManifestV1] = {}
        self.executors: dict[str, Callable[..., ToolResultV1]] = {}
        self._request_policy: Any | None = None

    def set_request_policy(self, policy: Any) -> None:
        if self._request_policy is not None:
            raise ValueError("Tool request policy is already configured")
        self._request_policy = policy

    def register(self, manifest: ToolManifestV1, executor: Callable[..., ToolResultV1]) -> None:
        key = (manifest.name, manifest.version)
        if (
            key in self.manifests
            or manifest.executor_key in self.executors
            or manifest.execution not in {"sync", "job"}
            or manifest.risk_level not in RiskLevel
        ):
            raise ValueError("invalid manifest")
        Draft202012Validator.check_schema(manifest.input_schema)
        Draft202012Validator.check_schema(manifest.output_schema)
        self.manifests[key] = manifest
        self.executors[manifest.executor_key] = executor

    def visible(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        available_assets = set(context.get("asset_types", []))
        read_only = bool(context.get("project_read_only", False))
        providers = set(context.get("available_provider_profiles", []))
        approved = bool(context.get("external_approved", False))
        visible: list[dict[str, Any]] = []
        for manifest in sorted(self.manifests.values(), key=lambda item: (item.name, item.version)):
            effective_risk = (
                self._request_policy.visibility_risk(manifest.name, manifest.risk_level)
                if self._request_policy is not None
                else manifest.risk_level
            )
            reason: str | None = None
            if read_only and effective_risk is not RiskLevel.READ_ONLY:
                reason = "project_read_only"
            elif manifest.allowed_asset_types and not available_assets.intersection(
                manifest.allowed_asset_types
            ):
                reason = "required_asset_type_missing"
            elif (
                effective_risk in {RiskLevel.EXTERNAL, RiskLevel.EXTERNAL_PAID}
                and not providers
            ):
                reason = "provider_unavailable"
            elif (
                effective_risk in {RiskLevel.EXTERNAL, RiskLevel.EXTERNAL_PAID}
                and not approved
            ):
                reason = "approval_required"
            visible.append(
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "available": reason is None,
                    "unavailable_reason": reason,
                }
            )
        return visible

    def execute(
        self,
        root: Any,
        project_id: str,
        name: str,
        version: str,
        arguments: dict[str, Any],
        request_id: str,
        run_id: str | None = None,
        round_index: int = 0,
        provider_profile: str | None = None,
    ) -> ToolResultV1:
        manifest = self.manifests.get((name, version))
        if manifest is None:
            raise DomainErrorV1(ErrorCode.TOOL_NOT_ALLOWED, "Tool is not registered.")
        if list(Draft202012Validator(manifest.input_schema).iter_errors(arguments)):
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "Invalid Tool arguments.")
        if self._contains_forbidden_argument(arguments):
            raise DomainErrorV1(
                ErrorCode.TOOL_ARGUMENT_INVALID,
                "Tool arguments may not contain paths, commands, URLs, or credentials.",
            )
        request_arguments = dict(arguments)
        argument_profile = arguments.get("provider_profile")
        if argument_profile is not None and not isinstance(argument_profile, str):
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "Invalid provider profile.")
        if (
            provider_profile is not None
            and argument_profile is not None
            and provider_profile != argument_profile
        ):
            raise DomainErrorV1(
                ErrorCode.TOOL_ARGUMENT_INVALID,
                "Provider profile must be identical in the request and Tool arguments.",
            )
        # The caller-facing profile is validated before the configured policy
        # freezes a concrete execution profile.
        provider_profile = argument_profile or provider_profile
        request_provider_profile = provider_profile
        effective_risk = manifest.risk_level
        if self._request_policy is not None:
            resolved = self._request_policy.resolve(name, arguments, manifest.risk_level)
            arguments = resolved.arguments
            effective_risk = resolved.risk_level
            if list(Draft202012Validator(manifest.input_schema).iter_errors(arguments)):
                raise DomainErrorV1(
                    ErrorCode.TOOL_ARGUMENT_INVALID,
                    "Resolved Tool arguments are invalid.",
                )
            if self._contains_forbidden_argument(arguments):
                raise DomainErrorV1(
                    ErrorCode.TOOL_ARGUMENT_INVALID,
                    "Resolved Tool arguments may not contain unsafe values.",
                )
            resolved_profile = arguments.get("provider_profile")
            provider_profile = resolved_profile if isinstance(resolved_profile, str) else None
        writable = True
        try:
            self._filesystem.require_writable_root(root)
        except DomainErrorV1 as error:
            if (
                effective_risk is not RiskLevel.READ_ONLY
                or error.code != ErrorCode.PROJECT_READ_ONLY
            ):
                raise
            writable = False
        if not writable:
            result = self.executors[manifest.executor_key](root, project_id, arguments, new_id())
            self._validate_output(manifest, result)
            return result
        database = root / "project.sqlite3"
        call_id = new_id()
        request_payload_hash = hashlib.sha256(
            canonical_json(
                {
                    "project_id": project_id,
                    "tool_name": name,
                    "tool_version": version,
                    "arguments": self._filesystem.redact_structure(request_arguments),
                    "run_id": run_id,
                    "round_index": round_index,
                    "provider_profile": request_provider_profile,
                }
            ).encode("utf-8")
        ).hexdigest()

        def key_factory(
            tool_name: str,
            tool_version: str,
            tool_arguments: Mapping[str, object],
            asset_hashes: list[str],
            profile: str | None,
        ) -> str:
            key_arguments: Mapping[str, object] = tool_arguments
            if effective_risk is RiskLevel.READ_ONLY:
                # A read reflects mutable project state.  The tool_requests row still
                # replays a transport retry with the same request_id, but a later
                # request must execute again rather than return an old inventory.
                key_arguments = {
                    **tool_arguments,
                    "__request_id": request_id,
                }
            elif effective_risk is RiskLevel.EXTERNAL_PAID:
                # A request ID identifies one explicit, approved submission
                # intent. Replaying that same request remains idempotent, but
                # a later user click is a new intent and must create a new Job.
                key_arguments = {
                    **tool_arguments,
                    "__submission_request_id": request_id,
                }
            elif tool_name in {
                "job.get_status",
                "job.retry",
                "job.confirm_new_submission",
            "model3d.get_status",
                "model3d.render_preview",
            }:
                key_arguments = {
                    **tool_arguments,
                    "__request_id": request_id,
                }
            return idempotency_key(
                tool_name,
                tool_version,
                key_arguments,
                asset_hashes,
                profile,
            )

        reservation = self._repository.reserve_committed(
            database,
            project_id=project_id,
            call_id=call_id,
            request_id=request_id,
            request_payload_hash=request_payload_hash,
            run_id=run_id,
            round_index=round_index,
            name=name,
            version=version,
            arguments_json=canonical_json(self._filesystem.redact_structure(arguments)),
            arguments=arguments,
            provider_profile=provider_profile,
            risk_level=effective_risk.value,
            input_asset_ids=self._input_asset_ids(arguments),
            key_factory=key_factory,
        )
        kind = reservation["kind"]
        if kind in {"reused", "request_reused"}:
            return ToolResultV1(**json.loads(str(reservation["result_json"])))
        if kind == "request_failed":
            payload = json.loads(str(reservation["error_json"]))
            raise DomainErrorV1(**payload)
        if kind in {"pending", "request_pending"}:
            state, existing = str(reservation["state"]), str(reservation["call_id"])
            pending = ToolResultV1(
                True,
                "queued",
                existing,
                [],
                "Existing tool call is awaiting confirmation; it was not issued again.",
                [],
                job={
                    "job_id": existing,
                    "status": "queued",
                    "job_type": name,
                    "stage": "unknown_submission" if state == "unknown_submission" else "running",
                    "elapsed_seconds": 0,
                    "provider": provider_profile or "local",
                    "can_cancel": False,
                    "can_stop_waiting": True,
                },
                reused=True,
            )
            if kind == "pending":
                self._repository.complete_request_committed(
                    database, request_id, canonical_json(pending.__dict__)
                )
            return pending
        key = str(reservation["key"])
        try:
            started = time.monotonic()
            result = self.executors[manifest.executor_key](root, project_id, arguments, call_id)
            self._validate_output(manifest, result)
            state = "succeeded" if result.status == "succeeded" else "failed_terminal"
            if result.status in {"queued", "awaiting_ui_action"}:
                state = "queued"
            elif result.status == "failed" and bool((result.error or {}).get("safe_to_retry")):
                state = "failed_retryable"
            self._repository.finish_committed(
                database,
                project_id=project_id,
                call_id=call_id,
                request_id=request_id,
                key=key,
                status=result.status,
                state=state,
                payload=canonical_json(result.__dict__),
                duration_ms=round((time.monotonic() - started) * 1000),
                output_asset_ids=result.output_asset_ids,
                ui_action=result.ui_action,
                run_id=run_id,
            )
            return result
        except Exception as error:
            unknown = effective_risk in {RiskLevel.EXTERNAL, RiskLevel.EXTERNAL_PAID}
            error_payload = (
                error.as_dict()
                if isinstance(error, DomainErrorV1)
                else {
                    "code": "TOOL_EXECUTION_FAILED",
                    "user_message": "Tool execution failed.",
                    "recoverable": False,
                }
            )
            self._repository.fail_committed(database, key, request_id, unknown, error_payload)
            raise

    @staticmethod
    def _validate_output(manifest: ToolManifestV1, result: ToolResultV1) -> None:
        if not isinstance(result, ToolResultV1):
            raise TypeError("invalid tool output")
        if list(Draft202012Validator(manifest.output_schema).iter_errors(result.__dict__)):
            raise ValueError("output schema invalid")

    @staticmethod
    def _input_asset_ids(arguments: dict[str, Any]) -> list[str]:
        found: list[str] = []

        def visit(value: Any, key: str | None = None) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    visit(child_value, child_key)
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif (
                isinstance(value, str)
                and key
                and (key == "asset_id" or key.endswith(("_asset_id", "_asset_ids")))
            ):
                found.append(value)

        visit(arguments)
        return list(dict.fromkeys(found))

    @staticmethod
    def _contains_forbidden_argument(arguments: dict[str, Any]) -> bool:
        forbidden = {
            "path",
            "url",
            "uri",
            "command",
            "authorization",
            "api_key",
            "password",
            "signed_url",
            "presigned_url",
            "download_url",
        }

        def visit(value: Any, key: str | None = None) -> bool:
            if key is not None:
                lowered = key.lower()
                if lowered in forbidden or lowered.endswith(("_path", "_url", "_uri")):
                    return True
            if isinstance(value, dict):
                return any(visit(child, str(child_key)) for child_key, child in value.items())
            if isinstance(value, list):
                return any(visit(child, key) for child in value)
            return isinstance(value, str) and value.startswith(("http://", "https://", "/", "\\\\"))

        return visit(arguments)
