import { ArrowCounterClockwise, ArrowsClockwise, Camera, Cube, DownloadSimple, GlobeSimple, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { ApiClient, AssetDto, JobDto, WorkflowContexts } from "../../shared/api/client";
import { ImportGlbAction } from "../assets/ImportGlbAction";
import { HostClient } from "../../shared/host/client";
import { localDefaultPreviewModelUrl } from "./defaultPreviewModel";
import "./model-viewport.css";

type ModelViewerElement = HTMLElement & { loaded?: boolean; src?: string; toBlob?: (options?: object) => Promise<Blob> };

function requestId() { return crypto.randomUUID(); }

function generationFailureMessage(job: JobDto) {
  if (job.status === "cancelled") return "3D 生成已取消。";
  if (job.error?.code === "MULTIVIEW_MANUAL_CONFIRMATION_REQUIRED") {
    return "3D 生成未开始：最终裁切的三视图尚未完成质量确认。请返回三视图制作页重新确认后提交。";
  }
  if (job.status === "interrupted") {
    return `3D 生成已中断：${job.error?.user_message ?? "请在任务中心查看详情后重试。"}`;
  }
  return `3D 生成失败：${job.error?.user_message ?? "请在任务中心查看详情后重试。"}`;
}

export function ModelViewport({ projectId, api, workflowContext, onWorkflowContextChange, onQueued, onImported, host = new HostClient() }: { projectId: string; api: ApiClient; workflowContext?: WorkflowContexts["model3d"]; onWorkflowContextChange?(value: WorkflowContexts["model3d"]): void; onQueued?(): void; onImported?(): void; host?: Pick<HostClient, "chooseExportDirectory"> }) {
  const [model, setModel] = useState<AssetDto | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [fallback, setFallback] = useState(false);
  const [reload, setReload] = useState(0);
  const [generationJob, setGenerationJob] = useState<JobDto | null>(null);
  const [generationResultAssetId, setGenerationResultAssetId] = useState<string | null>(null);
  const [conversionJob, setConversionJob] = useState<JobDto | null>(null);
  const [conversionAsset, setConversionAsset] = useState<AssetDto | null>(null);
  const [conversionPending, setConversionPending] = useState(false);
  const [savingFbx, setSavingFbx] = useState(false);
  const [targetTriangles, setTargetTriangles] = useState(workflowContext?.target_triangles ?? 50000);
  const [message, setMessage] = useState("正在加载受管 GLB…");
  const [restoreAssetId, setRestoreAssetId] = useState<string | null | undefined>(undefined);
  const [switchingAsset, setSwitchingAsset] = useState(false);
  const viewer = useRef<ModelViewerElement>(null);
  const loadedGenerationJob = useRef<string | null>(null);
  const generationPollVersion = useRef(0);
  const trackedConversionJobId = useRef<string | null>(null);
  const conversionRequestInFlight = useRef(false);
  const suppressHistoricalConversions = useRef(false);
  const resolvedConversionAssetId = useRef<string | null>(null);
  const explicitSelection = useRef(false);
  const hydrated = useRef(false);
  const loadVersion = useRef(0);
  useEffect(() => {
    if (!hydrated.current) return;
    const next = {
      asset_id: model?.id ?? null,
      target_triangles: targetTriangles,
      generation_job_id: workflowContext?.generation_job_id ?? null,
    };
    if (workflowContext?.asset_id === next.asset_id && workflowContext.target_triangles === next.target_triangles) return;
    onWorkflowContextChange?.(next);
  }, [model?.id, onWorkflowContextChange, targetTriangles, workflowContext?.asset_id, workflowContext?.generation_job_id, workflowContext?.target_triangles]);

  useEffect(() => {
    const version = ++loadVersion.current;
    hydrated.current = false;
    let active = true;
    let objectUrl: string | null = null;
    void api.assets(projectId).then(async (assets) => {
      const generated = generationResultAssetId
        ? assets.find(
            (asset) =>
              asset.id === generationResultAssetId
              && asset.asset_type === "glb"
              && asset.trashed_at == null,
          ) ?? null
        : null;
      const restored = workflowContext?.asset_id ? assets.find((asset) => asset.id === workflowContext.asset_id && asset.asset_type === "glb") ?? null : null;
      const selected = generated ?? restored ?? (explicitSelection.current
        ? null
        : assets.find((asset) => asset.is_current && asset.asset_type === "glb")
          ?? assets.find((asset) => asset.asset_type === "glb") ?? null);
      if (!selected) {
        objectUrl = localDefaultPreviewModelUrl();
        if (active && version === loadVersion.current) {
          setPreviewState("loading");
          setModel(null); setFallback(true); setUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return objectUrl; }); setMessage("正在显示本项目的内置资产信标，仅用于检查预览器；它不会上传、导出或写入项目。");
          hydrated.current = true;
        } else {
          URL.revokeObjectURL(objectUrl);
          objectUrl = null;
        }
        return;
      }
      const blob = await api.assetContent(projectId, selected.id);
      if (!active || version !== loadVersion.current) return;
      objectUrl = URL.createObjectURL(blob);
      setPreviewState("loading");
      setModel(selected); setFallback(false); setUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return objectUrl; }); setMessage("");
      hydrated.current = true;
    }).catch(() => { if (active && version === loadVersion.current) setMessage("无法读取当前受管 GLB。"); });
    return () => {
      active = false;
      if (version === loadVersion.current) loadVersion.current += 1;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, generationResultAssetId, projectId, reload, workflowContext?.asset_id]);

  useEffect(() => {
    let active = true;
    const refreshGeneration = async () => {
      const version = ++generationPollVersion.current;
      try {
        const { items } = await api.jobs(projectId, true);
        const trackedGenerationJobId = workflowContext?.generation_job_id ?? null;
        const generationJobs = items.filter((job) => job.job_type === "model3d.generate");
        const conversionJobs = items.filter((job) => job.job_type === "model3d.convert");
        const tracked = trackedGenerationJobId
          ? generationJobs.find((job) => job.id === trackedGenerationJobId) ?? null
          : null;
        if (!active || version !== generationPollVersion.current) return;
        setGenerationJob(tracked);
        const trackedConversion = trackedConversionJobId.current
          ? conversionJobs.find((job) => job.id === trackedConversionJobId.current) ?? null
          : null;
        const historicalConversion = !conversionRequestInFlight.current
          && !suppressHistoricalConversions.current
          && model?.id
          ? conversionJobs.find((job) => job.input_asset_ids?.includes(model.id)) ?? null
          : null;
        const conversion = trackedConversion ?? historicalConversion;
        if (conversion) trackedConversionJobId.current = conversion.id;
        setConversionJob(conversion);
        if (conversion) setConversionPending(false);
        const conversionAssetId = conversion?.status === "succeeded"
          ? conversion.output_asset_ids[0] ?? null
          : null;
        if (
          conversionAssetId
          && resolvedConversionAssetId.current !== conversionAssetId
        ) {
          void api.assets(projectId).then((assets) => {
            if (!active) return;
            const asset = assets.find(
              (candidate) =>
                candidate.id === conversionAssetId
                && candidate.asset_type === "fbx"
                && candidate.trashed_at == null,
            ) ?? null;
            if (!asset) return;
            resolvedConversionAssetId.current = asset.id;
            setConversionAsset(asset);
          }).catch(() => {
            // Retry on the next job poll; conversion success remains visible.
          });
        }
        if (
          tracked?.status === "succeeded"
          && loadedGenerationJob.current !== tracked.id
        ) {
          const outputAssetId = tracked.output_asset_ids[0] ?? null;
          if (outputAssetId) {
            loadedGenerationJob.current = tracked.id;
            hydrated.current = false;
            setGenerationResultAssetId(outputAssetId);
            onWorkflowContextChange?.({
              asset_id: outputAssetId,
              target_triangles: targetTriangles,
              generation_job_id: null,
            });
          }
        }
      } catch {
        // The model preview remains usable when progress polling is unavailable.
      }
    };
    void refreshGeneration();
    const timer = window.setInterval(() => void refreshGeneration(), 2000);
    return () => {
      active = false;
      generationPollVersion.current += 1;
      window.clearInterval(timer);
    };
  }, [api, model?.id, onWorkflowContextChange, projectId, targetTriangles, workflowContext?.generation_job_id]);

  useEffect(() => {
    trackedConversionJobId.current = null;
    conversionRequestInFlight.current = false;
    suppressHistoricalConversions.current = false;
    resolvedConversionAssetId.current = null;
    setConversionJob(null);
    setConversionAsset(null);
    setConversionPending(false);
  }, [model?.id]);

  useEffect(() => {
    const element = viewer.current;
    if (!element || !url) return;
    const handleLoad = () => setPreviewState("ready");
    const handleError = () => setPreviewState("error");
    element.addEventListener("load", handleLoad);
    element.addEventListener("error", handleError);
    if (element.loaded) setPreviewState("ready");
    return () => {
      element.removeEventListener("load", handleLoad);
      element.removeEventListener("error", handleError);
    };
  }, [url]);

  const screenshot = async () => {
    if (!model || !viewer.current?.toBlob) { setMessage("预览器尚未准备好截图。"); return; }
    try {
      const blob = await viewer.current.toBlob({ idealAspect: true });
      const bytes = new Uint8Array(await blob.arrayBuffer());
      let binary = "";
      bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
      await api.registerPreview(projectId, model.id, btoa(binary), requestId());
      setMessage("截图已作为受管预览资产保存。");
    } catch { setMessage("截图保存失败，请在模型完全加载后重试。"); }
  };
  const exportFbx = async () => {
    if (!model) return;
    trackedConversionJobId.current = null;
    conversionRequestInFlight.current = true;
    suppressHistoricalConversions.current = true;
    resolvedConversionAssetId.current = null;
    setConversionPending(true);
    setConversionJob(null);
    setConversionAsset(null);
    setMessage("正在创建 FBX 转换任务…");
    try {
      const result = await api.invokeTool(projectId, "model3d.convert", { asset_id: model.id, target_format: "fbx" }, requestId());
      if (result.status !== "queued" || !result.job?.job_id) {
        setConversionPending(false);
        setMessage(result.error?.user_message ?? result.summary ?? "FBX 转换任务未能创建。");
        return;
      }
      trackedConversionJobId.current = result.job.job_id;
      setMessage("");
    } catch {
      setConversionPending(false);
      setMessage("无法创建 FBX 转换任务。请确认已安装 Blender，或在设置中指定其可执行文件。");
    } finally {
      conversionRequestInFlight.current = false;
    }
  };
  const saveFbx = async () => {
    if (!conversionAsset) return;
    setSavingFbx(true);
    setMessage("");
    try {
      const capability = await host.chooseExportDirectory(projectId);
      if (!capability) {
        setMessage("已取消选择保存文件夹；FBX 仍保留在项目资产中。");
        return;
      }
      const result = await api.exportAsset(projectId, conversionAsset.id, capability, requestId());
      setMessage(`FBX 已保存到所选文件夹：${result.name}。`);
    } catch {
      setMessage("FBX 未能保存到所选文件夹；项目内的 FBX 资产未受影响。");
    } finally {
      setSavingFbx(false);
    }
  };
  const optimize = async () => {
    if (!model) return;
    setMessage("正在创建本地模型优化任务…");
    try {
      const result = await api.invokeTool(
        projectId,
        "model3d.optimize",
        { asset_id: model.id, target_triangles: targetTriangles },
        requestId(),
      );
      setMessage(result.summary);
      if (result.status === "queued") onQueued?.();
    } catch { setMessage("模型优化任务无法创建。请检查目标面数后重试。"); }
  };
  const openInBrowser = async () => {
    if (!model) return;
    setMessage("正在打开系统浏览器预览…");
    try {
      const blob = await api.assetContent(projectId, model.id);
      await new HostClient().openModelBrowser(Array.from(new Uint8Array(await blob.arrayBuffer())));
      setMessage("已在系统浏览器中打开受管模型预览。");
    } catch { setMessage("系统浏览器预览无法启动。请确认模型有效且默认浏览器可用。"); }
  };
  const showModel = async (next: AssetDto | null) => {
    const version = ++loadVersion.current;
    explicitSelection.current = true;
    setGenerationResultAssetId(null);
    const blob = next ? await api.assetContent(projectId, next.id) : null;
    if (version !== loadVersion.current) return;
    const nextUrl = blob ? URL.createObjectURL(blob) : localDefaultPreviewModelUrl();
    setPreviewState("loading");
    setUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return nextUrl;
    });
    setModel(next);
    setFallback(!next);
    setMessage(next ? "" : "正在显示本项目的内置资产信标，仅用于检查预览器；它不会上传、导出或写入项目。");
    hydrated.current = true;
    onWorkflowContextChange?.({
      asset_id: next?.id ?? null,
      target_triangles: targetTriangles,
      generation_job_id: null,
    });
  };
  const loadCurrentAsset = async () => {
    setSwitchingAsset(true);
    try {
      const assets = await api.assets(projectId);
      const current = assets.find((asset) => asset.is_current && asset.asset_type === "glb" && asset.trashed_at == null);
      if (!current) {
        setMessage("项目当前资产不是可用于 3D 预览的 GLB。");
        return;
      }
      if (current.id === model?.id) {
        setMessage(`${current.name} 已经是本页 3D 模型。`);
        return;
      }
      setRestoreAssetId(model?.id ?? null);
      await showModel(current);
      setMessage(`${current.name} 已加载到 3D 模型处理页；其他页签未改变。`);
    } catch {
      setMessage("无法加载项目当前 GLB。");
    } finally {
      setSwitchingAsset(false);
    }
  };
  const restoreLoadedAsset = async () => {
    if (restoreAssetId === undefined) return;
    setSwitchingAsset(true);
    try {
      const current = model?.id ?? null;
      const assets = await api.assets(projectId);
      const previous = restoreAssetId
        ? assets.find((asset) => asset.id === restoreAssetId && asset.asset_type === "glb") ?? null
        : null;
      if (restoreAssetId && !previous) {
        setMessage("加载前的 GLB 已不可用，无法恢复。");
        return;
      }
      await showModel(previous);
      setRestoreAssetId(current);
      setMessage(previous ? `已恢复加载前的 3D 模型：${previous.name}。` : "已恢复加载前的资产信标预览。");
    } catch {
      setMessage("无法恢复 3D 模型处理页加载前的状态。");
    } finally {
      setSwitchingAsset(false);
    }
  };

  const activeGeneration = generationJob != null && (
    generationJob.status === "queued"
    || generationJob.status === "running"
    || generationJob.status === "waiting"
  );
  const unsuccessfulGeneration = generationJob != null && (
    generationJob.status === "failed"
    || generationJob.status === "interrupted"
    || generationJob.status === "cancelled"
  );
  const showsGenerationStatus = activeGeneration || unsuccessfulGeneration;
  const activeConversion = conversionPending || conversionJob != null && (
    conversionJob.status === "queued"
    || conversionJob.status === "running"
    || conversionJob.status === "waiting"
  );
  const failedConversion = conversionJob != null && (
    conversionJob.status === "failed"
    || conversionJob.status === "interrupted"
    || conversionJob.status === "cancelled"
  );
  const showsModelStatus = showsGenerationStatus
    || activeConversion
    || failedConversion
    || conversionJob?.status === "succeeded";

  return <section className={`model-workspace${showsGenerationStatus ? " has-generation-status" : ""}${showsModelStatus ? " has-model-status" : ""}`} aria-labelledby="model-workspace-title" data-asset-id={model?.id ?? ""}>
    <header><div><p className="eyebrow">3D model</p><h1 id="model-workspace-title">{model?.name ?? (fallback ? "资产信标预览" : "GLB 预览")}</h1></div><div className="model-actions"><button onClick={() => void loadCurrentAsset()} disabled={switchingAsset}>加载当前资产</button><button onClick={() => void restoreLoadedAsset()} disabled={switchingAsset || restoreAssetId === undefined}><ArrowCounterClockwise size={18} />恢复加载前状态</button>{fallback && <ImportGlbAction projectId={projectId} api={api} onImported={() => { setReload((value) => value + 1); onImported?.(); }} />}<label className="model-target">目标面数<input type="number" min="4" max="10000000" value={targetTriangles} onChange={(event) => setTargetTriangles(Math.max(4, Number(event.target.value) || 4))} disabled={!model} /></label><button onClick={optimize} disabled={!model}><ArrowsClockwise size={18} />优化</button><button onClick={openInBrowser} disabled={!model}><GlobeSimple size={18} />浏览器预览</button><button onClick={screenshot} disabled={!model}><Camera size={18} />保存截图</button><button className="primary" onClick={exportFbx} disabled={!model || activeConversion}><DownloadSimple size={18} />{activeConversion ? "正在转换 FBX" : "导出 FBX"}</button></div></header>
    {showsModelStatus && <div className="model-status-stack">
      {activeGeneration && <p className="model-generation-status" role="status">3D 正在生成 · {generationJob.stage || "准备中"}{generationJob.progress === null ? "" : ` · ${Math.round(generationJob.progress)}%`}</p>}
      {unsuccessfulGeneration && <p className="model-generation-status model-generation-error" role="alert">{generationFailureMessage(generationJob)}</p>}
      {activeConversion && <p className="model-conversion-status" role="status"><SpinnerGap className="spin" size={18} />FBX 正在转换，完成后会保存在项目资产中。</p>}
      {failedConversion && <p className="model-conversion-status model-conversion-error" role="alert">FBX 转换失败：{conversionJob.error?.user_message ?? "请确认已安装 Blender，或在设置中指定其可执行文件。"}</p>}
      {conversionJob?.status === "succeeded" && <div className="model-conversion-status model-conversion-success" role="status"><span>FBX 转换完成，已保存在项目资产中：{conversionAsset?.name ?? "正在读取文件信息…"}</span><button onClick={() => void saveFbx()} disabled={!conversionAsset || savingFbx}><DownloadSimple size={17} />{savingFbx ? "正在保存" : "保存到文件夹"}</button></div>}
    </div>}
    {url ? <model-viewer ref={viewer as never} src={url} camera-controls auto-rotate shadow-intensity="1" exposure="1" alt={model?.name ?? "内置资产信标 3D 预览"} /> : <div className="model-placeholder"><Cube size={40} /><p>{message}</p></div>}
    {url && <p className={`model-hint${previewState === "error" ? " model-hint-error" : ""}`} role={previewState === "error" ? "alert" : undefined}>{previewState === "error" ? <><WarningCircle size={17} />模型预览加载失败。请重新加载当前资产，或使用“浏览器预览”确认模型文件。</> : <>{previewState === "loading" && <SpinnerGap className="spin" size={15} />}拖拽旋转，滚轮缩放，右键平移。{message || (previewState === "loading" ? "正在加载模型预览…" : "")}</>}</p>}
  </section>;
}
