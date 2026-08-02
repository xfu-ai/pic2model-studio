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
    if title != "AIPicToModel":
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
            "document.querySelector('.agent-run-status')?.classList.contains('completed')"
        ),
        "the sent and restored Agent image attachments with a completed UI state",
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
            "completed_state_visible": True,
            "provider_request_intercepted": True,
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
    """Prove the project-current image identity and task-refresh visibility."""

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
    manual_refresh_visible = connection.evaluate(
        """[...document.querySelectorAll('.multiview-source-panel button')]
          .some((item) => item.textContent?.trim() === '立即刷新生成进度')"""
    )
    if manual_refresh_visible:
        raise AssertionError(
            "generation progress refresh was shown without a multiview generation job"
        )
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
    parser.add_argument("--mock-tripo-approval", action="store_true")
    parser.add_argument("--open-model-result", action="store_true")
    parser.add_argument("--agent-ui-action-navigation", action="store_true")
    parser.add_argument("--agent-image-attachment", action="store_true")
    parser.add_argument("--agent-image-path", type=Path, nargs="+")
    parser.add_argument("--prompt-rewrite", action="store_true")
    parser.add_argument("--prompt-candidate-counts", action="store_true")
    parser.add_argument("--project-export", action="store_true")
    parser.add_argument("--multiview-current-source", action="store_true")
    parser.add_argument("--target-extraction", action="store_true")
    parser.add_argument("--task-center", action="store_true")
    parser.add_argument("--service-credentials", action="store_true")
    parser.add_argument("--blender-settings", action="store_true")
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
        if args.agent_image_attachment:
            interaction_summary = run_agent_image_attachment(
                connection, args.timeout, args.agent_image_path or []
            )
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
    if args.product_workflow and interaction_summary is not None:
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
    if args.agent_image_attachment and interaction_summary is not None:
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
