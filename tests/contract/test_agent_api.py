import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from aipic_to_model.agent.core.errors import ProviderError
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ImageContent,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
)
from aipic_to_model.agent.integrations.runtime import _system_prompt_for_project
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse
from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def _provider(_profile):
    return FakeProvider(
        (
            ScriptedResponse(
                (
                    ProviderEvent(ProviderEventType.MESSAGE_START),
                    ProviderEvent(
                        ProviderEventType.TEXT_DELTA, delta="reply C:\\outside Authorization: token"
                    ),
                    ProviderEvent(
                        ProviderEventType.MESSAGE_END,
                        message=AssistantMessage(
                            (TextContent("reply C:\\outside Authorization: token"),)
                        ),
                    ),
                )
            ),
        )
    )


def test_agent_system_prompt_has_a_non_optional_final_response_contract() -> None:
    prompt = _system_prompt_for_project("Custom workflow instruction.", "project-capability-id")

    assert "Custom workflow instruction." in prompt
    assert "project-capability-id" in prompt
    assert "terminal finish_reason" in prompt
    assert "always send a concise, user-facing final summary" in prompt
    assert "Never end a turn with an empty assistant message" in prompt
    assert "Chinese input receives Chinese output, English input receives English output" in prompt
    assert "call job.get_status at most once" in prompt
    assert "Image-presentation contract" in prompt
    assert "Image-tool decision contract" in prompt
    assert "ordinary generation, variants, transforms" in prompt
    assert "image.analyze_content" in prompt
    assert "image.analyze_style" in prompt
    assert "image.evaluate_3d_suitability" in prompt
    assert "toolbox.status" in prompt and "toolbox.load" in prompt


def test_existing_agent_conversation_is_upgraded_before_its_next_turn(tmp_path: Path) -> None:
    client, headers, project = _client(tmp_path)
    created = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "conversation-create"},
    )
    conversation_id = created.json()["id"]
    repository_path = tmp_path / "project" / "agent.sqlite3"
    from aipic_to_model.agent.session.sqlite import LinearSessionRepository

    repository = LinearSessionRepository(repository_path)
    repository.update_config(conversation_id, system_prompt="Legacy system prompt.")

    sent = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "Hello.",
            "request_id": "message-send",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "message-send"},
    )
    assert sent.status_code == 200
    assert "terminal finish_reason" in repository.open(conversation_id).system_prompt
    assert "Async-job contract" in repository.open(conversation_id).system_prompt


def _client(tmp_path: Path, provider_factory=_provider, *, raise_server_exceptions: bool = True):
    token, capabilities = "a" * 64, HostCapabilityStore()
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": f"Bearer {token}",
    }
    client = TestClient(
        create_app(
            token,
            capabilities,
            tmp_path / "app.sqlite3",
            agent_provider_factory=provider_factory,
        ),
        raise_server_exceptions=raise_server_exceptions,
    )
    client.app.state.test_capabilities = capabilities
    project = client.post(
        "/v1/projects",
        json={
            "name": "Agent API",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers={**headers, "X-Request-Id": "project-create"},
    ).json()
    return client, headers, project


def test_agent_conversation_contract_streams_safe_replayable_events(tmp_path: Path):
    client, headers, project = _client(tmp_path)
    skill_file = tmp_path / "project" / ".agent-skills" / "summarize" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: summarize\ndescription: Summarize safely\n---\nUse concise text."
    )
    created = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"], "system_prompt": "do not disclose Authorization: token"},
        headers={**headers, "X-Request-Id": "conversation-create"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["id"]
    assert (
        client.get(
            f"/v1/agent/conversations/{conversation_id}?project_id={project['id']}", headers=headers
        ).json()["state"]
        == "idle"
    )
    assert (
        client.get(
            f"/v1/agent/conversations/{conversation_id}/skills?project_id={project['id']}",
            headers=headers,
        ).json()["items"]
        == []
    )
    activated = client.post(
        f"/v1/agent/conversations/{conversation_id}/skills/activate",
        json={"project_id": project["id"], "name": "summarize", "request_id": "skill-activate"},
        headers={**headers, "X-Request-Id": "skill-activate"},
    )
    assert activated.status_code == 200 and activated.json()["active_skills"]
    assert client.get(
        f"/v1/agent/conversations/{conversation_id}/extensions?project_id={project['id']}",
        headers=headers,
    ).json() == {"disabled": [], "diagnostics": []}
    assert client.get("/v1/health", headers=headers).json()["agent"]["state"] == "ok"

    sent = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "hello C:\\outside Authorization: token",
            "request_id": "message-send",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "message-send"},
    )
    assert sent.status_code == 200 and sent.json()["state"] == "idle"

    events = client.get(
        f"/v1/agent/conversations/{conversation_id}/events?project_id={project['id']}",
        headers=headers,
    ).json()
    assert any(item["event_type"] == "message.delta" for item in events["items"])
    assert [item["sequence_no"] for item in events["items"]] == list(
        range(1, len(events["items"]) + 1)
    )
    assert "token" not in str(events)
    assert "C:\\outside" not in str(events)

    first_id = events["items"][1]["sequence_no"]
    replay = client.get(
        f"/v1/agent/conversations/{conversation_id}/events?project_id={project['id']}",
        headers={**headers, "Accept": "text/event-stream", "Last-Event-ID": str(first_id)},
    )
    assert replay.status_code == 200
    assert f"id: {first_id + 1}" in replay.text

    messages = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages?project_id={project['id']}",
        headers=headers,
    ).json()
    assert len(messages["items"]) == 2
    assert "C:\\outside" not in str(messages)
    assert "Authorization" not in str(messages)

    latest = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages?project_id={project['id']}&limit=1",
        headers=headers,
    ).json()
    assert [item["role"] for item in latest["items"]] == ["assistant"]
    assert latest["has_more"] is True
    assert latest["next_before"] == 2
    assert latest["event_cursor"] == events["next_cursor"]

    earlier = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages"
        f"?project_id={project['id']}&limit=1&before={latest['next_before']}",
        headers=headers,
    ).json()
    assert [item["role"] for item in earlier["items"]] == ["user"]
    assert earlier["has_more"] is False
    assert earlier["next_before"] is None


def test_agent_conversation_list_restores_the_latest_nonempty_transcript(tmp_path: Path):
    client, headers, project = _client(tmp_path)
    older = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "older-create"},
    ).json()["id"]
    sent = client.post(
        f"/v1/agent/conversations/{older}/messages",
        json={
            "project_id": project["id"],
            "content": "keep this conversation",
            "request_id": "older-message",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "older-message"},
    )
    assert sent.status_code == 200
    newer_empty = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "newer-create"},
    ).json()["id"]

    listed = client.get(f"/v1/agent/conversations?project_id={project['id']}", headers=headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [item["id"] for item in items] == [older]
    assert items[0]["message_count"] == 2
    assert items[0]["preview"] == "keep this conversation"
    assert items[0]["created_at"] and items[0]["updated_at"]
    assert newer_empty not in {item["id"] for item in items}


def test_agent_image_attachment_is_managed_persisted_and_hidden_from_display_content(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        (
            ScriptedResponse(
                (
                    ProviderEvent(ProviderEventType.MESSAGE_START),
                    ProviderEvent(
                        ProviderEventType.TEXT_DELTA,
                        delta=(
                            '{"goal":"Use both reference images","deliverables":[],'
                            '"constraints":[],"acceptance_criteria":[],"assumptions":[],'
                            '"blocking_questions":[],"next_action":"respond","steps":[]}'
                        ),
                    ),
                    ProviderEvent(ProviderEventType.MESSAGE_END),
                )
            ),
            ScriptedResponse(
                (
                    ProviderEvent(ProviderEventType.MESSAGE_START),
                    ProviderEvent(
                        ProviderEventType.MESSAGE_END,
                        message=AssistantMessage((TextContent("I can use the attached image."),)),
                    ),
                )
            ),
        )
    )
    client, headers, project = _client(tmp_path, lambda _profile: provider)
    image_path = tmp_path / "managed-reference.png"
    Image.new("RGB", (8, 6), "navy").save(image_path)
    imported = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={
            "file_capability_id": client.app.state.test_capabilities.issue(
                image_path, "import", project["id"]
            ),
            "asset_type": "source_image",
            "request_id": "image-import",
        },
        headers={**headers, "X-Request-Id": "image-import"},
    )
    assert imported.status_code == 200
    asset = imported.json()
    second_image_path = tmp_path / "managed-detail.webp"
    Image.new("RGB", (6, 8), "orange").save(second_image_path)
    second_asset = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={
            "file_capability_id": client.app.state.test_capabilities.issue(
                second_image_path, "import", project["id"]
            ),
            "asset_type": "source_image",
            "request_id": "second-image-import",
        },
        headers={**headers, "X-Request-Id": "second-image-import"},
    ).json()
    conversation_id = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "conversation-create"},
    ).json()["id"]

    sent = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "Use both reference images.",
            "asset_refs": [asset["id"], second_asset["id"]],
            "request_id": "message-with-image",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "message-with-image"},
    )
    assert sent.status_code == 200

    provider_user = next(
        message for message in reversed(provider.requests[-1].messages) if message.role == "user"
    )
    assert isinstance(provider_user.content, tuple)
    request_text = "\n".join(
        item.text for item in provider_user.content if isinstance(item, TextContent)
    )
    request_images = [item for item in provider_user.content if isinstance(item, ImageContent)]
    assert f'source_asset_ref="{asset["id"]}"' in request_text
    assert f'source_asset_ref="{second_asset["id"]}"' in request_text
    assert str(image_path) not in request_text
    assert str(second_image_path) not in request_text
    assert [item.mime_type for item in request_images] == ["image/png", "image/webp"]
    assert base64.b64decode(request_images[0].data) == image_path.read_bytes()
    assert base64.b64decode(request_images[1].data) == second_image_path.read_bytes()

    messages = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    assert messages[0]["content"] == "Use both reference images."
    assert "source_asset_ref" not in messages[0]["content"]
    assert messages[0]["attachments"] == [
        {
            "asset_id": asset["id"],
            "name": "managed-reference.png",
            "mime_type": "image/png",
        },
        {
            "asset_id": second_asset["id"],
            "name": "managed-detail.webp",
            "mime_type": "image/webp",
        },
    ]
    conversations = client.get(
        f"/v1/agent/conversations?project_id={project['id']}", headers=headers
    ).json()["items"]
    assert conversations[0]["preview"] == "Use both reference images."

    persisted = (tmp_path / "project" / "agent.sqlite3").read_bytes()
    assert request_images[0].data.encode() not in persisted
    assert request_images[1].data.encode() not in persisted
    event_payload = client.get(
        f"/v1/agent/conversations/{conversation_id}/events?project_id={project['id']}",
        headers=headers,
    ).content
    assert request_images[0].data.encode() not in event_payload
    assert request_images[1].data.encode() not in event_payload


def test_text_only_agent_receives_managed_reference_and_image_tool(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        (
            ScriptedResponse(
                (
                    ProviderEvent(ProviderEventType.MESSAGE_START),
                    ProviderEvent(
                        ProviderEventType.TEXT_DELTA,
                        delta=(
                            '{"goal":"Inspect the managed reference image","deliverables":[],'
                            '"constraints":[],"acceptance_criteria":[],"assumptions":[],'
                            '"blocking_questions":[],"next_action":"respond","steps":[]}'
                        ),
                    ),
                    ProviderEvent(ProviderEventType.MESSAGE_END),
                )
            ),
            ScriptedResponse(
                (
                    ProviderEvent(ProviderEventType.MESSAGE_START),
                    ProviderEvent(
                        ProviderEventType.MESSAGE_END,
                        message=AssistantMessage((TextContent("I inspected it with the tool."),)),
                    ),
                )
            ),
        )
    )
    client, headers, project = _client(tmp_path, lambda _profile: provider)
    image_path = tmp_path / "text-only-reference.png"
    Image.new("RGB", (8, 6), "navy").save(image_path)
    imported = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={
            "file_capability_id": client.app.state.test_capabilities.issue(
                image_path, "import", project["id"]
            ),
            "asset_type": "source_image",
            "request_id": "text-only-image-import",
        },
        headers={**headers, "X-Request-Id": "text-only-image-import"},
    ).json()
    conversation_id = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"], "model": "deepseek-v4-flash"},
        headers={**headers, "X-Request-Id": "text-only-conversation-create"},
    ).json()["id"]

    sent = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "What is shown in this image?",
            "asset_refs": [imported["id"]],
            "request_id": "text-only-message-with-image",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "text-only-message-with-image"},
    )
    assert sent.status_code == 200

    request = provider.requests[-1]
    provider_user = next(
        message for message in reversed(request.messages) if message.role == "user"
    )
    assert isinstance(provider_user.content, str)
    assert f'source_asset_ref="{imported["id"]}"' in provider_user.content
    assert "must call image.understand_for_agent" in provider_user.content
    assert str(image_path) not in provider_user.content
    assert "image.understand_for_agent" in {
        str(tool["function"]["name"])
        for tool in request.tools
        if isinstance(tool.get("function"), dict)
    }

    messages = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    assert messages[0]["content"] == "What is shown in this image?"
    assert messages[0]["attachments"] == [
        {
            "asset_id": imported["id"],
            "name": "text-only-reference.png",
            "mime_type": "image/png",
        }
    ]


def test_agent_rejects_a_non_image_attachment(tmp_path: Path) -> None:
    client, headers, project = _client(tmp_path)
    glb_path = tmp_path / "model.glb"
    glb_path.write_bytes(b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little"))
    imported = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={
            "file_capability_id": client.app.state.test_capabilities.issue(
                glb_path, "import", project["id"]
            ),
            "asset_type": "glb",
            "request_id": "glb-import",
        },
        headers={**headers, "X-Request-Id": "glb-import"},
    ).json()
    conversation_id = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "conversation-create"},
    ).json()["id"]

    response = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "Use this.",
            "asset_refs": [imported["id"]],
            "request_id": "message-with-model",
        },
        headers={**headers, "X-Request-Id": "message-with-model"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_AGENT_ATTACHMENT"


def test_agent_failure_after_a_tool_persists_a_terminal_assistant_message(tmp_path: Path):
    def failing_provider(_profile):
        return FakeProvider(
            (
                ScriptedResponse(
                    (
                        ProviderEvent(ProviderEventType.MESSAGE_START),
                        ProviderEvent(
                            ProviderEventType.MESSAGE_END,
                            message=AssistantMessage(
                                (ToolCall("call-1", "bash", {"command": "echo completed"}),),
                                stop_reason="tool_use",
                            ),
                        ),
                    )
                ),
                ScriptedResponse(
                    (
                        ProviderEvent(ProviderEventType.MESSAGE_START),
                        ProviderEvent(
                            ProviderEventType.PROVIDER_ERROR,
                            error_message="The provider stopped while generating the final answer.",
                        ),
                    )
                ),
            )
        )

    client, headers, project = _client(tmp_path, failing_provider, raise_server_exceptions=False)
    conversation_id = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "conversation-create"},
    ).json()["id"]
    sent = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "Run a tool and summarize it.",
            "request_id": "message-send",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "message-send"},
    )
    assert sent.status_code == 500

    messages = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["stop_reason"] == "error"
    assert "could not finish the final response" in messages[-1]["content"][0]["text"]

    events = client.get(
        f"/v1/agent/conversations/{conversation_id}/events?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    assert events[-2]["event_type"] == "message.completed"
    assert events[-1]["event_type"] == "conversation.failed"
    assert events[-1]["payload"]["code"] == "provider_error"
    status = client.get(
        f"/v1/agent/conversations/{conversation_id}?project_id={project['id']}", headers=headers
    ).json()
    assert status["state"] == "error"
    assert status["error_code"] == "provider_error"


def test_agent_failure_exposes_only_an_allowlisted_provider_reason(tmp_path: Path):
    def failing_provider(_profile):
        class ResourceFailureProvider:
            async def stream(self, _request, _cancellation):
                raise ProviderError(
                    "Provider request failed (500).",
                    retryable=True,
                    status_code=500,
                    error_code="resource_exhausted",
                    unsafe="C:\\secret\\model",
                )
                yield

        return ResourceFailureProvider()

    client, headers, project = _client(tmp_path, failing_provider, raise_server_exceptions=False)
    conversation_id = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "resource-conversation"},
    ).json()["id"]
    response = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "Inspect this image.",
            "request_id": "resource-message",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "resource-message"},
    )
    assert response.status_code == 500

    events = client.get(
        f"/v1/agent/conversations/{conversation_id}/events?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    payload = events[-1]["payload"]
    assert payload == {
        "conversation_id": conversation_id,
        "code": "provider_error",
        "reason": "resource_exhausted",
    }
    assert "secret" not in json.dumps(events)


def test_empty_final_assistant_message_is_not_reported_as_completed(tmp_path: Path):
    def empty_reply_provider(_profile):
        return FakeProvider(
            (
                ScriptedResponse(
                    (
                        ProviderEvent(ProviderEventType.MESSAGE_START),
                        ProviderEvent(
                            ProviderEventType.MESSAGE_END,
                            message=AssistantMessage(()),
                        ),
                    )
                ),
            )
        )

    client, headers, project = _client(
        tmp_path, empty_reply_provider, raise_server_exceptions=False
    )
    conversation_id = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "conversation-create"},
    ).json()["id"]
    sent = client.post(
        f"/v1/agent/conversations/{conversation_id}/messages",
        json={
            "project_id": project["id"],
            "content": "Give me a final answer.",
            "request_id": "message-send",
            "wait": True,
        },
        headers={**headers, "X-Request-Id": "message-send"},
    )
    assert sent.status_code == 500
    messages = client.get(
        f"/v1/agent/conversations/{conversation_id}/messages?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    assert "could not finish the final response" in messages[-1]["content"][0]["text"]
    events = client.get(
        f"/v1/agent/conversations/{conversation_id}/events?project_id={project['id']}",
        headers=headers,
    ).json()["items"]
    assert events[-1]["event_type"] == "conversation.failed"
    assert events[-1]["payload"]["code"] == "empty_final_response"
    status = client.get(
        f"/v1/agent/conversations/{conversation_id}?project_id={project['id']}", headers=headers
    ).json()
    assert status["state"] == "error"
    assert status["error_code"] == "empty_final_response"


def test_agent_endpoints_require_origin_and_bearer_authentication(tmp_path: Path):
    client, _headers, project = _client(tmp_path)
    response = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={"X-Request-Id": "unauthorized"},
    )
    assert response.status_code == 403
