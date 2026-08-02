"""Capture and assert the Amber Workshop layout in a controlled WebView2 host.

The host must already be running through run_controlled_webview2.ps1. This
script only clicks local navigation controls and never invokes a provider,
native picker, or paid operation.
"""

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
    redact,
)


WORKSPACE_PAGES = [
    ("01-current-image", "当前图片", ".image-workspace"),
    ("02-content-style-analysis", "内容与风格分析", ".compare-workspace"),
    ("03-creative-image-generation", "创意图生成", ".prompt-image-workspace"),
    ("04-model-subject-extraction", "建模主体提取", ".target-extraction-workspace"),
    ("05-multiview-production", "三视图制作", ".multiview-workspace"),
    ("06-model-processing", "3D 模型处理", ".model-workspace"),
]

ROUTE_PAGES = [
    ("07-assets", "资产", ".asset-browser"),
    ("08-jobs", "任务", ".jobs-panel"),
    ("09-export", "导出", ".project-package-actions"),
]


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


def click_workspace_tab(
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
          if (!button || button.disabled) {
            throw new Error(`workspace tab is unavailable: ${label}`);
          }
          button.click();
          return true;
        })()"""
        % json.dumps(label, ensure_ascii=False)
    )
    wait_for(
        connection,
        "!!document.querySelector(%s)"
        % json.dumps(selector),
        f"{label} workspace",
        timeout,
    )
    wait_for(
        connection,
        """(() => {
          const label = %s;
          return [...document.querySelectorAll('.workflow-stage-tools button')]
            .some((item) =>
              item.getAttribute('aria-label') === label &&
              item.getAttribute('aria-pressed') === 'true'
            );
        })()"""
        % json.dumps(label, ensure_ascii=False),
        f"{label} selected state",
        timeout,
    )


def click_primary_route(
    connection: CdpConnection,
    label: str,
    selector: str,
    timeout: float,
) -> None:
    connection.evaluate(
        """(() => {
          const label = %s;
          const button = [...document.querySelectorAll('.primary-navigation button')]
            .find((item) => item.title === label);
          if (!button || button.disabled) {
            throw new Error(`primary route is unavailable: ${label}`);
          }
          button.click();
          return true;
        })()"""
        % json.dumps(label, ensure_ascii=False)
    )
    wait_for(
        connection,
        "!!document.querySelector(%s)"
        % json.dumps(selector),
        f"{label} route",
        timeout,
    )


def layout_snapshot(connection: CdpConnection, selector: str) -> dict[str, object]:
    result = connection.evaluate(
        """(() => {
          const page = document.querySelector(%s);
          const agent = document.querySelector('.agent-panel');
          const tabs = [...document.querySelectorAll('.workflow-stage-tools button')];
          if (!page) throw new Error('page root missing');
          if (!agent) throw new Error('Agent panel missing');
          const pageRect = page.getBoundingClientRect();
          const agentRect = agent.getBoundingClientRect();
          const visible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 &&
              style.display !== 'none' && style.visibility !== 'hidden';
          };
          return {
            viewport: {width: innerWidth, height: innerHeight},
            page: {
              width: Math.round(pageRect.width),
              height: Math.round(pageRect.height),
              visible: visible(page),
            },
            agent: {
              width: Math.round(agentRect.width),
              height: Math.round(agentRect.height),
              visible: visible(agent),
            },
            workspaceTabs: tabs.length,
            selectedTabs: tabs.filter((item) =>
              item.getAttribute('aria-pressed') === 'true'
            ).map((item) => item.getAttribute('aria-label')),
            documentOverflowX:
              document.documentElement.scrollWidth >
              document.documentElement.clientWidth,
          };
        })()"""
        % json.dumps(selector)
    )
    if not isinstance(result, dict):
        raise AssertionError("layout snapshot was not an object")
    agent = result.get("agent")
    page = result.get("page")
    if not isinstance(agent, dict) or not agent.get("visible"):
        raise AssertionError("Agent panel is not visible")
    if not isinstance(page, dict) or not page.get("visible"):
        raise AssertionError(f"page is not visible: {selector}")
    width = int(agent.get("width") or 0)
    if width < 360 or width > 520:
        raise AssertionError(f"Agent width outside desktop contract: {width}")
    if result.get("documentOverflowX"):
        raise AssertionError(f"unexpected document horizontal overflow: {selector}")
    return result


def assert_runtime_clean(connection: CdpConnection) -> dict[str, object]:
    connection.evaluate("true")
    state = connection.evaluate(
        "globalThis.__aipicE2E || {errors:[],rejections:[],network:[]}"
    )
    if not isinstance(state, dict):
        state = {}
    state["network"] = [
        *(state.get("network", []) or []),
        *cdp_network_records(connection),
    ]
    errors = state.get("errors", []) or []
    rejections = state.get("rejections", []) or []
    failed = [
        item
        for item in state.get("network", []) or []
        if isinstance(item, dict) and (item.get("status") or 0) >= 400
    ]
    if errors or rejections or failed:
        raise AssertionError(
            f"WebView runtime not clean: {redact({'errors': errors, 'rejections': rejections, 'network': failed})}"
        )
    return {
        "runtime_errors": len(errors),
        "unhandled_rejections": len(rejections),
        "failed_requests": len(failed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()

    connection = CdpConnection.attach(args.debug_port)
    summaries: dict[str, object] = {}
    try:
        install_diagnostics(connection)
        wait_for(
            connection,
            "document.readyState === 'complete' && !!document.querySelector('.workbench')",
            "AIPicToModel workbench",
            args.timeout,
        )

        connection.evaluate(
            """(() => {
              const button = [...document.querySelectorAll('.primary-navigation button')]
                .find((item) => item.title === '工作区');
              if (!button || button.disabled) {
                throw new Error('workspace route is unavailable');
              }
              button.click();
              return true;
            })()"""
        )
        wait_for(
            connection,
            "document.querySelectorAll('.workflow-stage-tools button').length === 6",
            "product workbench tools",
            args.timeout,
        )

        for slug, label, selector in WORKSPACE_PAGES:
            click_workspace_tab(connection, label, selector, args.timeout)
            time.sleep(0.35)
            summaries[slug] = layout_snapshot(connection, selector)
            collect_evidence(connection, args.output / slug)

        for slug, label, selector in ROUTE_PAGES:
            click_primary_route(connection, label, selector, args.timeout)
            time.sleep(0.35)
            summaries[slug] = layout_snapshot(connection, selector)
            collect_evidence(connection, args.output / slug)

        connection.evaluate(
            """(() => {
              const button = [...document.querySelectorAll('.primary-navigation button')]
                .find((item) => item.title === '工作区');
              if (!button || button.disabled) {
                throw new Error('workspace route is unavailable');
              }
              button.click();
              return true;
            })()"""
        )
        wait_for(
            connection,
            "document.querySelectorAll('.workflow-stage-tools button').length === 6",
            "product workbench tools",
            args.timeout,
        )
        click_workspace_tab(
            connection,
            "当前图片",
            ".image-workspace",
            args.timeout,
        )

        summaries["runtime"] = assert_runtime_clean(connection)
        summaries["status"] = "passed"
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "interaction-summary.json").write_text(
            json.dumps(redact(summaries), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
