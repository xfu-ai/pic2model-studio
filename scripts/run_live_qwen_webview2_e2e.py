"""Real local-Qwen Agent verification through the retained Tauri WebView2 host."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from webview2_cdp import CdpConnection, collect_evidence, install_diagnostics, redact


def wait_for(
    connection: CdpConnection,
    expression: str,
    description: str,
    timeout: float,
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = connection.evaluate(expression)
        if last:
            return last
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {description}; last={redact(last)!r}")


def click_matching_button(connection: CdpConnection, text: str) -> None:
    script = """(() => {
          const expected = __EXPECTED__;
          const button = [...document.querySelectorAll('button')]
            .find((item) => item.textContent?.trim().includes(expected) && !item.disabled);
          if (!button) return false;
          button.click();
          return true;
        })()""".replace("__EXPECTED__", json.dumps(text))
    clicked = connection.evaluate(script)
    if not clicked:
        raise AssertionError(f"No enabled button contained {text!r}")


def ensure_project_open(connection: CdpConnection, timeout: float) -> None:
    if connection.evaluate("!!document.querySelector('textarea[aria-label=\"Message the Agent\"]')"):
        return
    click_matching_button(connection, "Pic2Model")
    wait_for(
        connection,
        "!!document.querySelector('textarea[aria-label=\"Message the Agent\"]')",
        "the Agent composer after opening the recent project",
        timeout,
    )


def create_conversation(connection: CdpConnection, timeout: float) -> None:
    wait_for(
        connection,
        "!!document.querySelector('button[aria-label=\"New conversation\"]:not(:disabled)')",
        "the new-conversation action",
        timeout,
    )
    connection.evaluate(
        "document.querySelector('button[aria-label=\"New conversation\"]')?.click(); true"
    )
    wait_for(
        connection,
        "document.querySelector('.agent-run-status')?.textContent?.includes('ready')",
        "the new Agent conversation",
        timeout,
    )


def composer_set_text(connection: CdpConnection, text: str) -> None:
    script = """(() => {
          const textarea = document.querySelector('textarea[aria-label="Message the Agent"]');
          if (!textarea) return false;
          const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
          setter?.call(textarea, __TEXT__);
          textarea.dispatchEvent(new Event('input', {bubbles: true}));
          return true;
        })()""".replace("__TEXT__", json.dumps(text))
    updated = connection.evaluate(script)
    if not updated:
        raise AssertionError("The Agent composer was unavailable")


def observation(connection: CdpConnection) -> dict[str, Any]:
    value = connection.evaluate(
        """(() => ({
          status: document.querySelector('.agent-run-status')?.textContent?.trim() || '',
          error: document.querySelector('.agent-error')?.textContent?.trim() || '',
          thinking: [...document.querySelectorAll('.agent-thinking')]
            .map((item) => item.textContent?.trim() || '').filter(Boolean),
          tools: [...document.querySelectorAll('.agent-tool-execution')]
            .map((item) => item.textContent?.trim() || '').filter(Boolean),
          assistants: [...document.querySelectorAll('.agent-message.assistant .agent-assistant-text')]
            .map((item) => item.textContent?.trim() || '').filter(Boolean),
          userImages: document.querySelectorAll('.agent-message.user img').length,
          readyImages: document.querySelectorAll('.agent-compose-attachment img').length,
        }))()"""
    )
    return value if isinstance(value, dict) else {}


def send_and_wait(
    connection: CdpConnection,
    text: str,
    marker: str,
    timeout: float,
    *,
    require_tool: bool,
    require_two_reasoning_messages: bool,
) -> dict[str, Any]:
    baseline = observation(connection)
    baseline_thinking = len(baseline.get("thinking") or [])
    baseline_tools = len(baseline.get("tools") or [])
    baseline_assistants = len(baseline.get("assistants") or [])
    composer_set_text(connection, text)
    wait_for(
        connection,
        "!!document.querySelector('button[aria-label=\"Send to Agent\"]:not(:disabled)')",
        "the enabled Agent send action",
        5.0,
    )
    started = time.monotonic()
    connection.evaluate(
        "document.querySelector('button[aria-label=\"Send to Agent\"]')?.click(); true"
    )
    timings: dict[str, float] = {}
    last: dict[str, Any] = {}
    deadline = started + timeout
    while time.monotonic() < deadline:
        last = observation(connection)
        elapsed = round(time.monotonic() - started, 3)
        thinking = (last.get("thinking") or [])[baseline_thinking:]
        tools = (last.get("tools") or [])[baseline_tools:]
        assistants = (last.get("assistants") or [])[baseline_assistants:]
        if thinking and "first_reasoning_seconds" not in timings:
            timings["first_reasoning_seconds"] = elapsed
        if tools and "tool_visible_seconds" not in timings:
            timings["tool_visible_seconds"] = elapsed
        if any("Completed" in str(item) for item in tools):
            timings.setdefault("tool_completed_seconds", elapsed)
        if len(thinking) >= 2 and "post_tool_reasoning_seconds" not in timings:
            timings["post_tool_reasoning_seconds"] = elapsed
        completed = "completed this response" in str(last.get("status", "")).lower()
        if completed and any(marker in str(item) for item in assistants):
            timings["completed_seconds"] = elapsed
            break
        if "stopped" in str(last.get("status", "")).lower() or last.get("error"):
            raise AssertionError(f"Agent failed before {marker!r}: {redact(last)!r}")
        time.sleep(0.2)
    else:
        raise AssertionError(f"Timed out waiting for {marker!r}; last={redact(last)!r}")
    turn = {
        **last,
        "thinking": (last.get("thinking") or [])[baseline_thinking:],
        "tools": (last.get("tools") or [])[baseline_tools:],
        "assistants": (last.get("assistants") or [])[baseline_assistants:],
    }
    if require_tool:
        if not any("inspect_workspace" in str(item) for item in turn["tools"]):
            raise AssertionError("The required inspect_workspace tool call was not visible")
        if not any("Completed" in str(item) for item in turn["tools"]):
            raise AssertionError("The required tool call did not complete")
    if require_two_reasoning_messages and len(turn["thinking"]) < 2:
        raise AssertionError("Reasoning did not resume after the tool result")
    return {"marker": marker, "timings": timings, "observation": redact(turn)}


def attach_image(connection: CdpConnection, image_path: Path, timeout: float) -> None:
    if not image_path.is_file():
        raise ValueError("The live-Qwen image fixture does not exist")
    connection.call("DOM.enable")
    root = connection.call("DOM.getDocument")["root"]["nodeId"]
    input_node = connection.call(
        "DOM.querySelector",
        nodeId=root,
        selector='input[aria-label="Choose images to attach"]',
    )["nodeId"]
    connection.call("DOM.setFileInputFiles", files=[str(image_path.resolve())], nodeId=input_node)
    connection.evaluate(
        """document.querySelector('input[aria-label="Choose images to attach"]')
          ?.dispatchEvent(new Event('change', {bubbles: true})); true"""
    )
    wait_for(
        connection,
        "document.querySelectorAll('.agent-compose-attachment img').length === 1",
        "the managed image attachment preview",
        timeout,
    )


def assert_managed_attachment_request(connection: CdpConnection) -> dict[str, Any]:
    records = connection.evaluate("globalThis.__aipicE2E?.network || []") or []
    imports = [item for item in records if "/assets/import" in str(item.get("url", ""))]
    messages = [
        item
        for item in records
        if item.get("method") == "POST"
        and "/v1/agent/conversations/" in str(item.get("url", ""))
        and str(item.get("url", "")).endswith("/messages")
    ]
    image_messages = [item for item in messages if '"asset_refs":[' in str(item.get("request", ""))]
    if not imports or not image_messages:
        raise AssertionError("The real attachment import/message request was not observed")
    request = str(image_messages[-1].get("request", ""))
    lowered = request.lower()
    if "base64" in lowered or "data:image" in lowered or ":\\" in request:
        raise AssertionError("The attachment request leaked bytes or a native path")
    return {
        "import_status": imports[-1].get("status"),
        "message_status": image_messages[-1].get("status"),
        "uses_managed_asset_refs": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    connection = CdpConnection.attach(args.debug_port)
    results: dict[str, Any] = {}
    try:
        install_diagnostics(connection)
        connection.evaluate("globalThis.__aipicE2E.network = []; true")
        ensure_project_open(connection, args.timeout)
        create_conversation(connection, args.timeout)
        results["text_tool_turn"] = send_and_wait(
            connection,
            (
                "Perform a real read-only workspace check. First call inspect_workspace with "
                "view set to summary. After the tool result, continue reasoning and give a "
                "concise final answer ending exactly with MANAGED OLLAMA OK. Do not call any "
                "generation, paid, approval, or write tool."
            ),
            "MANAGED OLLAMA OK",
            args.timeout,
            require_tool=True,
            require_two_reasoning_messages=True,
        )
        before_images = int(observation(connection).get("userImages") or 0)
        attach_image(connection, args.image, args.timeout)
        results["image_tool_turn"] = send_and_wait(
            connection,
            (
                "Understand the attached image pixels directly. Describe at least two specific, "
                "verifiable visual details, then call inspect_workspace with view set to summary. "
                "After the tool result, continue reasoning and end the final answer exactly with "
                "IMAGE LIFECYCLE OK. Do not call generation, paid, approval, or write tools."
            ),
            "IMAGE LIFECYCLE OK",
            args.timeout,
            require_tool=True,
            require_two_reasoning_messages=True,
        )
        wait_for(
            connection,
            f"document.querySelectorAll('.agent-message.user img').length > {before_images}",
            "the persisted user image attachment",
            args.timeout,
        )
        results["second_visual_turn"] = send_and_wait(
            connection,
            (
                "Using only the image from the previous turn, mention one specific visible detail "
                "again and end exactly with IMAGE SECOND TURN OK. Do not call any tool."
            ),
            "IMAGE SECOND TURN OK",
            args.timeout,
            require_tool=False,
            require_two_reasoning_messages=False,
        )
        results["attachment_transport"] = assert_managed_attachment_request(connection)
        results["passed"] = True
    except Exception as error:
        results["passed"] = False
        results["error"] = str(redact(str(error)))
        raise
    finally:
        collect_evidence(connection, args.output)
        (args.output / "result.json").write_text(
            json.dumps(redact(results), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        connection.close()
    print(json.dumps(redact(results), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
