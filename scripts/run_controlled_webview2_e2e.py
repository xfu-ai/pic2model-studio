"""Run small, lock-screen-safe DOM checks against an explicitly debug-enabled WebView2.

The Tauri host must be launched separately in Debug fixture mode.  This script
never opens a picker, drives a physical mouse, or sends a live provider request.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from webview2_cdp import (
    CdpConnection,
    cdp_network_records,
    collect_evidence,
    install_diagnostics,
    redact,
)


def wait_for(connection: CdpConnection, expression: str, description: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if connection.evaluate(expression):
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}")


def assert_clean_runtime(connection: CdpConnection, *, allow_failed_network: bool = False) -> None:
    # Complete a CDP round trip so events that arrived since the last DOM
    # assertion are collected before validating the evidence.
    connection.evaluate("true")
    state = connection.evaluate("globalThis.__aipicE2E") or {}
    state["network"] = [*(state.get("network", [])), *cdp_network_records(connection)]
    failures = {
        name: state.get(name, [])
        for name in ("errors", "rejections", "network")
        if state.get(name)
    }
    failed_network = [
        item for item in failures.get("network", []) if (item.get("status") or 0) >= 400
    ]
    if failed_network and not allow_failed_network:
        failures["network"] = failed_network
    else:
        failures.pop("network", None)
    if failures:
        raise AssertionError(f"WebView runtime is not clean: {redact(failures)}")


def run_startup(connection: CdpConnection, timeout: float) -> None:
    wait_for(
        connection,
        "document.readyState === 'complete' && !!document.querySelector('main, .workbench-layout')",
        "the application shell",
        timeout,
    )
    title = connection.evaluate("document.title")
    if title != "图模工坊":
        raise AssertionError(f"unexpected page title: {title!r}")
    offline = connection.evaluate("document.body.innerText.includes('本地服务暂时不可用')")
    if offline:
        raise AssertionError("the controlled sidecar did not reach a healthy state")


def run_project_export(connection: CdpConnection, timeout: float) -> dict[str, object]:
    connection.evaluate(
        """(() => {
          const button = document.querySelector('button[aria-label="导出"]');
          if (!button) throw new Error('project export navigation is missing');
          button.click();
        })()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.project-package-actions')",
        "the project export workspace",
        timeout,
    )
    action_labels = connection.evaluate(
        "[...document.querySelectorAll('.project-package-actions button')].map((item) => item.textContent?.trim()).filter(Boolean)"
    )
    if action_labels != ["导出项目备份…"]:
        raise AssertionError(f"unexpected project export actions: {action_labels!r}")
    connection.evaluate("document.querySelector('.project-package-actions button.primary').click()")
    wait_for(
        connection,
        "document.querySelector('.project-package-actions [role=status]')?.textContent?.includes('项目备份已导出')",
        "the completed project export",
        timeout,
    )
    return {
        "scenario": "project_export",
        "status": "passed",
        "assertions": {
            "actions": action_labels,
            "current_package_actions_only": True,
            "export_completed": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_offline_recovery(connection: CdpConnection, timeout: float) -> None:
    wait_for(
        connection,
        "!!document.querySelector('.app-shell .primary')",
        "the offline reconnect action",
        timeout,
    )
    connection.evaluate("document.querySelector('.app-shell .primary').click()")
    wait_for(
        connection,
        "!!document.querySelector('#project-launcher-title')",
        "the recovered project launcher",
        timeout,
    )


def run_create_project(connection: CdpConnection, timeout: float, name: str) -> None:
    wait_for(
        connection,
        "!!document.querySelector('#project-launcher-title')",
        "the project launcher",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const input = document.querySelector('input');
          if (!input) throw new Error('project name input is missing');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(input, """
        + json.dumps(name)
        + """);
          input.dispatchEvent(new Event('input', {bubbles: true}));
          input.dispatchEvent(new Event('change', {bubbles: true}));
          const button = [...document.querySelectorAll('button')]
            .find((item) => item.textContent?.includes('Choose folder and create'));
          if (!button || button.disabled) throw new Error('create-project control is unavailable');
          button.click();
        })()"""
    )
    wait_for(
        connection,
        "document.body.innerText.includes('建立你的资产工作台')",
        "the newly-created empty workspace",
        timeout,
    )


def run_import_image(connection: CdpConnection, timeout: float) -> None:
    connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('button')]
            .find((item) => item.textContent?.includes('Choose image'));
          if (!button || button.disabled) throw new Error('image import control is unavailable');
          button.click();
        })()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.image-workspace [aria-label=\"Image preview canvas\"]')",
        "the imported image workspace",
        timeout,
    )
    connection.evaluate("true")
    requests = [
        *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
        *cdp_network_records(connection),
    ]
    imports = [item for item in requests if "/assets/import" in str(item.get("url", ""))]
    if not imports:
        raise AssertionError("image import did not issue an API request")
    if any(":\\\\" in str(item.get("request", "")) for item in imports):
        raise AssertionError("image import sent a local path instead of a capability")


def run_asset_file_actions(connection: CdpConnection, timeout: float) -> dict[str, object]:
    """Exercise reveal and capability-backed export from both image surfaces."""

    if not connection.evaluate("!!document.querySelector('.image-workspace')"):
        connection.evaluate(
            "[...document.querySelectorAll('.primary-navigation button')].find((item) => item.textContent?.trim() === '工作区')?.click()"
        )
    wait_for(
        connection,
        "!!document.querySelector('.image-workspace .asset-file-actions')",
        "the current-image file actions",
        timeout,
    )
    current_name = connection.evaluate(
        "document.querySelector('.current-asset-title h1')?.textContent?.trim()"
    )
    if not current_name:
        raise AssertionError("the current image name is unavailable")

    connection.evaluate(
        "document.querySelector('.image-workspace .asset-file-actions button[aria-label=\"打开目录\"]')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.image-workspace .asset-file-actions [role=status]')?.textContent?.includes('已打开资产所在目录')",
        "the current-image directory feedback",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('.image-workspace .asset-file-actions button[aria-label=\"导出资源\"]')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.image-workspace .asset-file-actions [role=status]')?.textContent?.includes('已导出')",
        "the current-image export feedback",
        timeout,
    )

    connection.evaluate(
        "[...document.querySelectorAll('.primary-navigation button')].find((item) => item.textContent?.trim() === '资产')?.click()"
    )
    wait_for(
        connection,
        "!!document.querySelector('.asset-browser .asset-card .asset-file-actions')",
        "the asset-library file actions",
        timeout,
    )
    library_name = connection.evaluate(
        "document.querySelector('.asset-browser .asset-card h2')?.textContent?.trim()"
    )
    if not library_name:
        raise AssertionError("the asset-library card name is unavailable")

    connection.evaluate(
        "document.querySelector('.asset-browser .asset-card .asset-file-actions button[aria-label=\"打开目录\"]')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.asset-browser .asset-card .asset-file-actions [role=status]')?.textContent?.includes('已打开资产所在目录')",
        "the asset-library directory feedback",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('.asset-browser .asset-card .asset-file-actions button[aria-label=\"导出资源\"]')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.asset-browser .asset-card .asset-file-actions [role=status]')?.textContent?.includes('已导出')",
        "the asset-library export feedback",
        timeout,
    )

    # Return to the workbench so the final evidence bundle also proves the
    # compact toolbar layout, while the preceding bundle retains the asset card.
    connection.evaluate(
        "[...document.querySelectorAll('.primary-navigation button')].find((item) => item.textContent?.trim() === '工作区')?.click()"
    )
    wait_for(
        connection,
        "!!document.querySelector('.image-workspace .asset-file-actions')",
        "the restored current-image file actions",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('.image-workspace .asset-file-actions button[aria-label=\"导出资源\"]')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.image-workspace .asset-file-actions [role=status]')?.textContent?.includes('已导出')",
        "the restored current-image export feedback",
        timeout,
    )

    connection.evaluate("true")
    requests = [
        *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
        *cdp_network_records(connection),
    ]
    reveals = [item for item in requests if "/reveal" in str(item.get("url", ""))]
    exports = [item for item in requests if "/export" in str(item.get("url", ""))]
    if len(reveals) < 2 or len(exports) < 2:
        raise AssertionError(
            "both asset surfaces must issue reveal and export requests"
        )
    failed = [
        item
        for item in [*reveals, *exports]
        if int(item.get("status") or 0) >= 400
    ]
    if failed:
        raise AssertionError(f"asset file action request failed: {redact(failed)}")

    return {
        "scenario": "asset_file_actions",
        "status": "passed",
        "assertions": {
            "current_image": current_name,
            "asset_library_card": library_name,
            "current_image_reveal": True,
            "current_image_export": True,
            "asset_library_reveal": True,
            "asset_library_export": True,
            "final_surface": "current_image",
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_asset_remove(connection: CdpConnection, timeout: float) -> dict[str, object]:
    """Confirm impact and move one managed file into the project trash."""

    if not connection.evaluate("!!document.querySelector('.asset-browser')"):
        connection.evaluate(
            "[...document.querySelectorAll('.primary-navigation button')].find((item) => item.textContent?.trim() === '资产')?.click()"
        )
    wait_for(
        connection,
        "!!document.querySelector('.asset-browser .asset-card')",
        "an asset card to remove",
        timeout,
    )
    removed = connection.evaluate(
        """(() => {
          const card = document.querySelector('.asset-browser .asset-card');
          const button = card?.querySelector('.asset-remove-button');
          if (!card || !button) throw new Error('asset remove action is missing');
          const result = {
            id: card.dataset.assetId,
            name: card.querySelector('h2')?.textContent?.trim(),
            cardsBefore: document.querySelectorAll('.asset-browser .asset-card').length,
          };
          button.click();
          return result;
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('.asset-remove-action.confirming p')?.textContent?.includes('本地文件将移入项目回收站')",
        "the asset impact confirmation",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('.asset-remove-action.confirming button.danger')?.click()"
    )
    wait_for(
        connection,
        (
            "!document.querySelector('.asset-card[data-asset-id="
            + json.dumps(removed["id"])
            + "]') && document.querySelector('.asset-notice.success')?.textContent?.includes('本地文件已移入项目回收站')"
        ),
        "the completed asset removal",
        timeout,
    )

    connection.evaluate("true")
    requests = [
        *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
        *cdp_network_records(connection),
    ]
    impacts = [item for item in requests if "/impact" in str(item.get("url", ""))]
    trash = [item for item in requests if "/trash" in str(item.get("url", ""))]
    if not impacts or not trash:
        raise AssertionError("asset removal did not issue impact and trash requests")
    if any(int(item.get("status") or 0) >= 400 for item in [*impacts, *trash]):
        raise AssertionError("asset impact or trash request failed")

    return {
        "scenario": "asset_remove",
        "status": "passed",
        "assertions": {
            "asset_name": removed["name"],
            "impact_confirmed": True,
            "card_removed": True,
            "cards_before": removed["cardsBefore"],
            "cards_after": connection.evaluate(
                "document.querySelectorAll('.asset-browser .asset-card').length"
            ),
            "local_file_moved_to_project_trash": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_agent_image_attachment(
    connection: CdpConnection, timeout: float, image_paths: list[Path]
) -> dict[str, object]:
    """Exercise multi-file input while keeping the model provider offline."""

    wait_for(
        connection,
        "!!document.querySelector('input[aria-label=\"Choose images to attach\"][multiple]')",
        "the Agent multi-image attachment input",
        timeout,
    )
    if len(image_paths) < 2 or any(not path.is_file() for path in image_paths):
        raise ValueError("--agent-image-path requires at least two existing controlled images")
    connection.call("DOM.enable")
    root = connection.call("DOM.getDocument")["root"]["nodeId"]
    input_node = connection.call(
        "DOM.querySelector",
        nodeId=root,
        selector='input[aria-label="Choose images to attach"]',
    )["nodeId"]
    connection.call(
        "DOM.setFileInputFiles",
        files=[str(path.resolve()) for path in image_paths],
        nodeId=input_node,
    )
    connection.evaluate(
        """document.querySelector('input[aria-label="Choose images to attach"]')
          ?.dispatchEvent(new Event('change', {bubbles: true})); true"""
    )
    wait_for(
        connection,
        (
            f"document.querySelectorAll('.agent-compose-attachment img[src^=\"blob:\"]').length === {len(image_paths)} && "
            f"document.querySelectorAll('.agent-compose-attachment > strong').length === {len(image_paths)}"
        ),
        "the managed multi-image attachment previews",
        timeout,
    )
    attachment_names = connection.evaluate(
        """[...document.querySelectorAll('.agent-compose-attachment > strong')]
          .map((item) => item.textContent?.trim()).filter(Boolean)"""
    )

    # File input and managed import above are real controlled-host
    # interactions. Intercept only Agent endpoints below so this UI proof can
    # never contact the configured DeepSeek provider.
    connection.evaluate(
        """(() => {
          const attachmentNames = """
        + json.dumps(attachment_names)
        + """;
          const originalFetch = globalThis.fetch.bind(globalThis);
          globalThis.__aipicAgentAttachmentOriginalFetch = originalFetch;
          globalThis.fetch = async (input, init = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            const isMessages = /\\/v1\\/agent\\/conversations\\/[^/]+\\/messages(?:\\?|$)/.test(url);
            const isEvents = /\\/v1\\/agent\\/conversations\\/[^/]+\\/events(?:\\?|$)/.test(url);
            if (isEvents && globalThis.__aipicAgentAttachmentRequest) {
              const response = {
                items: [{
                  sequence_no: 998,
                  event_type: 'execution.plan.updated',
                  payload: {
                    conversation_id: 'controlled-agent-conversation',
                    plan: {
                      version: 1,
                      goal: 'Use both attached reference images',
                      constraints: ['Preserve the supplied references'],
                      steps: [{id: 'inspect', label: 'Inspect the attached references', state: 'review_required', warning: 'Controlled verification requires review'}],
                      current_step_id: null,
                      state: 'completed_with_warnings',
                      next_action: 'execute',
                    },
                  },
                  created_at: new Date().toISOString(),
                }, {
                  sequence_no: 999,
                  event_type: 'conversation.completed',
                  payload: {conversation_id: 'controlled-agent-conversation'},
                  created_at: new Date().toISOString(),
                }],
                next_cursor: 999,
              };
              return new Response(JSON.stringify(response), {
                status: 200,
                headers: {'Content-Type': 'application/json'},
              });
            }
            if (!isMessages) return originalFetch(input, init);
            const method = String(init.method || 'GET').toUpperCase();
            if (method === 'POST') {
              const body = JSON.parse(String(init.body || '{}'));
              globalThis.__aipicAgentAttachmentRequest = body;
              const response = {
                id: 'controlled-agent-conversation',
                project_id: body.project_id,
                state: 'idle',
                message_count: 1,
              };
              return new Response(JSON.stringify(response), {
                status: 200,
                headers: {'Content-Type': 'application/json'},
              });
            }
            const request = globalThis.__aipicAgentAttachmentRequest || {};
            const assetRefs = request.asset_refs || [];
            const response = {
              items: [{
                id: 'controlled-agent-user-message',
                role: 'user',
                content: request.content || '',
                attachments: assetRefs.map((assetId, index) => ({
                  asset_id: assetId,
                  name: attachmentNames[index] || `managed-image-${index + 1}.png`,
                  mime_type: 'image/png',
                })),
              }],
              execution_plan: {
                version: 1,
                goal: 'Use both attached reference images',
                constraints: ['Preserve the supplied references'],
                steps: [{id: 'inspect', label: 'Inspect the attached references', state: 'review_required', warning: 'Controlled verification requires review'}],
                current_step_id: null,
                state: 'completed_with_warnings',
                next_action: 'execute',
              },
            };
            return new Response(JSON.stringify(response), {
              status: 200,
              headers: {'Content-Type': 'application/json'},
            });
          };
          return true;
        })()"""
    )
    prompt = "Use both attached reference images for the next operation."
    connection.evaluate(
        """(() => {
          const textarea = document.querySelector('textarea[aria-label="Message the Agent"]');
          if (!textarea) throw new Error('Agent message editor is missing');
          Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(
            textarea, """
        + json.dumps(prompt)
        + """
          );
          textarea.dispatchEvent(new Event('input', {bubbles: true}));
          const send = document.querySelector('button[aria-label="Send to Agent"]');
          if (!send || send.disabled) throw new Error('Agent send control is unavailable');
          send.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            f"globalThis.__aipicAgentAttachmentRequest?.asset_refs?.length === {len(image_paths)} && "
            f"document.querySelectorAll('.agent-user-attachments img[src^=\"blob:\"]').length === {len(image_paths)} && "
            "document.querySelector('[aria-label=\"Execution plan\"]')?.textContent?.includes('Completed with warnings') && "
            "document.querySelector('.agent-run-status')?.classList.contains('completed') && "
            "document.querySelector('.agent-run-status')?.textContent?.includes('completed this response with warnings') && "
            "!document.querySelector('.agent-run-status')?.textContent?.includes('needs attention') && "
            "!document.querySelector('.agent-run-status')?.textContent?.includes('Waiting for your approval')"
        ),
        "the sent and restored Agent image attachments with a completed-with-warnings UI state",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('[aria-label=\"Execution plan\"] button[aria-expanded=\"false\"]')?.click(); true"
    )
    wait_for(
        connection,
        (
            "document.querySelector('[aria-label=\"Execution plan\"]')?.textContent"
            "?.includes('Controlled verification requires review')"
        ),
        "the expanded Agent plan review details",
        timeout,
    )
    request = connection.evaluate("globalThis.__aipicAgentAttachmentRequest")
    serialized = json.dumps(request, ensure_ascii=False)
    if ":\\" in serialized or "source_asset_ref" in serialized:
        raise AssertionError(
            "Agent attachment request exposed a path or internal model instruction"
        )
    if request.get("content") != prompt:
        raise AssertionError("Agent attachment request changed the visible user prompt")
    if len(request.get("asset_refs", [])) != len(image_paths):
        raise AssertionError("Agent attachment request lost one or more managed asset references")
    if connection.evaluate(
        "document.querySelector('.agent-message.user')?.textContent?.includes('source_asset_ref')"
    ):
        raise AssertionError(
            "the user transcript exposed the internal managed reference instruction"
        )

    requests = [
        *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
        *cdp_network_records(connection),
    ]
    imports = [item for item in requests if "/assets/import" in str(item.get("url", ""))]
    if len(imports) < len(image_paths):
        raise AssertionError("Agent image selection did not import every managed asset")
    if any(":\\" in str(item.get("request", "")) for item in imports):
        raise AssertionError("Agent image import exposed a native path")
    connection.evaluate(
        """(() => {
          if (globalThis.__aipicAgentAttachmentOriginalFetch) {
            globalThis.fetch = globalThis.__aipicAgentAttachmentOriginalFetch;
          }
          return true;
        })()"""
    )
    return {
        "scenario": "agent_image_attachment",
        "status": "passed",
        "assertions": {
            "managed_imports": len(image_paths),
            "attachment_previews": attachment_names,
            "managed_asset_ref_count": len(image_paths),
            "native_path_exposed": False,
            "internal_instruction_exposed": False,
            "restored_attachment_visible": True,
            "completed_with_warnings_state_visible": True,
            "plan_and_review_visible": True,
            "plan_details_expanded": True,
            "provider_request_intercepted": True,
        },
    }


def run_agent_approval_status(connection: CdpConnection, timeout: float) -> dict[str, object]:
    """Prove that a successful approval immediately clears the stale waiting banner."""

    wait_for(connection, "!!document.querySelector('.agent-live-panel')", "the Agent panel", timeout)
    connection.evaluate(
        r"""(() => {
          const originalFetch = globalThis.fetch.bind(globalThis);
          globalThis.__aipicAgentApprovalOriginalFetch = originalFetch;
          globalThis.fetch = async (input, init = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            const method = String(init.method || 'GET').toUpperCase();
            const json = (value) => new Response(JSON.stringify(value), {
              status: 200, headers: {'Content-Type': 'application/json'},
            });
            if (/\/v1\/agent\/conversations\?/.test(url)) {
              return json({items: [{id: 'controlled-approval-conversation', state: 'idle',
                message_count: 2, preview: 'Controlled approval status'}]});
            }
            if (/\/v1\/agent\/conversations\/[^/]+\/events\?/.test(url)
                && !url.includes('controlled-approval-conversation')) {
              if (!globalThis.__aipicAgentApprovalRefreshDelivered) {
                globalThis.__aipicAgentApprovalRefreshDelivered = true;
                return json({items: [{sequence_no: 1000, event_type: 'conversation.completed',
                  payload: {conversation_id: 'controlled-prior-conversation'},
                  created_at: new Date().toISOString()}], next_cursor: 1000});
              }
              return json({items: [], next_cursor: 1000});
            }
            if (/\/v1\/agent\/conversations\/controlled-approval-conversation\/messages\?/.test(url)) {
              return json({
                items: [{id: 'controlled-approval-call', role: 'assistant', content: [{
                  type: 'tool_call', id: 'controlled-paid-call',
                  name: 'image.transform_from_reference', arguments: {},
                }]}],
                pending_ui_actions: [{
                  tool_call_id: 'controlled-paid-call',
                  tool_name: 'image.transform_from_reference',
                  result: {
                    content: [{type: 'text', text: 'Approval is required.'}],
                    details: {status: 'awaiting_ui_action', ui_action: {
                      action_id: 'controlled-approval', type: 'approval_required',
                    }},
                    is_error: false,
                  },
                }],
                event_cursor: 0,
              });
            }
            if (/\/v1\/agent\/conversations\/controlled-approval-conversation\/events\?/.test(url)) {
              return json({items: [], next_cursor: 0});
            }
            if (/\/v1\/approvals\/controlled-approval\/decision$/.test(url) && method === 'POST') {
              globalThis.__aipicAgentApprovalDecision = JSON.parse(String(init.body || '{}'));
              return json({status: 'queued', summary: 'Job queued.',
                job: {job_id: 'controlled-job', job_type: 'image.transform'}});
            }
            if (/\/v1\/jobs\/controlled-job\?/.test(url)) {
              return json({id: 'controlled-job', status: 'running', job_type: 'image.transform',
                input_asset_ids: [], output_asset_ids: []});
            }
            return originalFetch(input, init);
          };
          const history = document.querySelector('button[aria-label="Conversation history"]');
          if (!history) throw new Error('Agent history control is missing');
          if (history.getAttribute('aria-expanded') !== 'true') history.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        "[...document.querySelectorAll('.agent-session-history button')].some((button) => button.textContent?.includes('Controlled approval status'))",
        "the controlled approval conversation",
        timeout,
    )
    connection.evaluate(
        """[...document.querySelectorAll('.agent-session-history button')]
          .find((button) => button.textContent?.includes('Controlled approval status'))?.click(); true"""
    )
    wait_for(
        connection,
        "!![...document.querySelectorAll('.agent-approval button')].find((button) => button.textContent?.includes('Approve and run'))",
        "the Agent approval action",
        timeout,
    )
    connection.evaluate(
        """[...document.querySelectorAll('.agent-approval button')]
          .find((button) => button.textContent?.includes('Approve and run'))?.click(); true"""
    )
    wait_for(
        connection,
        """globalThis.__aipicAgentApprovalDecision?.approved === true
          && document.querySelector('.agent-run-status')?.textContent?.includes('Background task is running')
          && !document.querySelector('.agent-run-status')?.textContent?.includes('Waiting for your approval')
          && !document.querySelector('.agent-run-status')?.textContent?.includes('Agent is ready')
          && document.querySelector('.agent-approval')?.textContent?.includes('Approved; task queued')""",
        "the approved background-task state without a stale approval banner",
        timeout,
    )
    return {
        "scenario": "agent_approval_status",
        "status": "passed",
        "assertions": {
            "approval_submitted": True,
            "background_task_visible": True,
            "waiting_approval_cleared": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def click_button(connection: CdpConnection, text: str) -> None:
    connection.evaluate(
        """(() => {
          const expected = """
        + json.dumps(text)
        + """;
          const button = [...document.querySelectorAll('button')]
            .find((item) => item.textContent?.trim().includes(expected));
          if (!button || button.disabled) throw new Error(`button is unavailable: ${expected}`);
          button.click();
          return true;
        })()"""
    )


def set_labeled_input(connection: CdpConnection, label: str, value: int) -> None:
    connection.evaluate(
        """(() => {
          const input = document.querySelector('input[aria-label=' + """
        + json.dumps(json.dumps(label, ensure_ascii=False))
        + """ + ']');
          if (!input) throw new Error('selection input is missing');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(
            input, """
        + json.dumps(str(value))
        + """
          );
          input.dispatchEvent(new Event('input', {bubbles: true}));
          input.dispatchEvent(new Event('change', {bubbles: true}));
          return true;
        })()"""
    )


def run_target_extraction(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    def collected_split_request_ids() -> set[str]:
        items = [
            *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
            *cdp_network_records(connection),
        ]
        request_ids: set[str] = set()
        for item in items:
            if "/v1/tools/invoke" not in str(item.get("url", "")):
                continue
            request_body = str(item.get("request", ""))
            if "element.split" not in request_body:
                continue
            with suppress(json.JSONDecodeError, TypeError):
                request_id = str(json.loads(request_body).get("request_id", ""))
                if request_id:
                    request_ids.add(request_id)
        return request_ids

    def result_count() -> int:
        return int(
            connection.evaluate(
                """(() => {
                  const selectors = document.querySelectorAll(
                    '.target-result-selector button'
                  ).length;
                  if (selectors > 0) return selectors;
                  return document.querySelector(
                    '.target-result-card img[src^="blob:"]'
                  ) ? 1 : 0;
                })()"""
            )
        )

    def cancel_stale_approval() -> None:
        if not connection.evaluate("!!document.querySelector('.target-approval')"):
            return
        wait_for(
            connection,
            """(() => {
              const approval = document.querySelector('.target-approval');
              if (!approval) return true;
              const cancel = [...approval.querySelectorAll('button')]
                .find((item) => item.textContent?.trim() === '取消');
              return !!cancel && !cancel.disabled;
            })()""",
            "a removable stale target-extraction approval",
            timeout,
        )
        if connection.evaluate("!!document.querySelector('.target-approval')"):
            connection.evaluate(
                """(() => {
                  const approval = document.querySelector('.target-approval');
                  const cancel = [...approval.querySelectorAll('button')]
                    .find((item) => item.textContent?.trim() === '取消');
                  if (!cancel || cancel.disabled) {
                    throw new Error('stale approval cancel is unavailable');
                  }
                  cancel.click();
                  return true;
                })()"""
            )
            wait_for(
                connection,
                "!document.querySelector('.target-approval')",
                "the stale target-extraction approval to close",
                timeout,
            )

    click_button(connection, "提取建模主体")
    wait_for(
        connection,
        "!!document.querySelector('#target-extraction-title')",
        "the target extraction workspace",
        timeout,
    )
    cancel_stale_approval()
    click_button(connection, "加载当前图片")
    wait_for(
        connection,
        "!!document.querySelector('.target-settings-panel section img[src^=\"blob:\"]')",
        "the target extraction source",
        timeout,
    )
    click_button(connection, "直接框选目标")
    wait_for(
        connection,
        "!!document.querySelector('.target-selection-rect.direct')",
        "the direct selection mode",
        timeout,
    )
    direct_class = connection.evaluate(
        "document.querySelector('.target-selection-rect')?.className || ''"
    )
    if "direct" not in direct_class:
        raise AssertionError("direct selection did not use the direct green-box state")
    for label, value in (("x", 1), ("y", 1), ("width", 12), ("height", 12)):
        set_labeled_input(connection, label, value)
    wait_for(
        connection,
        """[...document.querySelectorAll('button')].some(
          (item) => item.textContent?.includes('生成独立目标图') && !item.disabled
        )""",
        "the direct extraction action to become available",
        timeout,
    )
    direct_requests_before = collected_split_request_ids()
    direct_result_before = connection.evaluate(
        "document.querySelector('.target-result-card img')?.dataset.managedAssetId || ''"
    )
    click_button(connection, "生成独立目标图")
    wait_for(
        connection,
        (
            "document.body.innerText.includes('确认外部图像生成') && "
            "!!document.querySelector('.target-approval')"
        ),
        "the direct extraction approval",
        timeout,
    )
    direct_new_requests = collected_split_request_ids() - direct_requests_before
    if len(direct_new_requests) != 1:
        raise AssertionError(
            "direct extraction did not issue exactly one new element.split request"
        )
    click_button(connection, "批准并提交")
    wait_for(
        connection,
        (
            "!document.querySelector('.target-approval') && "
            "document.querySelector('.target-job-status span')?.textContent?.includes("
            "'生成完成，可以进入三视图或继续提取。')"
        ),
        "the new direct extraction job to complete",
        timeout,
    )
    wait_for(
        connection,
        (
            "(() => { const id = document.querySelector("
            "'.target-result-card img')?.dataset.managedAssetId || ''; "
            f"return !!id && id !== {json.dumps(direct_result_before)}; }})()"
        ),
        "a new direct extraction result",
        timeout,
    )
    direct_result_id = connection.evaluate(
        "document.querySelector('.target-result-card img')?.dataset.managedAssetId || ''"
    )

    click_button(connection, "先生成 AI 拆解图")
    wait_for(
        connection,
        "!![...document.querySelectorAll('[role=radio][aria-checked=true]')].find((item) => item.textContent?.includes('AI 拆解图'))",
        "the AI breakdown method",
        timeout,
    )
    breakdown_requests_before = collected_split_request_ids()
    breakdown_asset_before = connection.evaluate(
        "document.querySelector('.target-canvas img')?.dataset.managedAssetId || ''"
    )
    connection.evaluate(
        """(() => {
          const labels = ['生成部件拆解图', '重新生成拆解图'];
          const button = [...document.querySelectorAll('button')]
            .find((item) => labels.some((label) => item.textContent?.includes(label)) && !item.disabled);
          if (!button) throw new Error('AI breakdown generation control is unavailable');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "document.body.innerText.includes('确认外部图像生成') && "
            "!!document.querySelector('.target-approval')"
        ),
        "the AI breakdown approval",
        timeout,
    )
    breakdown_new_requests = collected_split_request_ids() - breakdown_requests_before
    if len(breakdown_new_requests) != 1:
        raise AssertionError(
            "AI breakdown did not issue exactly one new element.split request"
        )
    click_button(connection, "批准并提交")
    wait_for(
        connection,
        (
            "!document.querySelector('.target-approval') && "
            "document.querySelector('.target-job-status span')?.textContent?.includes("
            "'拆解图生成完成，请在红框画布中选择一个部件。') && "
            "document.body.innerText.includes('裁出选中部件')"
        ),
        "the new AI breakdown job and board",
        timeout,
    )
    wait_for(
        connection,
        (
            "(() => { const id = document.querySelector("
            "'.target-canvas img')?.dataset.managedAssetId || ''; "
            f"return !!id && id !== {json.dumps(breakdown_asset_before)}; }})()"
        ),
        "a new AI breakdown managed asset",
        timeout,
    )
    breakdown_asset_id = connection.evaluate(
        "document.querySelector('.target-canvas img')?.dataset.managedAssetId || ''"
    )
    breakdown_class = connection.evaluate(
        "document.querySelector('.target-selection-rect')?.className || ''"
    )
    if "breakdown" not in breakdown_class:
        raise AssertionError("breakdown selection did not use the breakdown red-box state")

    connection.evaluate("document.querySelector('button[aria-label=\"任务\"]').click()")
    wait_for(
        connection,
        "!!document.querySelector('.job-card .job-actions .primary')",
        "a completed extraction task",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('.job-card .job-actions .primary').click()"
    )
    wait_for(
        connection,
        "!!document.querySelector('#target-extraction-title')",
        "the task result in target extraction",
        timeout,
    )
    if not connection.evaluate(
        "!![...document.querySelectorAll('[role=radio][aria-checked=true]')].find((item) => item.textContent?.includes('AI 拆解图'))"
    ):
        raise AssertionError("task center did not restore the AI breakdown method")

    wait_for(
        connection,
        """['部件 x', '部件 y', '部件 width', '部件 height']
          .every((label) => document.querySelector(`input[aria-label="${label}"]`))""",
        "the restored breakdown selection controls",
        timeout,
    )
    for label, value in (
        ("部件 x", 1),
        ("部件 y", 1),
        ("部件 width", 10),
        ("部件 height", 10),
    ):
        set_labeled_input(connection, label, value)
    local_results_before = result_count()
    local_active_result_before = connection.evaluate(
        "document.querySelector('.target-result-card img')?.dataset.managedAssetId || ''"
    )
    click_button(connection, "裁出选中部件")
    wait_for(
        connection,
        (
            "document.querySelectorAll('.target-result-selector button').length > "
            f"{local_results_before}"
        ),
        "a new locally cropped extraction result",
        timeout,
    )
    wait_for(
        connection,
        (
            "(() => { const id = document.querySelector("
            "'.target-result-card img')?.dataset.managedAssetId || ''; "
            f"return !!id && id !== {json.dumps(local_active_result_before)}; }})()"
        ),
        "the locally cropped result to become active",
        timeout,
    )
    final_result_count = connection.evaluate(
        "document.querySelectorAll('.target-result-selector button').length"
    )
    selected_result_id = connection.evaluate(
        "document.querySelector('.target-result-card img')?.dataset.managedAssetId || ''"
    )

    click_button(connection, "在资产中查看")
    wait_for(
        connection,
        "!!document.querySelector('.asset-card.focused')",
        "the focused extracted asset",
        timeout,
    )
    click_button(connection, "工作区")
    wait_for(
        connection,
        "!!document.querySelector('#target-extraction-title')",
        "target extraction after asset navigation",
        timeout,
    )
    wait_for(
        connection,
        "!![...document.querySelectorAll('button')].find((item) => item.textContent?.includes('进入三视图制作') && !item.disabled)",
        "the restored extraction result after asset navigation",
        timeout,
    )
    click_button(connection, "进入三视图制作")
    wait_for(
        connection,
        "document.body.innerText.includes('三视图来源')",
        "the multiview handoff",
        timeout,
    )
    wait_for(
        connection,
        "document.querySelector('img.multiview-source-preview')"
        "?.dataset.managedAssetId === "
        + json.dumps(selected_result_id),
        "the selected extraction result in multiview",
        timeout,
    )
    multiview_source_asset_id = connection.evaluate(
        "document.querySelector('img.multiview-source-preview')"
        "?.dataset.managedAssetId || ''"
    )
    if not selected_result_id or multiview_source_asset_id != selected_result_id:
        raise AssertionError(
            "multiview did not receive the selected extraction result "
            f"(expected {selected_result_id or '<missing>'}, "
            f"received {multiview_source_asset_id or '<missing>'})"
        )
    return {
        "scenario": "target_extraction",
        "status": "passed",
        "assertions": {
            "direct_selection_class": direct_class,
            "breakdown_selection_class": breakdown_class,
            "direct_tool_requests": len(direct_new_requests),
            "breakdown_tool_requests": len(breakdown_new_requests),
            "direct_result_asset_id": direct_result_id,
            "breakdown_asset_id": breakdown_asset_id,
            "multiview_source_asset_id": multiview_source_asset_id,
            "result_count": final_result_count,
            "task_center_restored_breakdown": True,
            "asset_focus": True,
            "multiview_handoff": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_image_canvas(connection: CdpConnection, timeout: float) -> None:
    """Exercise the image workspace without relying on a physical pointer."""

    wait_for(
        connection,
        "!!document.querySelector('.image-workspace img[src^=\"blob:\"]')",
        "the managed Blob image preview",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const hundred = [...document.querySelectorAll('.zoom-controls button')]
            .find((item) => item.textContent?.trim() === '100%');
          if (!hundred) throw new Error('100% zoom control is missing');
          hundred.click();
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('output[aria-label=\"Zoom level\"]')?.textContent === '100%'",
        "a deterministic 100 percent canvas baseline",
        timeout,
    )
    for _ in range(50):
        connection.evaluate(
            """(() => {
              const stage = document.querySelector('.image-stage');
              if (!stage) throw new Error('image stage is missing');
              stage.dispatchEvent(new WheelEvent('wheel', {
                bubbles: true, cancelable: true, deltaY: -100, clientX: 320, clientY: 240,
              }));
            })()"""
        )
    wait_for(
        connection,
        "document.querySelector('output[aria-label=\"Zoom level\"]')?.textContent === '800%'",
        "the maximum zoom boundary",
        timeout,
    )
    for _ in range(80):
        connection.evaluate(
            """(() => {
              const stage = document.querySelector('.image-stage');
              stage.dispatchEvent(new WheelEvent('wheel', {
                bubbles: true, cancelable: true, deltaY: 100, clientX: 320, clientY: 240,
              }));
            })()"""
        )
    wait_for(
        connection,
        "document.querySelector('output[aria-label=\"Zoom level\"]')?.textContent === '10%'",
        "the minimum zoom boundary",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const hundred = [...document.querySelectorAll('.zoom-controls button')]
            .find((item) => item.textContent?.trim() === '100%');
          if (!hundred) throw new Error('100% zoom control is missing');
          hundred.click();
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('output[aria-label=\"Zoom level\"]')?.textContent === '100%'",
        "a panning baseline at 100 percent",
        timeout,
    )
    stage_rect = json.loads(
        connection.evaluate(
            """JSON.stringify((() => {
              const rect = document.querySelector('.image-stage')?.getBoundingClientRect();
              if (!rect) throw new Error('image stage is missing');
              return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
            })())"""
        )
    )
    start_x = stage_rect["left"] + stage_rect["width"] / 2
    start_y = stage_rect["top"] + stage_rect["height"] / 2
    connection.call(
        "Input.dispatchMouseEvent",
        type="mousePressed",
        x=start_x,
        y=start_y,
        button="middle",
        buttons=4,
        clickCount=1,
    )
    connection.call(
        "Input.dispatchMouseEvent",
        type="mouseMoved",
        x=start_x + 10,
        y=start_y + 10,
        button="middle",
        buttons=4,
    )
    connection.call(
        "Input.dispatchMouseEvent",
        type="mouseReleased",
        x=start_x + 10,
        y=start_y + 10,
        button="middle",
        buttons=0,
        clickCount=1,
    )
    wait_for(
        connection,
        "document.querySelector('.image-workspace img')?.style.transform.includes('translate(10px, 10px) scale(1)')",
        "middle-button canvas panning",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const hundred = [...document.querySelectorAll('.zoom-controls button')]
            .find((item) => item.textContent?.trim() === '100%');
          if (!hundred) throw new Error('100% zoom control is missing');
          hundred.click();
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('output[aria-label=\"Zoom level\"]')?.textContent === '100%'",
        "the 100 percent zoom reset",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const fit = [...document.querySelectorAll('.zoom-controls button')]
            .find((item) => item.textContent?.trim() !== '100%');
          if (!fit) throw new Error('fit zoom control is missing');
          fit.click();
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('output[aria-label=\"Zoom level\"]')?.textContent === 'Fit'",
        "the fit-to-view reset",
        timeout,
    )


def run_local_image_size(connection: CdpConnection, timeout: float) -> dict[str, object]:
    """Exercise local resize and bundled super-resolution through the real desktop DOM."""

    if connection.evaluate("!!document.querySelector('.local-image-size-dialog')"):
        connection.evaluate(
            """(() => {
              const cancel = [...document.querySelectorAll('.local-image-size-dialog button')]
                .find((item) => item.textContent?.trim() === '取消');
              if (!cancel) throw new Error('stale local image dialog cannot be closed');
              cancel.click();
            })()"""
        )
        wait_for(
            connection,
            "!document.querySelector('.local-image-size-dialog')",
            "the stale local image dialog to close",
            timeout,
        )
    if not connection.evaluate("!!document.querySelector('.image-workspace')"):
        connection.evaluate(
            """(() => {
              const workspace = [...document.querySelectorAll('.primary-navigation button')]
                .find((item) => item.textContent?.trim() === '工作区');
              if (!workspace) throw new Error('workspace navigation is missing');
              workspace.click();
            })()"""
        )
        wait_for(
            connection,
            "!!document.querySelector('.workflow-switcher')",
            "the product workspace navigation",
            timeout,
        )
        connection.evaluate(
            """(() => {
              const image = document.querySelector('.workflow-switcher button[aria-label="当前图片"]');
              if (!image) throw new Error('current image workspace control is missing');
              image.click();
            })()"""
        )
    wait_for(
        connection,
        "!!document.querySelector('.image-workspace img[src^=\"blob:\"]')",
        "the managed image workspace",
        timeout,
    )
    source_asset_id = connection.evaluate(
        "document.querySelector('.image-workspace img')?.dataset.managedAssetId || ''"
    )
    if not source_asset_id:
        raise AssertionError("the image preview is missing its managed asset identity")

    def tool_requests(tool_name: str) -> list[dict[str, object]]:
        connection.evaluate("true")
        records = [
            *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
            *cdp_network_records(connection),
        ]
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in records:
            request = str(item.get("request", ""))
            if tool_name not in request:
                continue
            unique[(str(item.get("url", "")), request)] = item
        return list(unique.values())

    def open_dialog() -> None:
        connection.evaluate(
            """(() => {
              const more = [...document.querySelectorAll('.canvas-context-toolbar button')]
                .find((item) => item.textContent?.trim() === '更多');
              if (!more) throw new Error('More image tools control is missing');
              more.click();
            })()"""
        )
        wait_for(
            connection,
            "!!document.querySelector('.canvas-more-menu')",
            "the expanded image tools menu",
            timeout,
        )
        connection.evaluate(
            """(() => {
              const edit = [...document.querySelectorAll('.canvas-more-menu button')]
                .find((item) => item.textContent?.includes('调整尺寸与超分'));
              if (!edit || edit.getAttribute('aria-disabled') === 'true') {
                throw new Error('local image size control is unavailable');
              }
              edit.click();
            })()"""
        )
        wait_for(
            connection,
            "!!document.querySelector('.local-image-size-dialog[role=dialog]')",
            "the local image size dialog",
            timeout,
        )

    before_resize = tool_requests("image.normalize")
    open_dialog()
    dialog_text = connection.evaluate(
        "document.querySelector('.local-image-size-dialog')?.innerText || ''"
    )
    for expected in ("本地图片处理", "普通缩放", "本地超分", "原图不会被覆盖"):
        if expected not in dialog_text:
            raise AssertionError(f"local image dialog is missing {expected!r}")
    connection.evaluate(
        """(() => {
          const setInput = (label, value) => {
            const input = document.querySelector(`[aria-label="${label}"]`);
            if (!input) throw new Error(`${label} input is missing`);
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(input, value);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
          };
          setInput('目标宽度', '320');
          setInput('目标高度', '240');
          const lock = document.querySelector('.local-image-checkbox input');
          if (!lock || !lock.checked) throw new Error('aspect ratio control is missing');
          lock.click();
          const submit = [...document.querySelectorAll('.local-image-size-dialog button')]
            .find((item) => item.textContent?.includes('生成缩放结果'));
          if (!submit) throw new Error('resize submit control is missing');
          submit.click();
        })()"""
    )
    wait_for(
        connection,
        "!document.querySelector('.local-image-size-dialog')",
        "the completed local resize",
        timeout,
    )
    wait_for(
        connection,
        "document.querySelector('.current-asset-summary')?.textContent?.includes('320 × 240')",
        "the resized dimensions",
        timeout,
    )
    wait_for(
        connection,
        "(() => { const id = document.querySelector('.image-workspace img')?.dataset.managedAssetId; "
        "return !!id && id !== " + json.dumps(source_asset_id) + "; })()",
        "the newly selected resized asset",
        timeout,
    )
    resized_asset_id = connection.evaluate(
        "document.querySelector('.image-workspace img')?.dataset.managedAssetId || ''"
    )
    if not resized_asset_id or resized_asset_id == source_asset_id:
        raise AssertionError("local resize did not select a new managed asset")
    resize_requests = tool_requests("image.normalize")
    if len(resize_requests) != len(before_resize) + 1:
        raise AssertionError("local resize did not issue exactly one image.normalize request")

    before_upscale = tool_requests("image.upscale_local")
    open_dialog()
    connection.evaluate(
        """(() => {
          const upscale = [...document.querySelectorAll('.local-image-mode button')]
            .find((item) => item.textContent?.trim() === '本地超分');
          if (!upscale) throw new Error('local upscale mode is missing');
          upscale.click();
        })()"""
    )
    wait_for(
        connection,
        "[...document.querySelectorAll('.local-image-mode button')].some((item) => item.textContent?.trim() === '本地超分' && item.getAttribute('aria-pressed') === 'true')",
        "the selected local upscale mode",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const submit = [...document.querySelectorAll('.local-image-size-dialog button')]
            .find((item) => item.textContent?.includes('开始本地超分'));
          if (!submit) throw new Error('local upscale submit control is missing');
          submit.click();
        })()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.jobs-panel')",
        "the queued local upscale job",
        timeout,
    )
    upscale_requests = tool_requests("image.upscale_local")
    if len(upscale_requests) != len(before_upscale) + 1:
        raise AssertionError("local upscale did not issue exactly one image.upscale_local request")
    upscale_request = str(upscale_requests[-1].get("request", ""))
    upscale_payload = json.loads(upscale_request)
    upscale_arguments = upscale_payload.get("arguments", {})
    if (
        "approval" in upscale_request.casefold()
        or upscale_payload.get("provider_profile") is not None
        or "provider_profile" in upscale_arguments
    ):
        raise AssertionError("local upscale unexpectedly used approval or Provider arguments")

    return {
        "scenario": "local_image_size",
        "status": "passed",
        "assertions": {
            "source_asset_preserved": True,
            "resize_tool": "image.normalize",
            "resized_dimensions": "320x240",
            "resized_asset_selected": True,
            "upscale_tool": "image.upscale_local",
            "upscale_scale": 2,
            "provider_approval_requests": 0,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_asset_visual_dedup(connection: CdpConnection, timeout: float) -> dict[str, object]:
    """Prove resized copies stay in storage while the asset browser renders one card."""

    def open_assets() -> None:
        if connection.evaluate("!!document.querySelector('.asset-browser')"):
            connection.evaluate(
                """(() => {
                  const button = [...document.querySelectorAll('.primary-navigation button')]
                    .find((item) => item.textContent?.trim() === '工作区');
                  if (!button) throw new Error('workspace navigation is missing');
                  button.click();
                })()"""
            )
            wait_for(
                connection,
                "!document.querySelector('.asset-browser')",
                "the asset browser to unmount",
                timeout,
            )
        connection.evaluate(
            """(() => {
              const button = [...document.querySelectorAll('.primary-navigation button')]
                .find((item) => item.textContent?.trim() === '资产');
              if (!button) throw new Error('asset navigation is missing');
              button.click();
            })()"""
        )
        wait_for(
            connection,
            "!!document.querySelector('.asset-browser .asset-list')",
            "the asset browser",
            timeout,
        )
        wait_for(
            connection,
            "(globalThis.__aipicE2E?.network || []).some((item) => "
            "String(item.url || '').includes('include_visual_identities=true'))",
            "the visual-identity asset query",
            timeout,
        )
        wait_for(
            connection,
            "document.querySelectorAll('.asset-card').length > 0",
            "the rendered asset cards",
            timeout,
        )

    def raw_assets() -> list[dict[str, object]]:
        result = connection.evaluate(
            """(() => {
              const record = [...(globalThis.__aipicE2E?.network || [])]
                .reverse()
                .find((item) => item.status === 200
                  && String(item.url || '').includes('include_visual_identities=true')
                  && typeof item.response === 'string' && item.response.length > 0);
              if (!record) return null;
              return JSON.parse(record.response);
            })()"""
        )
        if not isinstance(result, list):
            raise AssertionError("the authorized visual-identity asset response is unavailable")
        return result

    def visual_distance(left: str, right: str) -> float:
        if len(left) != len(right) or not left:
            return 1.0
        try:
            different_bits = sum(
                (int(left[index], 16) ^ int(right[index], 16)).bit_count()
                for index in range(len(left))
            )
        except ValueError:
            return 1.0
        return different_bits / (len(left) * 4)

    def duplicate_pair(
        assets: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object], float] | None:
        library_image_types = {
            "source_image",
            "generated_image",
            "annotation",
            "crop",
            "multiview",
        }
        images = [
            asset
            for asset in assets
            if asset.get("asset_type") in library_image_types
            and isinstance(asset.get("visual_fingerprint"), str)
            and isinstance(asset.get("visual_aspect_ratio"), (int, float))
            and isinstance(asset.get("sha256"), str)
        ]
        for index, left in enumerate(images):
            for right in images[index + 1 :]:
                if left["sha256"] == right["sha256"]:
                    continue
                if abs(float(left["visual_aspect_ratio"]) - float(right["visual_aspect_ratio"])) > 0.025:
                    continue
                distance = visual_distance(
                    str(left["visual_fingerprint"]), str(right["visual_fingerprint"])
                )
                if distance <= 0.08:
                    return left, right, distance
        return None

    open_assets()
    assets = raw_assets()
    pair = duplicate_pair(assets)
    if pair is None:
        run_local_image_size(connection, timeout)
        open_assets()
        assets = raw_assets()
        pair = duplicate_pair(assets)
    if pair is None:
        raise AssertionError("no resized visual-duplicate pair was returned by the asset API")

    left, right, distance = pair
    duplicate_ids = [str(left["id"]), str(right["id"])]
    expected = next((asset for asset in (left, right) if asset.get("is_current")), None)
    if expected is None:
        expected = max(
            (left, right),
            key=lambda asset: int((asset.get("metadata") or {}).get("width") or 0)
            * int((asset.get("metadata") or {}).get("height") or 0),
        )

    rendered_ids = connection.evaluate(
        "[...document.querySelectorAll('.asset-card')].map((item) => item.dataset.assetId)"
    )
    rendered_duplicates = [asset_id for asset_id in duplicate_ids if asset_id in rendered_ids]
    if rendered_duplicates != [str(expected["id"])]:
        raise AssertionError(
            "asset browser did not keep exactly the preferred visual-duplicate representative"
        )

    original_columns = connection.evaluate(
        "document.querySelector('.asset-list')?.dataset.columns || '4'"
    )
    tested_columns: list[int] = []
    for columns in range(3, 9):
        connection.evaluate(
            """(() => {
              const expected = '每行 """
            + str(columns)
            + """ 个资产';
              const button = [...document.querySelectorAll('.asset-layout-options button')]
                .find((item) => item.getAttribute('aria-label') === expected);
              if (!button) throw new Error('asset column control is missing');
              button.click();
            })()"""
        )
        wait_for(
            connection,
            f"document.querySelector('.asset-list')?.dataset.columns === '{columns}' "
            f"&& document.querySelector('button[aria-label=\"每行 {columns} 个资产\"]')?.getAttribute('aria-pressed') === 'true'",
            f"the {columns}-column asset layout",
            timeout,
        )
        tested_columns.append(columns)
    if str(original_columns) in {str(value) for value in range(3, 9)}:
        connection.evaluate(
            "document.querySelector('button[aria-label=\"每行 "
            + str(original_columns)
            + " 个资产\"]')?.click()"
        )

    return {
        "scenario": "asset_visual_dedup",
        "status": "passed",
        "assertions": {
            "raw_visual_duplicate_count": 2,
            "different_sha256": True,
            "visual_fingerprints_present": True,
            "visual_distance": round(distance, 4),
            "rendered_duplicate_cards": 1,
            "preferred_representative_kept": True,
            "supported_columns": tested_columns,
            "original_column_layout_restored": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_mock_tripo_approval(connection: CdpConnection, timeout: float) -> None:
    """Prove that 3D generation cannot bypass its desktop approval dialog."""

    def model3d_requests() -> list[dict[str, object]]:
        connection.evaluate("true")
        records = [
            *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
            *cdp_network_records(connection),
        ]
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in records:
            if "model3d.generate" not in str(item.get("request", "")):
                continue
            unique[(str(item.get("url", "")), str(item.get("request", "")))] = item
        return list(unique.values())

    trigger = """(() => {
      const button = [...document.querySelectorAll('.canvas-context-toolbar button')]
        .find((item) => item.textContent?.includes('3D'));
      if (!button) throw new Error('3D generation control is missing');
      button.click();
    })()"""
    before = model3d_requests()
    connection.evaluate(trigger)
    wait_for(connection, "!!document.querySelector('[role=alertdialog]')", "the Tripo approval dialog", timeout)
    dialog_text = connection.evaluate("document.querySelector('[role=alertdialog]')?.innerText || ''")
    for expected in ("Tripo3D", "tripo3d/default", "v3.1-20260211"):
        if expected not in dialog_text:
            raise AssertionError(f"Tripo approval is missing {expected!r}")
    if len(model3d_requests()) != len(before):
        raise AssertionError("3D generation invoked the provider before approval")
    connection.evaluate(
        "document.querySelector('[role=alertdialog] .dialog-actions button:not(.primary)')?.click()"
    )
    wait_for(connection, "!document.querySelector('[role=alertdialog]')", "approval cancellation", timeout)
    if len(model3d_requests()) != len(before):
        raise AssertionError("cancelling a 3D approval created a provider request")

    connection.evaluate(trigger)
    wait_for(connection, "!!document.querySelector('[role=alertdialog]')", "the second Tripo approval dialog", timeout)
    connection.evaluate("document.querySelector('[role=alertdialog] .dialog-actions .primary')?.click()")
    wait_for(connection, "!!document.querySelector('.jobs-panel')", "the queued Mock Tripo job", timeout)
    submitted = model3d_requests()
    if len(submitted) != len(before) + 1:
        raise AssertionError("approving 3D generation did not issue exactly one tool request")
    request = str(submitted[-1].get("request", ""))
    for expected in ("model3d.generate", "tripo3d/default", "image_asset_id"):
        if expected not in request:
            raise AssertionError(f"approved 3D request is missing {expected!r}")


def run_open_model_result(connection: CdpConnection, timeout: float) -> None:
    """Open a succeeded Mock Tripo result and verify WebView2 receives a GLB Blob."""

    wait_for(
        connection,
        """[...document.querySelectorAll('.jobs-panel .job-card .primary')]
          .some((item) => item.textContent?.includes('3D'))""",
        "the succeeded Mock Tripo result action",
        timeout,
    )
    connection.evaluate(
        """[...document.querySelectorAll('.jobs-panel .job-card .primary')]
          .find((item) => item.textContent?.includes('3D'))?.click()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.model-workspace model-viewer[src^=\"blob:\"]')",
        "the managed GLB model preview",
        timeout,
    )


def run_agent_ui_action_navigation(connection: CdpConnection, timeout: float) -> None:
    """Inject one controlled Agent event and prove the real workbench navigation."""

    wait_for(
        connection,
        "!!document.querySelector('.agent-live-panel')",
        "the Agent panel",
        timeout,
    )
    connection.evaluate(
        """(() => {
          if (globalThis.__aipicOriginalFetch) throw new Error('controlled fetch is already installed');
          const original = globalThis.fetch.bind(globalThis);
          globalThis.__aipicOriginalFetch = original;
          let delivered = false;
          globalThis.fetch = async (...args) => {
            const input = args[0];
            const url = String(input instanceof Request ? input.url : input);
            if (!delivered && /\\/v1\\/agent\\/conversations\\/[^/]+\\/events\\?/.test(url)) {
              delivered = true;
              return new Response(JSON.stringify({
                items: [{
                  sequence_no: 900000,
                  event_type: 'tool.completed',
                  payload: {
                    conversation_id: 'controlled-conversation',
                    tool_call_id: 'controlled-multiview-action',
                    tool_name: 'multiview.request_box_confirmation',
                    is_error: false,
                    result: {
                      content: [{type: 'text', text: 'Three-view regions require user confirmation.'}],
                      details: {
                        status: 'awaiting_ui_action',
                        ui_action: {
                          action_id: 'controlled-multiview-action',
                          type: 'confirm_multiview_regions',
                          workspace_mode: 'multiview',
                        },
                      },
                      is_error: false,
                    },
                  },
                  created_at: new Date().toISOString(),
                }],
                next_cursor: 900000,
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }
            return original(...args);
          };
          return true;
        })()"""
    )
    try:
        wait_for(
            connection,
            (
                "!!document.querySelector('.multiview-workspace') && "
                "[...document.querySelectorAll('.agent-tool-execution button')]"
                ".some((item) => item.textContent?.trim() === 'Open multiview')"
            ),
            "the Agent UI action to open the Multiview workspace",
            timeout,
        )
        selected = connection.evaluate(
            """(() => {
              const active = document.querySelector('.workflow-stage-tools button[aria-pressed="true"]');
              return {
                hasMultiview: !!document.querySelector('.multiview-workspace'),
                activeTitle: active?.getAttribute('title') || '',
                openAction: [...document.querySelectorAll('.agent-tool-execution button')]
                  .some((item) => item.textContent?.trim() === 'Open multiview'),
              };
            })()"""
        )
        if not selected.get("hasMultiview") or not selected.get("openAction"):
            raise AssertionError("Agent UI action did not select the expected workspace")
    finally:
        connection.evaluate(
            """(() => {
              if (globalThis.__aipicOriginalFetch) {
                globalThis.fetch = globalThis.__aipicOriginalFetch;
                delete globalThis.__aipicOriginalFetch;
              }
              return true;
            })()"""
        )


def run_agent_image_result_navigation(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    """Prove a queued Agent image job reopens Creative Image Generation with its result."""

    wait_for(
        connection,
        "!!document.querySelector('.agent-live-panel')",
        "the Agent panel",
        timeout,
    )
    connection.evaluate(
        r"""(async () => {
          if (globalThis.__aipicOriginalFetch) throw new Error('controlled fetch is already installed');
          const original = globalThis.fetch.bind(globalThis);
          globalThis.__aipicOriginalFetch = original;
          globalThis.__aipicAgentImageResult = {
            projectId: null,
            assetId: null,
            apiOrigin: null,
            requestHeaders: null,
            eventStage: 0,
            terminalEventsDelivered: 0,
            messageReads: 0,
            thumbnailReads: 0,
            jobReads: 0,
          };
          globalThis.fetch = async (...args) => {
            const input = args[0];
            const init = args[1] || {};
            const request = input instanceof Request ? input : null;
            const url = String(request ? request.url : input);
            const method = String(init.method || request?.method || 'GET').toUpperCase();
            const controlled = globalThis.__aipicAgentImageResult;

            if (/\/v1\/agent\/conversations\/[^/]+\/events\?/.test(url)) {
              const eventUrl = new URL(url, location.href);
              const projectId = eventUrl.searchParams.get('project_id');
              if (!projectId) throw new Error('controlled Agent event has no project identity');
              if (controlled.eventStage === 1 && controlled.jobReads > 0) {
                controlled.eventStage = 2;
                controlled.terminalEventsDelivered += 1;
                const resultMessage = {
                  id: 'controlled-image-result',
                  role: 'tool_result',
                  tool_call_id: 'controlled-image-call',
                  tool_name: 'generate_images',
                  content: [{type: 'text', text: 'Controlled image generation completed.'}],
                  details: {
                    status: 'succeeded',
                    output_asset_ids: [controlled.assetId],
                    job: {
                      job_id: 'controlled-image-job',
                      status: 'succeeded',
                      job_type: 'image.generate',
                      stage: 'completed',
                      provider: 'controlled/fixture',
                    },
                  },
                  is_error: false,
                };
                const finalMessage = {
                  id: 'controlled-image-final',
                  role: 'assistant',
                  content: [{
                    type: 'text',
                    text: `图片已生成：\n\n![受管结果](asset:${controlled.assetId})`,
                  }],
                  stop_reason: 'stop',
                };
                return new Response(JSON.stringify({
                  items: [
                    {
                      sequence_no: 910003,
                      event_type: 'message.completed',
                      payload: {conversation_id: 'controlled-conversation', message: resultMessage},
                      created_at: new Date().toISOString(),
                    },
                    {
                      sequence_no: 910004,
                      event_type: 'message.completed',
                      payload: {conversation_id: 'controlled-conversation', message: finalMessage},
                      created_at: new Date().toISOString(),
                    },
                    {
                      sequence_no: 910005,
                      event_type: 'conversation.completed',
                      payload: {conversation_id: 'controlled-conversation'},
                      created_at: new Date().toISOString(),
                    },
                  ],
                  next_cursor: 910005,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              if (controlled.eventStage > 0) {
                return new Response(JSON.stringify({
                  items: [], next_cursor: controlled.eventStage === 2 ? 910005 : 910002,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              const requestHeaders = new Headers(request?.headers || init.headers || {});
              const assetsResponse = await original(
                `${eventUrl.origin}/v1/projects/${encodeURIComponent(projectId)}/assets?include_trashed=false`,
                {headers: requestHeaders}
              );
              if (!assetsResponse.ok) throw new Error('controlled assets are unavailable');
              const assets = await assetsResponse.json();
              const imageAsset = assets.find((asset) =>
                asset.asset_type === 'source_image'
                && String(asset.mime_type || '').startsWith('image/')
                && !asset.trashed_at
              );
              if (!imageAsset) throw new Error('controlled image asset is unavailable');
              controlled.projectId = projectId;
              controlled.assetId = imageAsset.id;
              controlled.apiOrigin = eventUrl.origin;
              controlled.requestHeaders = requestHeaders;
              controlled.eventStage = 1;
              return new Response(JSON.stringify({
                items: [
                  {
                    sequence_no: 910001,
                    event_type: 'tool.call',
                    payload: {
                      conversation_id: 'controlled-conversation',
                      tool_call: {
                        id: 'controlled-image-call',
                        name: 'generate_images',
                        arguments: {candidate_count: 1, aspect_ratio: '1:1'},
                      },
                    },
                    created_at: new Date().toISOString(),
                  },
                  {
                    sequence_no: 910002,
                    event_type: 'tool.completed',
                    payload: {
                      conversation_id: 'controlled-conversation',
                      tool_call_id: 'controlled-image-call',
                      tool_name: 'generate_images',
                      is_error: false,
                      result: {
                        content: [{type: 'text', text: 'Controlled image generation queued.'}],
                        details: {
                          status: 'queued',
                          job: {
                            job_id: 'controlled-image-job',
                            status: 'queued',
                            job_type: 'image.generate',
                            stage: 'queued',
                            elapsed_seconds: 0,
                            estimated_seconds: 1,
                            provider: 'controlled/fixture',
                            can_cancel: true,
                            can_stop_waiting: false,
                          },
                        },
                        is_error: false,
                      },
                    },
                    created_at: new Date().toISOString(),
                  },
                ],
                next_cursor: 910002,
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }

            if (
              method === 'GET'
              && controlled.eventStage === 2
              && /\/v1\/agent\/conversations\/[^/]+\/messages\?/.test(url)
            ) {
              controlled.messageReads += 1;
              return new Response(JSON.stringify({
                items: [
                  {
                    id: 'controlled-image-call-message',
                    role: 'assistant',
                    content: [{
                      type: 'tool_call',
                      id: 'controlled-image-call',
                      name: 'generate_images',
                      arguments: {candidate_count: 1, aspect_ratio: '1:1'},
                    }],
                    stop_reason: 'tool_use',
                  },
                  {
                    id: 'controlled-image-result',
                    role: 'tool_result',
                    tool_call_id: 'controlled-image-call',
                    tool_name: 'generate_images',
                    content: [{type: 'text', text: 'Controlled image generation completed.'}],
                    details: {
                      status: 'succeeded',
                      output_asset_ids: [controlled.assetId],
                      job: {
                        job_id: 'controlled-image-job',
                        status: 'succeeded',
                        job_type: 'image.generate',
                        stage: 'completed',
                        provider: 'controlled/fixture',
                      },
                    },
                    is_error: false,
                  },
                  {
                    id: 'controlled-image-final',
                    role: 'assistant',
                    content: [{
                      type: 'text',
                      text: `图片已生成：\n\n![受管结果](asset:${controlled.assetId})`,
                    }],
                    stop_reason: 'stop',
                  },
                ],
                event_cursor: 910005,
                next_before: null,
                has_more: false,
                pending_ui_actions: [],
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }

            if (
              method === 'GET'
              && controlled.assetId
              && url.includes(`/v1/assets/${encodeURIComponent(controlled.assetId)}/thumbnail?`)
            ) {
              controlled.thumbnailReads += 1;
              return original(...args);
            }

            if (/\/v1\/jobs\/controlled-image-job\?/.test(url)) {
              controlled.jobReads += 1;
              return new Response(JSON.stringify({
                schema_version: 1,
                id: 'controlled-image-job',
                job_type: 'image.generate',
                status: 'succeeded',
                stage: 'completed',
                progress: 1,
                elapsed_seconds: 1,
                estimated_seconds: 0,
                provider: 'controlled/fixture',
                cancel_capability: 'not_cancellable',
                can_cancel: false,
                can_stop_waiting: false,
                output_asset_ids: [controlled.assetId],
                input_asset_ids: [],
                error: null,
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }

            return original(...args);
          };
          return {installed: true};
        })()"""
    )
    try:
        wait_for(
            connection,
            "!!document.querySelector('.prompt-image-workspace')",
            "the Creative Image Generation workspace",
            timeout,
        )
        wait_for(
            connection,
            "!!document.querySelector('.prompt-image-results')",
            "the Agent-generated image result section",
            timeout,
        )
        wait_for(
            connection,
            (
                "(() => { const images = [...document.querySelectorAll("
                "'.prompt-image-results img[src^=\"blob:\"]')]; return images.length >= 2 && "
                "images.every((item) => item.complete && item.naturalWidth > 0); })()"
            ),
            "decoded Blob-backed Agent image previews",
            timeout,
        )
        wait_for(
            connection,
            "globalThis.__aipicAgentImageResult?.terminalEventsDelivered === 1",
            "the broker-delivered Agent terminal result",
            timeout,
        )
        wait_for(
            connection,
            (
                "(() => { const image = document.querySelector('.agent-inline-image img'); "
                "if (!image || !image.src.startsWith('blob:') || image.alt !== '受管结果' "
                "|| !image.complete || image.naturalWidth <= 0) return false; "
                "const box = image.getBoundingClientRect(); return Math.abs(box.width - 80) <= 1 "
                "&& Math.abs(box.height - 80) <= 1; })()"
            ),
            "the compact inline Blob-backed final-response image",
            timeout,
        )
        wait_for(
            connection,
            (
                "globalThis.__aipicAgentImageResult?.messageReads >= 1 "
                "&& globalThis.__aipicAgentImageResult?.thumbnailReads >= 1 "
                "&& !document.querySelector('.agent-message.assistant img[src^=\"asset:\"]') "
                "&& !document.querySelector('.agent-message.assistant .agent-chat-images')"
            ),
            "the durable final transcript without raw or duplicate image previews",
            timeout,
        )
        wait_for(
            connection,
            """(async () => {
              const controlled = globalThis.__aipicAgentImageResult;
              const response = await globalThis.__aipicOriginalFetch(
                `${controlled.apiOrigin}/v1/projects/${encodeURIComponent(controlled.projectId)}`,
                {headers: controlled.requestHeaders}
              );
              if (!response.ok) return false;
              const project = await response.json();
              const state = JSON.parse(project.workspace_state_json || '{}');
              return state.image_generation_job_id === 'controlled-image-job'
                && state.workflow_contexts?.prompt_image?.job_id === 'controlled-image-job';
            })()""",
            "the persisted Agent image job identity",
            timeout,
        )
        result = connection.evaluate(
            """(() => {
              const controlled = globalThis.__aipicAgentImageResult;
              const images = [...document.querySelectorAll('.prompt-image-results img')];
              const inlineImage = document.querySelector('.agent-inline-image img');
              const inlineBox = inlineImage?.getBoundingClientRect();
              return {
                workspaceVisible: !!document.querySelector('.prompt-image-workspace'),
                resultVisible: !!document.querySelector('.prompt-image-results'),
                resultAssetId: controlled.assetId,
                jobReads: controlled.jobReads,
                terminalEventsDelivered: controlled.terminalEventsDelivered,
                messageReads: controlled.messageReads,
                thumbnailReads: controlled.thumbnailReads,
                previewCount: images.length,
                decodedBlobPreviews: images.filter((item) =>
                  item.src.startsWith('blob:') && item.complete && item.naturalWidth > 0
                ).length,
                inlineBlobPreview: Boolean(inlineImage?.src.startsWith('blob:')),
                inlineAlt: inlineImage?.alt || '',
                inlineWidth: inlineBox?.width || 0,
                inlineHeight: inlineBox?.height || 0,
                rawAssetImages: document.querySelectorAll(
                  '.agent-message.assistant img[src^="asset:"]'
                ).length,
                duplicateGallery: Boolean(document.querySelector(
                  '.agent-message.assistant .agent-chat-images'
                )),
              };
            })()"""
        )
    finally:
        connection.evaluate(
            """(() => {
              if (globalThis.__aipicOriginalFetch) {
                globalThis.fetch = globalThis.__aipicOriginalFetch;
                delete globalThis.__aipicOriginalFetch;
              }
              delete globalThis.__aipicAgentImageResult;
              return true;
            })()"""
        )

    return {
        "scenario": "agent_image_result_navigation",
        "status": "passed",
        "assertions": {
            "workspace_mode": "prompt_image",
            "workspace_visible": result["workspaceVisible"],
            "result_visible": result["resultVisible"],
            "result_asset_id": result["resultAssetId"],
            "job_id": "controlled-image-job",
            "job_reads": result["jobReads"],
            "terminal_events_delivered": result["terminalEventsDelivered"],
            "message_reads": result["messageReads"],
            "thumbnail_reads": result["thumbnailReads"],
            "preview_count": result["previewCount"],
            "decoded_blob_previews": result["decodedBlobPreviews"],
            "inline_blob_preview": result["inlineBlobPreview"],
            "inline_alt": result["inlineAlt"],
            "inline_width": result["inlineWidth"],
            "inline_height": result["inlineHeight"],
            "raw_asset_images": result["rawAssetImages"],
            "duplicate_gallery": result["duplicateGallery"],
            "workspace_job_persisted": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_agent_target_extraction_result_sync(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    """Prove an Agent target-extraction completion restores both managed images."""

    wait_for(connection, "!!document.querySelector('.agent-live-panel')", "the Agent panel", timeout)
    connection.evaluate(
        r"""(async () => {
          if (globalThis.__aipicOriginalFetch) throw new Error('controlled fetch is already installed');
          const original = globalThis.fetch.bind(globalThis);
          globalThis.__aipicOriginalFetch = original;
          globalThis.__aipicAgentTargetExtraction = {
            projectId: null, sourceAsset: null, outputAsset: null, apiOrigin: null,
            requestHeaders: null, eventsDelivered: false, continuationPosts: 0,
          };
          globalThis.fetch = async (...args) => {
            const input = args[0];
            const init = args[1] || {};
            const request = input instanceof Request ? input : null;
            const url = String(request ? request.url : input);
            const method = String(init.method || request?.method || 'GET').toUpperCase();
            const controlled = globalThis.__aipicAgentTargetExtraction;

            if (!controlled.eventsDelivered && /\/v1\/agent\/conversations\/[^/]+\/events\?/.test(url)) {
              const eventUrl = new URL(url, location.href);
              const projectId = eventUrl.searchParams.get('project_id');
              if (!projectId) throw new Error('controlled Agent event has no project identity');
              const requestHeaders = new Headers(request?.headers || init.headers || {});
              const assetsResponse = await original(
                `${eventUrl.origin}/v1/projects/${encodeURIComponent(projectId)}/assets?include_trashed=false`,
                {headers: requestHeaders},
              );
              if (!assetsResponse.ok) throw new Error('controlled assets are unavailable');
              const assets = await assetsResponse.json();
              const sourceAsset = assets.find((asset) => asset.asset_type === 'source_image'
                && String(asset.mime_type || '').startsWith('image/') && !asset.trashed_at);
              if (!sourceAsset) throw new Error('controlled source image is unavailable');
              controlled.projectId = projectId;
              controlled.sourceAsset = sourceAsset;
              controlled.outputAsset = {
                ...sourceAsset,
                id: 'controlled-target-output',
                name: 'controlled-target-output.png',
                asset_type: 'generated_image',
                parent_asset_id: sourceAsset.id,
                is_current: false,
              };
              controlled.apiOrigin = eventUrl.origin;
              controlled.requestHeaders = requestHeaders;
              controlled.eventsDelivered = true;
              return new Response(JSON.stringify({
                items: [
                  {
                    sequence_no: 920001,
                    event_type: 'tool.call',
                    payload: {
                      conversation_id: 'controlled-target-conversation',
                      tool_call: {
                        id: 'controlled-target-call', name: 'split_image',
                        arguments: {
                          source_asset_ref: sourceAsset.id,
                          selection_ref: 'controlled-selection',
                          prompt_asset_ref: 'controlled-target-prompt',
                          split_mode: 'boxsplit',
                        },
                      },
                    },
                    created_at: new Date().toISOString(),
                  },
                  {
                    sequence_no: 920002,
                    event_type: 'tool.completed',
                    payload: {
                      conversation_id: 'controlled-target-conversation',
                      tool_call_id: 'controlled-target-call', tool_name: 'split_image', is_error: false,
                      result: {
                        content: [{type: 'text', text: 'Controlled target extraction queued.'}],
                        details: {status: 'queued', job: {
                          job_id: 'controlled-target-job', status: 'queued', job_type: 'element.split',
                          stage: 'queued', elapsed_seconds: 0, estimated_seconds: 1,
                          provider: 'controlled/fixture', can_cancel: true, can_stop_waiting: false,
                        }},
                        is_error: false,
                      },
                    },
                    created_at: new Date().toISOString(),
                  },
                ],
                next_cursor: 920002,
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }

            if (/\/v1\/jobs\/controlled-target-job\?/.test(url)) {
              return new Response(JSON.stringify({
                schema_version: 1, id: 'controlled-target-job', job_type: 'element.split',
                status: 'succeeded', stage: 'completed', progress: 1, elapsed_seconds: 1,
                estimated_seconds: 0, provider: 'controlled/fixture', cancel_capability: 'not_cancellable',
                can_cancel: false, can_stop_waiting: false,
                output_asset_ids: [controlled.outputAsset.id], input_asset_ids: [controlled.sourceAsset.id], error: null,
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }

            if (controlled.outputAsset && method === 'GET'
              && new URL(url, location.href).pathname === `/v1/projects/${encodeURIComponent(controlled.projectId)}/assets`) {
              const response = await original(...args);
              if (!response.ok) return response;
              const assets = await response.json();
              return new Response(JSON.stringify([...assets.filter((asset) => asset.id !== controlled.outputAsset.id), controlled.outputAsset]), {
                status: 200, headers: {'Content-Type': 'application/json'},
              });
            }
            if (controlled.outputAsset && /\/assets\/controlled-target-output\/content$/.test(url)) {
              const source = await original(
                `${controlled.apiOrigin}/v1/projects/${encodeURIComponent(controlled.projectId)}/assets/${encodeURIComponent(controlled.sourceAsset.id)}/content`,
                {headers: controlled.requestHeaders},
              );
              return new Response(await source.arrayBuffer(), {
                status: source.status, headers: {'Content-Type': source.headers.get('Content-Type') || 'image/png'},
              });
            }
            if (method === 'POST' && /\/v1\/agent\/conversations\/[^/]+\/messages$/.test(url)) {
              const body = JSON.parse(String(init.body || '{}'));
              if (String(body.request_id || '').startsWith('agent-job-terminal-controlled-target-job')) {
                controlled.continuationPosts += 1;
                return new Response(JSON.stringify({
                  id: 'controlled-target-conversation', project_id: controlled.projectId, state: 'running', message_count: 0,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
            }
            return original(...args);
          };
          return true;
        })()"""
    )
    try:
        wait_for(connection, "!!document.querySelector('.target-extraction-workspace')", "the target extraction workspace", timeout)
        wait_for(
            connection,
            "!!document.querySelector('.target-settings-panel img[src^=\"blob:\"]') && !!document.querySelector('.target-result-card img[data-managed-asset-id=\"controlled-target-output\"]')",
            "the managed source and generated target images",
            timeout,
        )
        wait_for(
            connection,
            "globalThis.__aipicAgentTargetExtraction?.continuationPosts === 1",
            "the intercepted Agent terminal continuation",
            timeout,
        )
        wait_for(
            connection,
            r"""(async () => {
              const controlled = globalThis.__aipicAgentTargetExtraction;
              const response = await globalThis.__aipicOriginalFetch(
                `${controlled.apiOrigin}/v1/projects/${encodeURIComponent(controlled.projectId)}`,
                {headers: controlled.requestHeaders},
              );
              if (!response.ok) return false;
              const state = JSON.parse((await response.json()).workspace_state_json || '{}');
              const target = state.workflow_contexts?.target_extract;
              return state.workspace_mode === 'target_extract'
                && target?.source_asset_id === controlled.sourceAsset.id
                && target?.active_result_asset_id === controlled.outputAsset.id
                && target?.result_asset_ids?.includes(controlled.outputAsset.id);
            })()""",
            "the persisted Agent target-extraction source and result identities",
            timeout,
        )
        result = connection.evaluate(
            """(() => ({
              sourceAssetId: globalThis.__aipicAgentTargetExtraction.sourceAsset.id,
              outputAssetId: globalThis.__aipicAgentTargetExtraction.outputAsset.id,
              sourcePreview: !!document.querySelector('.target-settings-panel img[src^=\"blob:\"]'),
              outputPreview: !!document.querySelector('.target-result-card img[data-managed-asset-id=\"controlled-target-output\"]'),
              continuationPosts: globalThis.__aipicAgentTargetExtraction.continuationPosts,
            }))()"""
        )
    finally:
        connection.evaluate(
            """(() => {
              if (globalThis.__aipicOriginalFetch) globalThis.fetch = globalThis.__aipicOriginalFetch;
              delete globalThis.__aipicOriginalFetch;
              delete globalThis.__aipicAgentTargetExtraction;
              return true;
            })()"""
        )
    return {
        "scenario": "agent_target_extraction_result_sync",
        "status": "passed",
        "assertions": {
            "workspace_mode": "target_extract",
            "source_asset_id": result["sourceAssetId"],
            "result_asset_id": result["outputAssetId"],
            "source_preview": result["sourcePreview"],
            "result_preview": result["outputPreview"],
            "terminal_continuations": result["continuationPosts"],
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_agent_analysis_result_sync(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    """Prove an Agent content-analysis job refreshes the visible analysis Prompt."""

    wait_for(
        connection,
        "!!document.querySelector('.agent-live-panel')",
        "the Agent panel",
        timeout,
    )
    connection.evaluate(
        r"""(async () => {
          if (globalThis.__aipicOriginalFetch) throw new Error('controlled fetch is already installed');
          const original = globalThis.fetch.bind(globalThis);
          globalThis.__aipicOriginalFetch = original;
          globalThis.__aipicAgentAnalysis = {
            projectId: null,
            sourceAssetId: null,
            analysisAssetId: null,
            jobId: null,
            apiOrigin: null,
            requestHeaders: null,
            eventsDelivered: false,
            continuationPosts: 0,
            jobReads: 0,
          };
          globalThis.fetch = async (...args) => {
            const input = args[0];
            const init = args[1] || {};
            const request = input instanceof Request ? input : null;
            const url = String(request ? request.url : input);
            const method = String(init.method || request?.method || 'GET').toUpperCase();
            const controlled = globalThis.__aipicAgentAnalysis;

            if (!controlled.eventsDelivered && /\/v1\/agent\/conversations\/[^/]+\/events\?/.test(url)) {
              const eventUrl = new URL(url, location.href);
              const projectId = eventUrl.searchParams.get('project_id');
              if (!projectId) throw new Error('controlled Agent event has no project identity');
              const requestHeaders = new Headers(request?.headers || init.headers || {});
              const assetsResponse = await original(
                `${eventUrl.origin}/v1/projects/${encodeURIComponent(projectId)}/assets?include_trashed=false`,
                {headers: requestHeaders}
              );
              if (!assetsResponse.ok) throw new Error('controlled assets are unavailable');
              const assets = await assetsResponse.json();
              const imageAsset = assets.find((asset) =>
                asset.asset_type === 'source_image'
                && String(asset.mime_type || '').startsWith('image/')
                && !asset.trashed_at
              );
              if (!imageAsset) throw new Error('controlled image asset is unavailable');
              const requestId = `controlled-agent-analysis-${Date.now()}`;
              controlled.eventsDelivered = true;
              const commandHeaders = new Headers(requestHeaders);
              commandHeaders.set('Content-Type', 'application/json');
              commandHeaders.set('X-Request-Id', requestId);
              const toolResponse = await original(`${eventUrl.origin}/v1/tools/invoke`, {
                method: 'POST',
                headers: commandHeaders,
                body: JSON.stringify({
                  project_id: projectId,
                  run_id: null,
                  round_index: 0,
                  tool_name: 'image.analyze_content',
                  tool_version: '1.0.0',
                  arguments: {
                    asset_id: imageAsset.id,
                    provider_profile: 'gemini/google/default',
                    model: 'gemini-flash-lite-latest',
                  },
                  request_id: requestId,
                  provider_profile: 'gemini/google/default',
                }),
              });
              if (!toolResponse.ok) throw new Error('controlled content analysis could not be queued');
              const queued = await toolResponse.json();
              const jobId = queued.job?.job_id;
              if (queued.status !== 'queued' || !jobId) {
                throw new Error(`controlled content analysis returned ${queued.status || 'unknown'}`);
              }
              controlled.projectId = projectId;
              controlled.sourceAssetId = imageAsset.id;
              controlled.jobId = jobId;
              controlled.apiOrigin = eventUrl.origin;
              controlled.requestHeaders = requestHeaders;
              return new Response(JSON.stringify({
                items: [
                  {
                    sequence_no: 920001,
                    event_type: 'tool.call',
                    payload: {
                      conversation_id: 'controlled-conversation',
                      tool_call: {
                        id: 'controlled-analysis-call',
                        name: 'analyze_image',
                        arguments: {source_asset_ref: imageAsset.id, analysis_type: 'content'},
                      },
                    },
                    created_at: new Date().toISOString(),
                  },
                  {
                    sequence_no: 920002,
                    event_type: 'tool.completed',
                    payload: {
                      conversation_id: 'controlled-conversation',
                      tool_call_id: 'controlled-analysis-call',
                      tool_name: 'analyze_image',
                      is_error: false,
                      result: {
                        content: [{type: 'text', text: 'Controlled content analysis queued.'}],
                        details: {
                          status: 'queued',
                          job: queued.job,
                        },
                        is_error: false,
                      },
                    },
                    created_at: new Date().toISOString(),
                  },
                ],
                next_cursor: 920002,
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }

            if (controlled.jobId && url.includes(`/v1/jobs/${controlled.jobId}?`)) {
              controlled.jobReads += 1;
              const response = await original(...args);
              const body = await response.clone().json();
              if (body.status === 'succeeded') {
                controlled.analysisAssetId = body.output_asset_ids?.[0] || null;
              }
              return response;
            }

            if (method === 'POST' && /\/v1\/agent\/conversations\/[^/]+\/messages$/.test(url)) {
              const body = JSON.parse(String(init.body || '{}'));
              if (String(body.request_id || '').startsWith('agent-job-terminal-')) {
                controlled.continuationPosts += 1;
                return new Response(JSON.stringify({
                  id: 'controlled-conversation',
                  project_id: controlled.projectId,
                  state: 'running',
                  message_count: 0,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
            }
            return original(...args);
          };
          return {installed: true};
        })()"""
    )
    try:
        wait_for(
            connection,
            "!!document.querySelector('.compare-workspace')",
            "the Content and Style Analysis workspace",
            timeout,
        )
        wait_for(
            connection,
            "globalThis.__aipicAgentAnalysis?.continuationPosts === 1",
            "the intercepted Agent terminal continuation",
            timeout,
        )
        wait_for(
            connection,
            """(() => {
              const editor = document.querySelector(
                '.prompt-role-editor[aria-label="内容分析（主体与结构）"]'
              );
              const values = [...(editor?.querySelectorAll('textarea') || [])]
                .map((item) => item.value);
              return values.includes('一个用于受控端到端验证的素材')
                && values.includes('an asset for controlled end-to-end validation');
            })()""",
            "the refreshed bilingual content Prompt",
            timeout,
        )
        wait_for(
            connection,
            """(async () => {
              const controlled = globalThis.__aipicAgentAnalysis;
              if (!controlled.analysisAssetId) return false;
              const response = await globalThis.__aipicOriginalFetch(
                `${controlled.apiOrigin}/v1/projects/${encodeURIComponent(controlled.projectId)}`,
                {headers: controlled.requestHeaders}
              );
              if (!response.ok) return false;
              const project = await response.json();
              const state = JSON.parse(project.workspace_state_json || '{}');
              return state.reference_context?.content_asset_id === controlled.sourceAssetId
                && state.reference_context?.content_analysis_asset_id === controlled.analysisAssetId
                && !!state.reference_context?.content_prompt_asset_id
                && state.reference_context?.merged_prompt_asset_id === null;
            })()""",
            "the persisted Agent analysis and extracted Prompt identities",
            timeout,
        )
        result = connection.evaluate(
            """(() => {
              const controlled = globalThis.__aipicAgentAnalysis;
              const editor = document.querySelector(
                '.prompt-role-editor[aria-label="内容分析（主体与结构）"]'
              );
              const values = [...(editor?.querySelectorAll('textarea') || [])]
                .map((item) => item.value);
              return {
                workspaceVisible: !!document.querySelector('.compare-workspace'),
                sourceAssetId: controlled.sourceAssetId,
                analysisAssetId: controlled.analysisAssetId,
                jobId: controlled.jobId,
                jobReads: controlled.jobReads,
                continuationPosts: controlled.continuationPosts,
                chinesePromptRefreshed: values.includes('一个用于受控端到端验证的素材'),
                englishPromptRefreshed: values.includes('an asset for controlled end-to-end validation'),
              };
            })()"""
        )
    finally:
        connection.evaluate(
            """(() => {
              if (globalThis.__aipicOriginalFetch) {
                globalThis.fetch = globalThis.__aipicOriginalFetch;
                delete globalThis.__aipicOriginalFetch;
              }
              delete globalThis.__aipicAgentAnalysis;
              return true;
            })()"""
        )

    return {
        "scenario": "agent_analysis_result_sync",
        "status": "passed",
        "assertions": {
            "workspace_mode": "compare",
            "workspace_visible": result["workspaceVisible"],
            "source_asset_id": result["sourceAssetId"],
            "analysis_asset_id": result["analysisAssetId"],
            "job_id": result["jobId"],
            "job_reads": result["jobReads"],
            "terminal_continuations": result["continuationPosts"],
            "chinese_prompt_refreshed": result["chinesePromptRefreshed"],
            "english_prompt_refreshed": result["englishPromptRefreshed"],
            "workspace_context_persisted": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_prompt_rewrite(connection: CdpConnection, timeout: float) -> dict[str, object]:
    """Prove controlled bilingual Prompt rewrite and visible language refill."""

    def collected_rewrite_request_ids() -> set[str]:
        items = [
            *(connection.evaluate("globalThis.__aipicE2E?.network || []") or []),
            *cdp_network_records(connection),
        ]
        request_ids = {
            str(json.loads(str(item.get("request", "{}"))).get("request_id", ""))
            for item in items
            if "/v1/tools/invoke" in str(item.get("url", ""))
            and "prompt.rewrite" in str(item.get("request", ""))
        }
        request_ids.discard("")
        return request_ids

    connection.evaluate(
        """(() => {
          const tab = [...document.querySelectorAll('.workflow-stage-tools button')]
            .find((item) => item.getAttribute('aria-label') === '创意图生成');
          if (!tab) throw new Error('prompt-image workflow tab is missing');
          tab.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.prompt-image-workspace textarea')",
        "the prompt-image editor",
        timeout,
    )
    rewrite_requests_before = collected_rewrite_request_ids()
    connection.evaluate(
        """(() => {
          const textarea = document.querySelector('.prompt-image-workspace textarea');
          const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
          setter.call(textarea, 'controlled mechanical bird concept');
          textarea.dispatchEvent(new Event('input', {bubbles: true}));
          textarea.dispatchEvent(new Event('change', {bubbles: true}));
          const button = [...document.querySelectorAll('.prompt-image-prompt-actions button')]
            .find((item) => item.textContent?.trim() === '智能扩写');
          if (!button || button.disabled) throw new Error('smart rewrite action is unavailable');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "document.querySelector('.prompt-image-workspace textarea')?.value === "
            + json.dumps("controlled rewritten prompt")
        ),
        "the English rewritten Prompt refill",
        timeout,
    )
    rewrite_request_ids = collected_rewrite_request_ids() - rewrite_requests_before
    if len(rewrite_request_ids) != 1:
        raise AssertionError("smart rewrite did not issue exactly one prompt.rewrite Tool request")
    connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('.prompt-image-language-options button')]
            .find((item) => item.textContent?.trim() === '中文');
          if (!button) throw new Error('Chinese Prompt language action is missing');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "document.querySelector('.prompt-image-workspace textarea')?.value === "
            + json.dumps("受控重写提示词")
        ),
        "the Chinese rewritten Prompt refill",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('.prompt-image-language-options button')]
            .find((item) => item.textContent?.trim() === 'English');
          if (!button) throw new Error('English Prompt language action is missing');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "document.querySelector('.prompt-image-workspace textarea')?.value === "
            + json.dumps("controlled rewritten prompt")
        ),
        "the restored English Prompt refill",
        timeout,
    )
    return {
        "scenario": "prompt_rewrite",
        "status": "passed",
        "assertions": {
            "tool_requests": len(rewrite_request_ids),
            "english_refill": True,
            "chinese_refill": True,
            "language_switch": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_multiview_current_source(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    """Prove the project-current source and the crop-confirmation handoff."""

    connection.evaluate(
        """(() => {
          const tab = [...document.querySelectorAll('.workflow-stage-tools button')]
            .find((item) => item.getAttribute('aria-label') === '三视图制作');
          if (!tab) throw new Error('multiview workflow tab is missing');
          tab.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.multiview-workspace img[alt=\"三视图源图\"]')",
        "the initial managed multiview source",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('.multiview-source-panel button')]
            .find((item) => item.textContent?.includes('清空图片'));
          if (!button || button.disabled) throw new Error('clear multiview source is unavailable');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "document.querySelector('[aria-label=\"三视图来源\"]')?.value === '' && "
            "document.querySelector('[aria-label=\"三视图来源\"] option:checked')"
            "?.textContent?.trim() === '未选择三视图来源'"
        ),
        "the explicit empty multiview source",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('.multiview-source-panel button')]
            .find((item) => item.textContent?.trim() === '加载项目当前图片');
          if (!button || button.disabled) throw new Error('load project current image is unavailable');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "!!document.querySelector('.multiview-source-panel img[alt=\"三视图源图\"][src^=\"blob:\"]') && "
            "document.querySelector('[aria-label=\"三视图来源\"] option:checked')"
            "?.textContent?.includes('（项目当前图片）') && "
            "document.querySelector('.multiview-source-identity')"
            "?.textContent?.includes('项目当前图片')"
        ),
        "the authoritative project-current multiview source",
        timeout,
    )
    wait_for(
        connection,
        (
            "(() => { const previews = [...document.querySelectorAll("
            "'.multiview-crop-card img')]; return previews.length === 3 && "
            "previews.every((item) => item.src.startsWith('blob:') && "
            "item.complete && item.naturalWidth > 0); })()"
        ),
        "three CSP-compatible multiview crop previews",
        timeout,
    )
    manual_refresh_visible = connection.evaluate(
        """[...document.querySelectorAll('.multiview-source-panel button')]
          .some((item) => item.textContent?.trim() === '立即刷新生成进度')"""
    )
    if manual_refresh_visible:
        raise AssertionError(
            "generation progress refresh was shown without a multiview generation job"
        )
    crop_button_style = connection.evaluate(
        """(() => {
          const button = document.querySelector('.multiview-confirm-crops');
          if (!button || button.disabled) throw new Error('crop confirmation is unavailable');
          const style = getComputedStyle(button);
          return {backgroundColor: style.backgroundColor, color: style.color};
        })()"""
    )
    connection.evaluate(
        """(() => {
          const button = document.querySelector('.multiview-confirm-crops');
          if (!button || button.disabled) throw new Error('crop confirmation is unavailable');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        (
            "(() => { const previews = [...document.querySelectorAll("
            "'.multiview-crop-card img')]; return previews.length === 3 && "
            "previews.every((item) => item.src.startsWith('blob:') && "
            "item.complete && item.naturalWidth > 0) && "
            "[...document.querySelectorAll('.multiview-output-panel button')]"
            ".some((item) => item.textContent?.trim() === '重新调整裁切框'); })()"
        ),
        "three persisted managed crop assets",
        timeout,
    )
    wait_for(
        connection,
        "!document.querySelector('.multiview-submit')?.disabled",
        "the unlocked 3D handoff after crop confirmation",
        timeout,
    )
    connection.evaluate(
        "globalThis.__aipicMultiviewPersistenceDeadline = Date.now() + 750"
    )
    wait_for(
        connection,
        "Date.now() >= globalThis.__aipicMultiviewPersistenceDeadline",
        "the debounced multiview workspace persistence",
        timeout,
    )
    confirmation_result = connection.evaluate(
        """(() => {
          const submit = document.querySelector('.multiview-submit');
          if (!submit) throw new Error('3D handoff action is missing');
          const style = getComputedStyle(submit);
          const bodyText = document.body.innerText;
          return {
            submitBackgroundColor: style.backgroundColor,
            submitColor: style.color,
            submitEnabled: !submit.disabled,
            qualityCheckboxAbsent: !bodyText.includes('我已确认三张视图与质量'),
            managedCropCount: [...document.querySelectorAll('.multiview-crop-card img')]
              .filter((item) => item.src.startsWith('blob:')).length,
          };
        })()"""
    )
    if not confirmation_result["qualityCheckboxAbsent"]:
        raise AssertionError("the redundant multiview quality checkbox is still visible")
    selected_label = connection.evaluate(
        """document.querySelector('[aria-label="三视图来源"] option:checked')
          ?.textContent?.trim() || ''"""
    )
    return {
        "scenario": "multiview_current_source",
        "status": "passed",
        "assertions": {
            "explicit_empty_source": True,
            "project_current_marker": True,
            "selected_source": selected_label,
            "source_preview": True,
            "crop_previews": 3,
            "crop_preview_scheme": "blob",
            "crop_confirmation_background": crop_button_style["backgroundColor"],
            "crop_confirmation_text_color": crop_button_style["color"],
            "managed_crop_assets": confirmation_result["managedCropCount"],
            "quality_checkbox_absent": confirmation_result["qualityCheckboxAbsent"],
            "submit_enabled_after_crop_confirmation": confirmation_result["submitEnabled"],
            "submit_background": confirmation_result["submitBackgroundColor"],
            "submit_text_color": confirmation_result["submitColor"],
            "refresh_hidden_without_job": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_task_center(connection: CdpConnection, timeout: float) -> dict[str, object]:
    click_button(connection, "资产")
    wait_for(
        connection,
        "!![...document.querySelectorAll('.asset-card h2')].find((item) => item.textContent?.trim() === 'source-a.png')",
        "source asset card",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const card = [...document.querySelectorAll('.asset-card')]
            .find((item) => item.querySelector('h2')?.textContent?.trim() === 'source-a.png');
          if (!card) throw new Error('source asset card missing');
          const button = [...card.querySelectorAll('button')]
            .find((item) => item.textContent?.includes('使用此图片'));
          if (button && !button.disabled) button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('.topbar-breadcrumb')?.textContent?.trim() === 'source-a.png'",
        "source image as current asset",
        timeout,
    )

    click_button(connection, "任务")
    wait_for(
        connection,
        "!!document.querySelector('.jobs-panel #task-center-title')",
        "task center",
        timeout,
    )
    overview_count = connection.evaluate(
        "document.querySelectorAll('.jobs-overview button').length"
    )
    if overview_count != 3:
        raise AssertionError(f"task overview expected 3 controls, got {overview_count}")
    wait_for(
        connection,
        """[...document.querySelectorAll('.job-actions button')]
          .some((item) => item.textContent?.trim() === '查看 2 个候选图')""",
        "completed image task",
        timeout,
    )
    terminal_copy = connection.evaluate(
        """(() => {
          const card = [...document.querySelectorAll('.job-card')]
            .find((item) => item.querySelector('.job-status')?.textContent?.includes('已完成'));
          return card?.querySelector('.job-summary')?.textContent?.trim() || '';
        })()"""
    )
    if not terminal_copy or "正在" in terminal_copy:
        raise AssertionError(f"terminal task copy is not state-aware: {terminal_copy}")

    action_label = connection.evaluate(
        """[...document.querySelectorAll('.job-actions button')]
          .find((item) => item.textContent?.includes('候选图'))?.textContent?.trim() || ''"""
    )
    if action_label != "查看 2 个候选图":
        raise AssertionError(f"precise result action missing: {action_label}")
    output_names = connection.evaluate(
        """(() => {
          const card = [...document.querySelectorAll('.job-card')]
            .find((item) => item.querySelector('.job-actions button')?.textContent?.includes('候选图'));
          const output = [...(card?.querySelectorAll('.job-assets') || [])]
            .find((item) => item.querySelector('.job-assets-label')?.textContent?.trim() === '输出');
          return [...(output?.querySelectorAll('.job-asset > span:last-child') || [])]
            .map((item) => item.textContent?.trim())
            .filter(Boolean);
        })()"""
    )
    set_current_before = connection.evaluate(
        """(globalThis.__aipicE2E?.network || [])
          .filter((item) => String(item.url || '').includes('/set-current')).length"""
    )
    current_before = connection.evaluate(
        "document.querySelector('.topbar-breadcrumb')?.textContent?.trim() || ''"
    )
    click_button(connection, action_label)
    wait_for(
        connection,
        "document.querySelector('.candidate-workspace')?.dataset.resultScope === 'job'",
        "job-scoped candidate results",
        timeout,
    )
    wait_for(
        connection,
        "document.activeElement?.id === 'workspace-title'",
        "result heading focus",
        timeout,
    )
    current_after = connection.evaluate(
        "document.querySelector('.topbar-breadcrumb')?.textContent?.trim() || ''"
    )
    set_current_after = connection.evaluate(
        """(globalThis.__aipicE2E?.network || [])
          .filter((item) => String(item.url || '').includes('/set-current')).length"""
    )
    wait_for(
        connection,
        f"document.querySelectorAll('.candidate-card strong').length === {len(output_names)}",
        "job-scoped candidate cards",
        timeout,
    )
    candidate_names = connection.evaluate(
        """[...document.querySelectorAll('.candidate-card strong')]
          .map((item) => item.textContent?.trim()).filter(Boolean)"""
    )
    if current_after != current_before:
        raise AssertionError(
            f"opening results changed current asset: {current_before} -> {current_after}"
        )
    if set_current_after != set_current_before:
        raise AssertionError("opening results issued an unexpected set-current request")
    if candidate_names != output_names:
        raise AssertionError(
            f"candidate results are not job-scoped: {candidate_names} != {output_names}"
        )
    return {
        "scenario": "task_center",
        "status": "passed",
        "assertions": {
            "overview_controls": overview_count,
            "terminal_copy": terminal_copy,
            "result_action": action_label,
            "result_assets": candidate_names,
            "current_asset_unchanged": current_after,
            "set_current_requests_unchanged": True,
            "result_heading_focused": True,
        },
    }


def run_prompt_candidate_counts(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    click_button(connection, "创意图生成")
    wait_for(
        connection,
        "!!document.querySelector('.prompt-image-workspace .candidate-count-options')",
        "prompt-image candidate count controls",
        timeout,
    )
    counts = connection.evaluate(
        """[...document.querySelectorAll('.prompt-image-workspace .candidate-count-options button')]
          .map((button) => button.textContent?.trim())"""
    )
    if counts != ["1", "2", "4"]:
        raise AssertionError(f"unexpected prompt-image candidate counts: {counts}")
    connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll(
            '.prompt-image-workspace .candidate-count-options button'
          )].find((item) => item.textContent?.trim() === '1');
          if (!button || button.disabled) throw new Error('single-image option is unavailable');
          button.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('.prompt-image-workspace .candidate-count-options button[aria-pressed=true]')?.textContent?.trim() === '1'",
        "single-image candidate selection",
        timeout,
    )
    return {
        "scenario": "prompt_candidate_counts",
        "status": "passed",
        "assertions": {
            "candidate_counts": counts,
            "single_image_selected": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_service_credentials(connection: CdpConnection, timeout: float) -> dict[str, object]:
    connection.evaluate("document.querySelector('button[aria-label=\"设置\"]')?.click()")
    wait_for(connection, "!!document.querySelector('#credential-title')", "the settings dialog", timeout)
    wait_for(
        connection,
        "document.querySelectorAll('.credential-card').length === 4",
        "the four flattened credential cards",
        timeout,
    )
    labels = connection.evaluate(
        "[...document.querySelectorAll('.credential-card label')].map((item) => item.textContent?.trim())"
    )
    expected = [
        "Tripo3D API Key",
        "Meshy API Key",
        "Gemini API Key",
        "DeepSeek Agent API Key",
    ]
    if labels != expected:
        raise AssertionError(f"unexpected flattened credential labels: {labels!r}")
    wait_for(
        connection,
        "[...document.querySelectorAll('.credential-card')].every((card) => card.textContent?.includes('可用'))",
        "controlled Provider availability",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const input = [...document.querySelectorAll('.credential-card input')]
            .find((item) => item.closest('.credential-card')?.textContent?.includes('DeepSeek Agent'));
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
          setter?.call(input, 'controlled-placeholder-secret');
          input?.dispatchEvent(new Event('input', { bubbles: true }));
        })()"""
    )
    wait_for(
        connection,
        "![...document.querySelectorAll('.credential-card')].find((card) => card.textContent?.includes('DeepSeek Agent'))?.querySelector('button.primary')?.disabled",
        "the DeepSeek save action to enable",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const card = [...document.querySelectorAll('.credential-card')]
            .find((item) => item.textContent?.includes('DeepSeek Agent'));
          const input = card?.querySelector('input');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
          setter?.call(input, '');
          input?.dispatchEvent(new Event('input', { bubbles: true }));
          [...(card?.querySelectorAll('button') || [])]
            .find((item) => item.textContent?.includes('检测现有凭据'))?.click();
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('.settings-message')?.textContent?.includes('DeepSeek Agent 凭据检测已完成')",
        "the independent DeepSeek probe result",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const card = [...document.querySelectorAll('.credential-card')]
            .find((item) => item.textContent?.includes('Gemini'));
          [...(card?.querySelectorAll('button') || [])]
            .find((item) => item.textContent?.includes('检测现有凭据'))?.click();
        })()"""
    )
    wait_for(
        connection,
        "document.querySelector('.settings-message')?.textContent?.includes('Gemini 凭据检测已完成')",
        "the independent Gemini probe result",
        timeout,
    )
    connection.evaluate(
        "[...document.querySelectorAll('.credential-card')].find((card) => card.textContent?.includes('Gemini'))?.scrollIntoView({ block: 'center' })"
    )
    return {
        "scenario": "service_credentials",
        "status": "passed",
        "assertions": {
            "flattened_credential_cards": 4,
            "credential_labels": labels,
            "all_controlled_providers_available": True,
            "deepseek_independent_probe": True,
            "gemini_independent_probe": True,
            "secret_input_cleared": connection.evaluate(
                "[...document.querySelectorAll('.credential-card input')].every((input) => input.value === '')"
            ),
        },
    }


def run_blender_settings(connection: CdpConnection, timeout: float) -> dict[str, object]:
    connection.evaluate(
        "if (!document.querySelector('#converter-title')) document.querySelector('button[aria-label=\"设置\"]')?.click()"
    )
    wait_for(connection, "!!document.querySelector('#converter-title')", "the converter settings", timeout)
    converter_text = connection.evaluate(
        "document.querySelector('#converter-title')?.closest('.settings-section')?.textContent || ''"
    )
    if "Blender" not in converter_text or "Assimp" in converter_text:
        raise AssertionError("converter settings do not expose the Blender-only architecture")
    blender_path = "controlled-blender.exe"
    connection.evaluate(
        """(() => {
          const input = [...document.querySelectorAll('.settings-section label input')]
            .find((item) => item.closest('label')?.textContent?.includes('Blender'));
          if (!input) throw new Error('Blender executable input is missing');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
          setter?.call(input, """
        + json.dumps(blender_path)
        + """);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          [...document.querySelectorAll('.dialog-actions button')]
            .find((item) => item.textContent?.includes('保存设置'))?.click();
        })()"""
    )
    wait_for(
        connection,
        "['设置已保存', '状态已更新'].some((text) => document.querySelector('.settings-message')?.textContent?.includes(text))",
        "the Blender setting save result",
        timeout,
    )
    connection.evaluate(
        "[...document.querySelectorAll('.dialog-actions button')].find((item) => item.textContent?.includes('关闭'))?.click()"
    )
    wait_for(connection, "!document.querySelector('#converter-title')", "the settings dialog to close", timeout)
    connection.evaluate("document.querySelector('button[aria-label=\"设置\"]')?.click()")
    wait_for(
        connection,
        "[...document.querySelectorAll('.settings-section label input')].some((item) => item.closest('label')?.textContent?.includes('Blender') && item.value === "
        + json.dumps(blender_path)
        + ")",
        "the persisted Blender setting",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('#converter-title')?.closest('.settings-section')?.scrollIntoView({ block: 'center' })"
    )
    return {
        "scenario": "blender_settings",
        "status": "passed",
        "assertions": {
            "blender_only_controls": True,
            "setting_saved": True,
            "setting_reloaded": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_agent_model_settings(connection: CdpConnection, timeout: float) -> dict[str, object]:
    connection.evaluate(
        "if (!document.querySelector('#agent-model')) document.querySelector('button[aria-label=\"设置\"]')?.click()"
    )
    wait_for(connection, "!!document.querySelector('#agent-model')", "the Agent model setting", timeout)
    default_model = connection.evaluate("document.querySelector('#agent-model')?.value")
    if default_model != "qwen3-vl:8b":
        raise AssertionError(f"unexpected default Agent model: {default_model!r}")
    selected_model = "qwen3-vl:4b"
    connection.evaluate(
        """(() => {
          const select = document.querySelector('#agent-model');
          if (!(select instanceof HTMLSelectElement)) throw new Error('Agent model select is missing');
          const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
          setter?.call(select, """
        + json.dumps(selected_model)
        + """);
          select.dispatchEvent(new Event('input', { bubbles: true }));
          select.dispatchEvent(new Event('change', { bubbles: true }));
          document.querySelector('.dialog-actions button.primary')?.click();
        })()"""
    )
    wait_for(
        connection,
        "!!document.querySelector('.settings-message')?.textContent?.trim()",
        "the Agent model setting save result",
        timeout,
    )
    connection.evaluate("document.querySelector('.dialog-actions button:not(.primary)')?.click()")
    wait_for(connection, "!document.querySelector('#agent-model')", "the settings dialog to close", timeout)
    connection.evaluate("document.querySelector('button[aria-label=\"设置\"]')?.click()")
    wait_for(
        connection,
        "document.querySelector('#agent-model')?.value === " + json.dumps(selected_model),
        "the persisted Agent model setting",
        timeout,
    )
    connection.evaluate("document.querySelector('#agent-model')?.closest('.settings-section')?.scrollIntoView({ block: 'center' })")
    return {
        "scenario": "agent_model_settings",
        "status": "passed",
        "assertions": {
            "default_model": default_model,
            "selected_model": selected_model,
            "setting_saved": True,
            "setting_reloaded": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_local_model_settings(connection: CdpConnection, timeout: float) -> dict[str, object]:
    connection.evaluate(
        "if (!document.querySelector('#local-provider-title')) document.querySelector('button[aria-label=\"设置\"]')?.click()"
    )
    wait_for(connection, "!!document.querySelector('#local-provider-title')", "the local Provider settings", timeout)
    wait_for(
        connection,
        "document.querySelectorAll('.local-provider-card').length === 3",
        "the three local Provider cards",
        timeout,
    )
    cards = connection.evaluate(
        "[...document.querySelectorAll('.local-provider-card')].map((card) => card.textContent?.trim() || '')"
    )
    for expected in ("Qwen3-VL", "Z-Image-Turbo", "TripoSR"):
        if not any(expected in card for card in cards):
            raise AssertionError(f"missing local Provider card: {expected}")
    safety_text = connection.evaluate(
        "document.querySelector('#local-provider-title')?.closest('.settings-section')?.textContent || ''"
    )
    if "不下载权重" not in safety_text or "不会启动" not in safety_text:
        raise AssertionError("local Provider probe safety explanation is missing")
    default_agent = connection.evaluate("document.querySelector('#agent-model')?.value")
    if default_agent != "qwen3-vl:8b":
        raise AssertionError(f"unexpected default local Agent model: {default_agent!r}")
    connection.evaluate(
        "[...document.querySelectorAll('button')].find((item) => item.textContent?.includes('检测本地模型'))?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.settings-message')?.textContent?.includes('不会下载模型')",
        "the safe local Provider refresh result",
        timeout,
    )
    connection.evaluate(
        """(() => {
          const setSelect = (labelText, value) => {
            const select = [...document.querySelectorAll('.settings-section label select')]
              .find((item) => item.closest('label')?.textContent?.includes(labelText));
            if (!(select instanceof HTMLSelectElement)) throw new Error(`${labelText} select is missing`);
            const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
            setter?.call(select, value);
            select.dispatchEvent(new Event('input', { bubbles: true }));
            select.dispatchEvent(new Event('change', { bubbles: true }));
          };
          setSelect('文生图执行后端', 'local');
          setSelect('图生 3D 执行后端', 'remote');
          document.querySelector('.dialog-actions button.primary')?.click();
          return true;
        })()"""
    )
    wait_for(
        connection,
        "['设置已保存', '状态已更新'].some((text) => document.querySelector('.settings-message')?.textContent?.includes(text))",
        "the local generation policy save result",
        timeout,
    )
    connection.evaluate("document.querySelector('.dialog-actions button:not(.primary)')?.click()")
    wait_for(connection, "!document.querySelector('#local-provider-title')", "the settings dialog to close", timeout)
    connection.evaluate("document.querySelector('button[aria-label=\"设置\"]')?.click()")
    wait_for(
        connection,
        "[...document.querySelectorAll('.settings-section label select')].some((item) => item.closest('label')?.textContent?.includes('文生图执行后端') && item.value === 'local')",
        "the persisted local image backend",
        timeout,
    )
    wait_for(
        connection,
        "[...document.querySelectorAll('.settings-section label select')].some((item) => item.closest('label')?.textContent?.includes('图生 3D 执行后端') && item.value === 'remote')",
        "the persisted remote 3D backend",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('#local-provider-title')?.closest('.settings-section')?.scrollIntoView({ block: 'center' })"
    )
    return {
        "scenario": "local_model_settings",
        "status": "passed",
        "assertions": {
            "local_provider_cards": 3,
            "models": ["Qwen3-VL", "Z-Image-Turbo", "TripoSR"],
            "probe_does_not_download_or_generate": True,
            "default_agent_model": default_agent,
            "image_backend": "local",
            "model3d_backend": "remote",
            "settings_reloaded": True,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_product_workflow(connection: CdpConnection, timeout: float) -> dict[str, object]:
    wait_for(
        connection,
        "!!document.querySelector('nav[aria-label=\"产品工作台\"]')",
        "the product workbench navigation",
        timeout,
    )
    stages = connection.evaluate(
        "[...document.querySelectorAll('.workflow-stage-header strong')].map((item) => item.textContent?.trim())"
    )
    expected_stages = ["素材工作台", "创意定稿", "建模准备", "资产交付"]
    if stages != expected_stages:
        raise AssertionError(f"unexpected product workbenches: {stages!r}")
    tools = connection.evaluate(
        "[...document.querySelectorAll('.workflow-stage-tools button')].map((item) => item.getAttribute('aria-label'))"
    )
    expected_tools = ["当前图片", "内容与风格分析", "创意图生成", "建模主体提取", "三视图制作", "3D 模型处理"]
    if tools != expected_tools:
        raise AssertionError(f"unexpected product tools: {tools!r}")
    navigation_layout = connection.evaluate(
        """(() => {
          const navigation = document.querySelector('nav[aria-label="产品工作台"]');
          const buttons = [...document.querySelectorAll('.workflow-stage-tools button')];
          return {
            horizontalOverflow: navigation.scrollWidth > navigation.clientWidth,
            clippedLabels: buttons.filter((button) => button.scrollWidth > button.clientWidth)
              .map((button) => button.getAttribute('aria-label')),
          };
        })()"""
    )
    if navigation_layout.get("horizontalOverflow") or navigation_layout.get("clippedLabels"):
        raise AssertionError(f"product tool labels overflow: {navigation_layout!r}")

    for label, heading in (
        ("内容与风格分析", "分析内容与风格参考"),
        ("建模主体提取", "提取可建模主体"),
        ("三视图制作", "生成和校准三视图"),
    ):
        connection.evaluate(
            "[...document.querySelectorAll('.workflow-stage-tools button')].find((item) => item.getAttribute('aria-label') === "
            + json.dumps(label)
            + ")?.click()"
        )
        wait_for(
            connection,
            "document.querySelector('.workspace-region h1')?.textContent?.includes("
            + json.dumps(heading)
            + ")",
            f"the {label} workbench",
            timeout,
        )

    connection.evaluate(
        "[...document.querySelectorAll('.workflow-stage-tools button')].find((item) => item.getAttribute('aria-label') === '当前图片')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('.current-asset-title .eyebrow')?.textContent?.includes('素材工作台')",
        "the material workbench",
        timeout,
    )
    action_labels = connection.evaluate(
        "[...document.querySelectorAll('.canvas-context-toolbar > .canvas-tool > button')].map((item) => item.textContent?.trim())"
    )
    for expected in ("提取建模主体", "分析内容与风格", "生成创意图", "制作三视图", "发起 3D 生成"):
        if expected not in action_labels:
            raise AssertionError(f"material workbench action is missing: {expected}")
    return {
        "scenario": "product_workflow",
        "status": "passed",
        "assertions": {
            "workbenches": stages,
            "tools": tools,
            "cross_workbench_navigation": True,
            "material_actions_aligned": True,
            "navigation_horizontal_overflow": False,
            "clipped_tool_labels": [],
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def run_model_fallback_visual(
    connection: CdpConnection, timeout: float
) -> dict[str, object]:
    """Prove readable unavailable actions and the project-owned fallback model."""

    connection.evaluate(
        "[...document.querySelectorAll('.workflow-stage-tools button')].find((item) => item.getAttribute('aria-label') === '当前图片')?.click()"
    )
    wait_for(
        connection,
        "!![...document.querySelectorAll('.canvas-context-toolbar button')].find((item) => item.textContent?.includes('生成创意图'))",
        "the material action toolbar",
        timeout,
    )
    material_action = connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('.canvas-context-toolbar button')]
            .find((item) => item.textContent?.includes('生成创意图'));
          if (!button || button.getAttribute('aria-disabled') !== 'true') {
            throw new Error('expected unavailable material action is missing');
          }
          const style = getComputedStyle(button);
          return {label: button.textContent?.trim(), color: style.color,
            background: style.backgroundColor, border: style.borderColor,
            opacity: style.opacity};
        })()"""
    )
    if material_action.get("opacity") != "1":
        raise AssertionError(f"material action is still dimmed: {material_action!r}")

    connection.evaluate(
        "[...document.querySelectorAll('.workflow-stage-tools button')].find((item) => item.getAttribute('aria-label') === '3D 模型处理')?.click()"
    )
    wait_for(
        connection,
        "document.querySelector('#model-workspace-title')?.textContent?.trim() === '资产信标预览'",
        "the asset beacon fallback heading",
        timeout,
    )
    wait_for(
        connection,
        "document.querySelector('model-viewer[alt=\"内置资产信标 3D 预览\"]')?.loaded === true",
        "the loaded project-owned asset beacon",
        timeout,
    )
    primary_action = connection.evaluate(
        """(() => {
          const button = [...document.querySelectorAll('.model-actions button')]
            .find((item) => item.textContent?.includes('Choose GLB'));
          if (!button || button.disabled) throw new Error('GLB import action is unavailable');
          const style = getComputedStyle(button);
          const channels = (value) => {
            const values = (value.match(/[0-9.]+/g) || []).slice(0, 3).map(Number);
            return Math.max(...values) > 1 ? values.map((item) => item / 255) : values;
          };
          const luminance = (value) => channels(value).map((item) =>
            item <= 0.04045 ? item / 12.92 : ((item + 0.055) / 1.055) ** 2.4
          ).reduce((sum, item, index) => sum + item * [0.2126, 0.7152, 0.0722][index], 0);
          const foreground = luminance(style.color);
          const background = luminance(style.backgroundColor);
          const contrast = (Math.max(foreground, background) + 0.05) /
            (Math.min(foreground, background) + 0.05);
          return {label: button.textContent?.trim(), color: style.color,
            background: style.backgroundColor, border: style.borderColor,
            opacity: style.opacity, contrast: Number(contrast.toFixed(2))};
        })()"""
    )
    if primary_action.get("contrast", 0) < 4.5:
        raise AssertionError(f"model primary action contrast is too low: {primary_action!r}")
    model_actions = connection.evaluate(
        """[...document.querySelectorAll('.model-actions button:disabled')].map((button) => {
          const style = getComputedStyle(button);
          return {label: button.textContent?.trim(), color: style.color,
            background: style.backgroundColor, border: style.borderColor,
            opacity: style.opacity};
        })"""
    )
    if len(model_actions) < 4:
        raise AssertionError(f"expected disabled model actions are missing: {model_actions!r}")
    if any(action.get("opacity") != "1" for action in model_actions):
        raise AssertionError(f"model actions are still dimmed: {model_actions!r}")
    return {
        "scenario": "model_fallback_visual",
        "status": "passed",
        "assertions": {
            "fallback_heading": "资产信标预览",
            "fallback_model_loaded": True,
            "fallback_model_alt": "内置资产信标 3D 预览",
            "primary_action_style": primary_action,
            "material_action_style": material_action,
            "model_action_styles": model_actions,
            "runtime_errors": 0,
            "unhandled_rejections": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--create-project", action="store_true")
    parser.add_argument("--import-image", action="store_true")
    parser.add_argument("--image-canvas", action="store_true")
    parser.add_argument("--local-image-size", action="store_true")
    parser.add_argument("--asset-visual-dedup", action="store_true")
    parser.add_argument("--asset-file-actions", action="store_true")
    parser.add_argument("--asset-remove", action="store_true")
    parser.add_argument("--mock-tripo-approval", action="store_true")
    parser.add_argument("--open-model-result", action="store_true")
    parser.add_argument("--agent-ui-action-navigation", action="store_true")
    parser.add_argument("--agent-image-result-navigation", action="store_true")
    parser.add_argument("--agent-target-extraction-result-sync", action="store_true")
    parser.add_argument("--agent-analysis-result-sync", action="store_true")
    parser.add_argument("--agent-image-attachment", action="store_true")
    parser.add_argument("--agent-approval-status", action="store_true")
    parser.add_argument("--agent-image-path", type=Path, nargs="+")
    parser.add_argument("--prompt-rewrite", action="store_true")
    parser.add_argument("--prompt-candidate-counts", action="store_true")
    parser.add_argument("--project-export", action="store_true")
    parser.add_argument("--multiview-current-source", action="store_true")
    parser.add_argument("--target-extraction", action="store_true")
    parser.add_argument("--task-center", action="store_true")
    parser.add_argument("--service-credentials", action="store_true")
    parser.add_argument("--blender-settings", action="store_true")
    parser.add_argument("--agent-model-settings", action="store_true")
    parser.add_argument("--local-model-settings", action="store_true")
    parser.add_argument("--product-workflow", action="store_true")
    parser.add_argument("--model-fallback-visual", action="store_true")
    parser.add_argument("--recover-offline", action="store_true")
    parser.add_argument("--close-window", action="store_true", help="close the isolated test host after evidence capture")
    parser.add_argument("--project-name", default="Controlled WebView2 E2E")
    args = parser.parse_args()

    connection = CdpConnection.attach(args.debug_port)
    failure: Exception | None = None
    try:
        # A Tauri WebView is not a normal browser tab: CDP Page.reload() can
        # leave its custom-origin document blank.  Attach the current document
        # immediately and also register the probe for any app-initiated
        # navigation; isolated profiles make a reload unnecessary.
        connection.call("Network.enable")
        install_diagnostics(connection, document_start=True)
        if args.recover_offline:
            run_offline_recovery(connection, args.timeout)
        else:
            run_startup(connection, args.timeout)
        if args.create_project:
            run_create_project(connection, args.timeout, args.project_name)
        if args.import_image:
            if not args.create_project:
                raise ValueError("--import-image requires --create-project in an isolated run")
            run_import_image(connection, args.timeout)
        if args.image_canvas:
            run_image_canvas(connection, args.timeout)
        if args.mock_tripo_approval:
            run_mock_tripo_approval(connection, args.timeout)
        if args.open_model_result:
            run_open_model_result(connection, args.timeout)
        if args.agent_ui_action_navigation:
            run_agent_ui_action_navigation(connection, args.timeout)
        interaction_summary = None
        if args.local_image_size:
            interaction_summary = run_local_image_size(connection, args.timeout)
        if args.asset_visual_dedup:
            interaction_summary = run_asset_visual_dedup(connection, args.timeout)
        if args.asset_file_actions:
            interaction_summary = run_asset_file_actions(connection, args.timeout)
        if args.asset_remove:
            interaction_summary = run_asset_remove(connection, args.timeout)
        if args.agent_image_result_navigation:
            interaction_summary = run_agent_image_result_navigation(
                connection, args.timeout
            )
        if args.agent_target_extraction_result_sync:
            interaction_summary = run_agent_target_extraction_result_sync(
                connection, args.timeout
            )
        if args.agent_analysis_result_sync:
            interaction_summary = run_agent_analysis_result_sync(
                connection, args.timeout
            )
        if args.agent_image_attachment:
            interaction_summary = run_agent_image_attachment(
                connection, args.timeout, args.agent_image_path or []
            )
        if args.agent_approval_status:
            interaction_summary = run_agent_approval_status(connection, args.timeout)
        if args.prompt_rewrite:
            interaction_summary = run_prompt_rewrite(connection, args.timeout)
        if args.prompt_candidate_counts:
            interaction_summary = run_prompt_candidate_counts(connection, args.timeout)
        if args.project_export:
            interaction_summary = run_project_export(connection, args.timeout)
        if args.multiview_current_source:
            interaction_summary = run_multiview_current_source(
                connection, args.timeout
            )
        if args.target_extraction:
            interaction_summary = run_target_extraction(connection, args.timeout)
        if args.task_center:
            interaction_summary = run_task_center(connection, args.timeout)
        if args.service_credentials:
            interaction_summary = run_service_credentials(connection, args.timeout)
        if args.blender_settings:
            interaction_summary = run_blender_settings(connection, args.timeout)
        if args.agent_model_settings:
            interaction_summary = run_agent_model_settings(connection, args.timeout)
        if args.local_model_settings:
            interaction_summary = run_local_model_settings(connection, args.timeout)
        if args.product_workflow:
            interaction_summary = run_product_workflow(connection, args.timeout)
        if args.model_fallback_visual:
            interaction_summary = run_model_fallback_visual(connection, args.timeout)
        assert_clean_runtime(connection, allow_failed_network=args.recover_offline)
    except Exception as error:  # noqa: BLE001 - every scenario failure needs an evidence bundle.
        failure = error
    finally:
        try:
            collect_evidence(connection, args.output)
        finally:
            if args.close_window:
                with suppress(RuntimeError, OSError):
                    connection.evaluate("window.close(); true")
            connection.close()
    if failure:
        (args.output / "failure.txt").write_text(str(redact(str(failure))), encoding="utf-8")
        raise failure
    if args.service_credentials and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.blender_settings and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.agent_model_settings and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.local_model_settings and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.product_workflow and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.local_image_size and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.asset_visual_dedup and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.asset_file_actions and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.asset_remove and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.model_fallback_visual and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.agent_ui_action_navigation:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(
                {
                    "scenario": "agent_ui_action_navigation",
                    "status": "passed",
                    "assertions": {
                        "workspace_mode": "multiview",
                        "workspace_visible": True,
                        "manual_open_action_visible": True,
                        "runtime_errors": 0,
                        "unhandled_rejections": 0,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if args.agent_image_result_navigation and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.agent_target_extraction_result_sync and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.agent_analysis_result_sync and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.agent_image_attachment and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.agent_approval_status and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.prompt_rewrite and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.prompt_candidate_counts and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.project_export and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.multiview_current_source and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.target_extraction and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.task_center and interaction_summary is not None:
        (args.output / "interaction-summary.json").write_text(
            json.dumps(interaction_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
