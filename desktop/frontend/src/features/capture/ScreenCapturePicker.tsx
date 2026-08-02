import { Check, Selection, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { HostClient } from "../../shared/host/client";
import "./screen-capture-picker.css";

type Point = { x: number; y: number };
type NormalizedRect = Point & { width: number; height: number };

const clamp = (value: number) => Math.max(0, Math.min(1, value));
const defaultHost = new HostClient();

export function ScreenCapturePicker({ token, sourceSize, host = defaultHost }: {
  token: string;
  sourceSize?: { width: number; height: number };
  host?: Pick<HostClient, "screenCapturePreview" | "completeScreenCapture" | "cancelScreenCapture">;
}) {
  const [source, setSource] = useState("");
  const [rect, setRect] = useState<NormalizedRect | null>(null);
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const start = useRef<Point | null>(null);
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void host.screenCapturePreview(token).then((bytes) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(new Blob([bytes], { type: "image/bmp" }));
      setSource(objectUrl);
    }).catch(() => active && setError("无法加载屏幕快照，请取消后重试。"));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [host, token]);

  const point = (event: PointerEvent<HTMLDivElement>): Point => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) / Math.max(1, bounds.width)),
      y: clamp((event.clientY - bounds.top) / Math.max(1, bounds.height)),
    };
  };
  const begin = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || busy) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const origin = point(event);
    start.current = origin;
    setRect({ ...origin, width: 0, height: 0 });
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (!start.current || busy) return;
    const current = point(event);
    setRect({
      x: Math.min(start.current.x, current.x),
      y: Math.min(start.current.y, current.y),
      width: Math.abs(current.x - start.current.x),
      height: Math.abs(current.y - start.current.y),
    });
  };
  const end = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    start.current = null;
    setRect((current) => current && current.width >= 0.002 && current.height >= 0.002 ? current : null);
  };
  const valid = Boolean(rect && rect.width >= 0.002 && rect.height >= 0.002);
  const captureSize = sourceSize ?? naturalSize;
  const dimensions = useMemo(() => rect && captureSize.width && captureSize.height
    ? `${Math.round(rect.width * captureSize.width)} × ${Math.round(rect.height * captureSize.height)} px`
    : "拖动鼠标选择范围", [captureSize, rect]);

  const cancel = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    await host.cancelScreenCapture(token).catch(() => setError("无法关闭截图工具。"));
  }, [busy, host, token]);
  const confirm = useCallback(async () => {
    if (!rect || !valid || busy) return;
    setBusy(true);
    setError("");
    await host.completeScreenCapture(token, rect).catch(() => {
      setBusy(false);
      setError("无法保存所选区域，请重新框选。");
    });
  }, [busy, host, rect, token, valid]);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); void cancel(); }
      if (event.key === "Enter" && valid) { event.preventDefault(); void confirm(); }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [cancel, confirm, valid]);

  return <main className="screen-capture-picker">
    <div
      ref={stage}
      className="screen-capture-stage"
      aria-label="屏幕截图框选区域"
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
    >
      {source && <img src={source} alt="" draggable={false} onLoad={(event) => setNaturalSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />}
      {rect && <div className="screen-capture-selection" data-capture-selection style={{
        left: `${rect.x * 100}%`,
        top: `${rect.y * 100}%`,
        width: `${rect.width * 100}%`,
        height: `${rect.height * 100}%`,
      }} />}
      {!rect && <div className="screen-capture-dim" />}
    </div>
    <section className="screen-capture-help" aria-live="polite">
      <Selection size={22} />
      <div><strong>拖动选择截图区域</strong><span>松开鼠标后确认 · Esc 取消 · Enter 完成</span></div>
    </section>
    <section className="screen-capture-actions">
      <output aria-label="截图尺寸">{dimensions}</output>
      {error && <span role="alert">{error}</span>}
      <button type="button" disabled={busy} onClick={() => void cancel()}><X size={18} />取消</button>
      <button type="button" disabled={!valid || busy} onClick={() => setRect(null)}>重新框选</button>
      <button type="button" className="primary" disabled={!valid || busy} onClick={() => void confirm()}><Check size={18} />{busy ? "正在保存…" : "完成截图"}</button>
    </section>
  </main>;
}
