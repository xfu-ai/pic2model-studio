"""Run controlled WebView2 checks through the local Edge WebDriver endpoint.

This is the pipe-based fallback for WebView2 runtimes that do not expose a
configured DevTools port. It uses JavaScript DOM operations only; it never
opens a native picker, drives a mouse, or contacts a real provider.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from webview2_cdp import redact


class WebDriver:
    def __init__(
        self,
        endpoint: str,
        binary: Path,
        *,
        tauri_driver: bool,
        session_timeout: float,
        webview_user_data_folder: Path | None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        if tauri_driver:
            tauri_options: dict[str, Any] = {"application": str(binary)}
            if webview_user_data_folder:
                tauri_options["webviewOptions"] = {"userDataFolder": str(webview_user_data_folder)}
            always_match = {"tauri:options": tauri_options}
        else:
            always_match = {
                "browserName": "webview2",
                "ms:edgeChromium": True,
                "ms:edgeOptions": {"binary": str(binary)},
            }
        response = requests.post(
            f"{self.endpoint}/session",
            json={"capabilities": {"alwaysMatch": always_match}},
            timeout=session_timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"WebDriver session creation returned {response.status_code}: {redact(response.text[:4000])}"
            )
        self.session_id = response.json()["value"]["sessionId"]

    def close(self) -> None:
        requests.delete(f"{self.endpoint}/session/{self.session_id}", timeout=20)

    def evaluate(self, script: str) -> Any:
        response = requests.post(
            f"{self.endpoint}/session/{self.session_id}/execute/sync",
            json={"script": script, "args": []},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("value", {}).get("error"):
            raise RuntimeError(payload["value"].get("message", "WebDriver evaluation failed"))
        return payload["value"]

    def screenshot(self) -> bytes:
        response = requests.get(f"{self.endpoint}/session/{self.session_id}/screenshot", timeout=30)
        response.raise_for_status()
        return base64.b64decode(response.json()["value"])


def wait_for(driver: WebDriver, expression: str, description: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if driver.evaluate(f"return !!({expression});"):
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}")


def install_diagnostics(driver: WebDriver) -> None:
    driver.evaluate(
        """if (!globalThis.__aipicE2E) {
          const records = {errors: [], rejections: [], network: []};
          globalThis.__aipicE2E = records;
          const safe = (value) => String(value ?? '').slice(0, 8000);
          addEventListener('error', (event) => records.errors.push({message: safe(event.message), source: safe(event.filename), line: event.lineno}));
          addEventListener('unhandledrejection', (event) => records.rejections.push(safe(event.reason?.stack || event.reason)));
          const original = globalThis.fetch.bind(globalThis);
          globalThis.fetch = async (input, init = {}) => {
            const request = new Request(input, init);
            const record = {method: request.method, url: request.url, request: safe(init.body), status: null, response: ''};
            try {
              const response = await original(input, init);
              record.status = response.status;
              record.response = safe(await response.clone().text());
              return response;
            } catch (error) {
              record.response = safe(error?.stack || error);
              throw error;
            } finally { records.network.push(record); }
          };
        }
        return true;"""
    )


def assert_clean_runtime(driver: WebDriver, *, allow_failed_network: bool) -> None:
    state = driver.evaluate("return globalThis.__aipicE2E || {};") or {}
    failures = {key: state.get(key, []) for key in ("errors", "rejections") if state.get(key)}
    failed_network = [item for item in state.get("network", []) if (item.get("status") or 0) >= 400]
    if failed_network and not allow_failed_network:
        failures["network"] = failed_network
    if failures:
        raise AssertionError(f"WebView runtime is not clean: {redact(failures)}")


def run_startup(driver: WebDriver, timeout: float) -> None:
    wait_for(driver, "document.readyState === 'complete' && !!document.querySelector('main, .workbench-layout')", "application shell", timeout)
    if driver.evaluate("return document.title;") != "图模工坊":
        raise AssertionError("unexpected document title")
    if driver.evaluate("return document.body.innerText.includes('本地服务暂时不可用');"):
        raise AssertionError("controlled sidecar did not reach a healthy state")


def run_offline_recovery(driver: WebDriver, timeout: float) -> None:
    wait_for(driver, "!!document.querySelector('.app-shell .primary')", "offline reconnect action", timeout)
    driver.evaluate("document.querySelector('.app-shell .primary').click(); return true;")
    wait_for(driver, "!!document.querySelector('#project-launcher-title')", "recovered project launcher", timeout)


def run_create_project(driver: WebDriver, timeout: float, name: str) -> None:
    wait_for(driver, "!!document.querySelector('#project-launcher-title')", "project launcher", timeout)
    driver.evaluate(
        """const input = document.querySelector('input');
        if (!input) throw new Error('project name input is missing');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, """
        + json.dumps(name)
        + """);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('Choose folder and create'));
        if (!button || button.disabled) throw new Error('create-project control is unavailable');
        button.click(); return true;"""
    )
    wait_for(driver, "document.body.innerText.includes('建立你的资产工作台')", "new empty asset workbench", timeout)


def run_import_image(driver: WebDriver, timeout: float) -> None:
    driver.evaluate(
        """const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('Choose image'));
        if (!button || button.disabled) throw new Error('image import control is unavailable');
        button.click(); return true;"""
    )
    wait_for(driver, "!!document.querySelector('.image-workspace')", "imported material workbench", timeout)
    network = driver.evaluate("return globalThis.__aipicE2E?.network || [];") or []
    imports = [item for item in network if "/assets/import" in str(item.get("url", ""))]
    if not imports:
        raise AssertionError("image import did not issue an API request")
    if any(":\\\\" in str(item.get("request", "")) for item in imports):
        raise AssertionError("image import sent a local path instead of a capability")


def click_button(driver: WebDriver, text: str) -> None:
    driver.evaluate(
        """const expected = """
        + json.dumps(text)
        + """;
        const button = [...document.querySelectorAll('button')].find(
          (item) => item.textContent?.trim().includes(expected)
        );
        if (!button || button.disabled) throw new Error(`button is unavailable: ${expected}`);
        button.click();
        return true;"""
    )


def set_input(driver: WebDriver, label: str, value: int) -> None:
    driver.evaluate(
        """const input = document.querySelector(`input[aria-label=${JSON.stringify("""
        + json.dumps(label)
        + """)}]`);
        if (!input) throw new Error('selection input is missing');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, """
        + json.dumps(str(value))
        + """);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return true;"""
    )


def run_target_extraction(driver: WebDriver, timeout: float) -> dict[str, object]:
    click_button(driver, "建模主体提取")
    wait_for(driver, "!!document.querySelector('#target-extraction-title')", "target extraction workspace", timeout)
    click_button(driver, "加载当前图片")
    wait_for(driver, "!!document.querySelector('.target-canvas img')", "target extraction source image", timeout)
    direct_class = driver.evaluate("return document.querySelector('.target-selection-rect')?.className || '';")
    if "direct" not in direct_class:
        raise AssertionError("direct selection did not use the green direct-selection state")
    for label, value in (("x", 1), ("y", 1), ("width", 12), ("height", 12)):
        set_input(driver, label, value)
    click_button(driver, "生成独立目标图")
    wait_for(driver, "document.body.innerText.includes('确认外部图像生成')", "direct extraction approval", timeout)
    click_button(driver, "批准并提交")
    wait_for(driver, "!!document.querySelector('.target-result-card img')", "direct extraction result", timeout)

    click_button(driver, "先生成 AI 拆解图")
    wait_for(driver, "document.querySelector('[role=radio][aria-checked=true]')?.textContent?.includes('AI 拆解图')", "breakdown method selection", timeout)
    click_button(driver, "生成部件拆解图")
    wait_for(driver, "document.body.innerText.includes('确认外部图像生成')", "breakdown approval", timeout)
    click_button(driver, "批准并提交")
    wait_for(driver, "document.body.innerText.includes('裁出选中部件')", "AI breakdown board", timeout)
    breakdown_class = driver.evaluate("return document.querySelector('.target-selection-rect')?.className || '';")
    if "breakdown" not in breakdown_class:
        raise AssertionError("breakdown selection did not use the red breakdown-selection state")

    driver.evaluate("document.querySelector('button[aria-label=任务]')?.click(); return true;")
    wait_for(driver, "!!document.querySelector('.job-card .job-actions .primary')", "completed extraction task", timeout)
    driver.evaluate("document.querySelector('.job-card .job-actions .primary').click(); return true;")
    wait_for(driver, "!!document.querySelector('#target-extraction-title')", "task result target extraction workspace", timeout)
    if not driver.evaluate("return !![...document.querySelectorAll('[role=radio][aria-checked=true]')].find((item) => item.textContent?.includes('AI 拆解图'));"):
        raise AssertionError("task center did not restore the breakdown extraction method")

    for label, value in (("部件 x", 1), ("部件 y", 1), ("部件 width", 10), ("部件 height", 10)):
        set_input(driver, label, value)
    click_button(driver, "裁出选中部件")
    wait_for(driver, "document.querySelectorAll('.target-result-selector button').length >= 2", "multiple extracted results", timeout)
    result_count = driver.evaluate("return document.querySelectorAll('.target-result-selector button').length;")

    click_button(driver, "在资产中查看")
    wait_for(driver, "!!document.querySelector('.asset-card.focused')", "focused extracted asset", timeout)
    click_button(driver, "工作区")
    wait_for(driver, "!!document.querySelector('#target-extraction-title')", "target extraction after asset navigation", timeout)
    click_button(driver, "进入三视图制作")
    wait_for(driver, "document.body.innerText.includes('三视图来源')", "multiview handoff", timeout)
    if not driver.evaluate("return !!document.querySelector('.multiview-source-preview img, img.multiview-source-preview');"):
        raise AssertionError("multiview did not receive the selected extraction result")
    return {
        "direct_selection_class": direct_class,
        "breakdown_selection_class": breakdown_class,
        "result_count": result_count,
        "task_center_restored_breakdown": True,
        "asset_focus": True,
        "multiview_handoff": True,
    }


def collect_evidence(driver: WebDriver, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    html = driver.evaluate("return document.documentElement.outerHTML;")
    state = driver.evaluate("return globalThis.__aipicE2E || {errors:[],rejections:[],network:[]};")
    workspace = driver.evaluate("return JSON.stringify({title: document.title, body: document.body.innerText, focus: document.activeElement?.outerHTML?.slice(0, 300)});")
    (output / "dom.html").write_text(str(redact(html)), encoding="utf-8")
    (output / "runtime.json").write_text(json.dumps(redact(state), ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "workspace.json").write_text(str(redact(workspace)), encoding="utf-8")
    (output / "webview.png").write_bytes(driver.screenshot())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-url", required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--session-timeout", type=float, default=120)
    parser.add_argument("--webview-user-data-folder", type=Path)
    parser.add_argument("--create-project", action="store_true")
    parser.add_argument("--import-image", action="store_true")
    parser.add_argument("--target-extraction", action="store_true")
    parser.add_argument("--recover-offline", action="store_true")
    parser.add_argument("--tauri-driver", action="store_true", help="send the Tauri WebDriver capability envelope")
    parser.add_argument("--project-name", default="Controlled WebDriver E2E")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        driver = WebDriver(
            args.driver_url,
            args.binary,
            tauri_driver=args.tauri_driver,
            session_timeout=args.session_timeout,
            webview_user_data_folder=args.webview_user_data_folder,
        )
    except Exception as error:
        (args.output / "failure.txt").write_text(str(redact(str(error))), encoding="utf-8")
        raise
    failure: Exception | None = None
    try:
        wait_for(driver, "document.readyState !== 'loading'", "WebView document", args.timeout)
        install_diagnostics(driver)
        if args.recover_offline:
            run_offline_recovery(driver, args.timeout)
        else:
            run_startup(driver, args.timeout)
        if args.create_project:
            run_create_project(driver, args.timeout, args.project_name)
        if args.import_image:
            if not args.create_project:
                raise ValueError("--import-image requires --create-project")
            run_import_image(driver, args.timeout)
        interaction_summary: dict[str, object] | None = None
        if args.target_extraction:
            if not args.import_image:
                raise ValueError("--target-extraction requires --import-image")
            interaction_summary = run_target_extraction(driver, args.timeout)
            (args.output / "interaction-summary.json").write_text(
                json.dumps(interaction_summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        assert_clean_runtime(driver, allow_failed_network=args.recover_offline)
    except Exception as error:  # noqa: BLE001 - every failure must retain safe evidence.
        failure = error
    finally:
        try:
            collect_evidence(driver, args.output)
        finally:
            driver.close()
    if failure:
        (args.output / "failure.txt").write_text(str(redact(str(failure))), encoding="utf-8")
        raise failure
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
