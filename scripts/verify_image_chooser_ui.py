"""Verify both workspace image chooser actions in a controlled WebView2 host."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from webview2_cdp import (  # noqa: E402
    CdpConnection,
    cdp_network_records,
    collect_evidence,
    install_diagnostics,
)


def wait_for(
    connection: CdpConnection,
    expression: str,
    description: str,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if connection.evaluate(expression):
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}")


def open_workspace_tab(
    connection: CdpConnection,
    label: str,
    selector: str,
    timeout: float,
) -> None:
    connection.evaluate(
        """(() => {
          const label = %s;
          const button = [...document.querySelectorAll('.workflow-stage-tools button')]
            .find((item) => item.getAttribute('aria-label') === label);
          if (!button || button.disabled) throw new Error(`unavailable tab: ${label}`);
          button.click();
          return true;
        })()"""
        % json.dumps(label, ensure_ascii=False)
    )
    wait_for(
        connection,
        f"!!document.querySelector({json.dumps(selector)})",
        f"{label} workspace",
        timeout,
    )


def button_snapshot(connection: CdpConnection, selector: str) -> dict[str, object]:
    result = connection.evaluate(
        """(() => {
          const button = document.querySelector(%s);
          if (!button) throw new Error('chooser button missing');
          const rect = button.getBoundingClientRect();
          const style = getComputedStyle(button);
          return {
            text: button.textContent.trim(),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            background: style.backgroundColor,
            color: style.color,
            disabled: button.disabled,
          };
        })()"""
        % json.dumps(selector)
    )
    if not isinstance(result, dict):
        raise AssertionError("chooser button snapshot was not an object")
    if result.get("disabled"):
        raise AssertionError("chooser button is unexpectedly disabled")
    if int(result.get("height") or 0) < 36:
        raise AssertionError(f"chooser button is too small: {result}")
    if result.get("background") in {"rgba(0, 0, 0, 0)", "transparent"}:
        raise AssertionError(f"chooser button has no visible fill: {result}")
    return result


def click(connection: CdpConnection, selector: str) -> None:
    connection.evaluate(
        """(() => {
          const button = document.querySelector(%s);
          if (!button || button.disabled) throw new Error('chooser button unavailable');
          button.click();
          return true;
        })()"""
        % json.dumps(selector)
    )


def assert_runtime_clean(connection: CdpConnection) -> dict[str, int]:
    state = connection.evaluate(
        "globalThis.__aipicE2E || {errors:[],rejections:[],network:[]}"
    )
    if not isinstance(state, dict):
        state = {}
    errors = state.get("errors", []) or []
    rejections = state.get("rejections", []) or []
    network = [
        *(state.get("network", []) or []),
        *cdp_network_records(connection),
    ]
    failed = [
        item
        for item in network
        if isinstance(item, dict) and int(item.get("status") or 0) >= 400
    ]
    if errors or rejections or failed:
        raise AssertionError(
            "runtime errors detected: "
            + json.dumps(
                {
                    "errors": len(errors),
                    "rejections": len(rejections),
                    "failed_requests": len(failed),
                }
            )
        )
    return {
        "runtime_errors": 0,
        "unhandled_rejections": 0,
        "failed_requests": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument(
        "--open-native-dialog",
        action="store_true",
        help="Open the ordinary-host target image dialog and leave it open for Windows UI inspection.",
    )
    args = parser.parse_args()

    connection = CdpConnection.attach(args.debug_port)
    args.output.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {}
    try:
        install_diagnostics(connection)
        wait_for(
            connection,
            "document.readyState === 'complete'",
            "document",
            args.timeout,
        )

        if args.open_native_dialog:
            if connection.evaluate("!!document.querySelector('.project-launcher')"):
                connection.evaluate(
                    """(() => {
                      const button = document.querySelector('.recent-projects button');
                      if (!button || button.disabled) throw new Error('recent project unavailable');
                      button.click();
                      return true;
                    })()"""
                )
            wait_for(
                connection,
                "!!document.querySelector('.workbench')",
                "opened recent project",
                args.timeout,
            )
            open_workspace_tab(
                connection,
                "建模主体提取",
                ".target-extraction-workspace",
                args.timeout,
            )
            button_snapshot(connection, ".target-choose-source")
            click(connection, ".target-choose-source")
            wait_for(
                connection,
                "document.querySelector('.target-choose-source')?.disabled === true",
                "native chooser pending state",
                args.timeout,
            )
            print('{"native_dialog_request":"pending"}')
            return 0

        open_workspace_tab(
            connection,
            "建模主体提取",
            ".target-extraction-workspace",
            args.timeout,
        )
        summary["target_button"] = button_snapshot(
            connection, ".target-choose-source"
        )
        click(connection, ".target-choose-source")
        wait_for(
            connection,
            "document.querySelector('.target-message')?.textContent.includes('图片已导入')",
            "target extraction import confirmation",
            args.timeout,
        )
        wait_for(
            connection,
            "!!document.querySelector('.target-settings-panel img')",
            "target extraction source preview",
            args.timeout,
        )
        collect_evidence(connection, args.output / "target-extraction")

        open_workspace_tab(
            connection,
            "三视图制作",
            ".multiview-workspace",
            args.timeout,
        )
        summary["multiview_button"] = button_snapshot(
            connection, ".multiview-import-primary button"
        )
        if int(summary["multiview_button"].get("height") or 0) < 46:
            raise AssertionError("multiview chooser did not reach the 46px primary-action size")
        click(connection, ".multiview-import-primary button")
        wait_for(
            connection,
            "[...document.querySelectorAll('[role=status]')].some((item) => item.textContent.includes('已选择新的三视图来源图'))",
            "multiview import confirmation",
            args.timeout,
        )
        wait_for(
            connection,
            "!!document.querySelector('.multiview-source-preview[data-managed-asset-id]')",
            "multiview managed source preview",
            args.timeout,
        )
        collect_evidence(connection, args.output / "multiview")

        summary["runtime"] = assert_runtime_clean(connection)
        summary["result"] = "passed"
        (args.output / "interaction-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
