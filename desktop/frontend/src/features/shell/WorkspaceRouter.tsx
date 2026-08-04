import type { ApiClient, WorkflowContexts, WorkspaceMode } from "../../shared/api/client";
import { useCallback, useEffect } from "react";
import { ImportGlbAction } from "../assets/ImportGlbAction";
import { ImportImageAction } from "../assets/ImportImageAction";
import { CaptureScreenAction } from "../assets/CaptureScreenAction";
import { ImportDropZone } from "../assets/ImportDropZone";
import { ImageWorkspace } from "../canvas/ImageWorkspace";
import { CompareWorkspace } from "../canvas/CompareWorkspace";
import { ModelViewport } from "../model/ModelViewport";
import { CandidateWorkspace } from "../canvas/CandidateWorkspace";
import { SelectionWorkspace } from "../canvas/SelectionWorkspace";
import { MultiviewWorkspace } from "../canvas/MultiviewWorkspace";
import { PromptImageWorkspace } from "../canvas/PromptImageWorkspace";
import { TargetExtractionWorkspace } from "../canvas/TargetExtractionWorkspace";
import { DEFAULT_WORKSPACE } from "../../shared/state/uiStore";

const labels: Record<WorkspaceMode, { title: string; description: string }> = {
  empty: { title: "建立你的资产工作台", description: "导入图片或 GLB，也可以直接告诉 Agent 想要交付的 3D 资产。" }, prompt_image: { title: "生成并比较创意图", description: "从受管描述创建并比较候选图片。" }, image: { title: "当前图片", description: "查看图片、版本和可用操作。" }, compare: { title: "分析内容与风格参考", description: "独立分析内容与风格参考，再形成可复用的设计说明。" }, selection: { title: "局部编辑", description: "创建并调整矩形区域。" }, target_extract: { title: "提取可建模主体", description: "直接框选或从 AI 拆解结果中获得干净主体。" }, candidate: { title: "方案选择", description: "选择要继续使用的结果。" }, multiview: { title: "生成和校准三视图", description: "生成并确认正、侧、背视图的有效范围。" }, model3d: { title: "预览、优化与导出 3D 模型", description: "检查、优化并导出模型资产。" }, task_waiting: { title: "任务正在执行", description: "任务在后台继续，你可以浏览其他工作台。" }, error_diagnostics: { title: "需要恢复工作台", description: "当前工作台状态无法恢复。" },
};

export function WorkspaceRouter({ mode, onRecover, onModeChange, onCurrentAssetChange, onContinueToMultiview, onOpenTasks, onOpenAssets, projectId, api, onImageImported, onGlbImported, referenceContext, onReferenceContextChange, workflowContexts, onWorkflowContextChange, imageGenerationJobId, onImageJobQueued, candidateResult }: { mode: WorkspaceMode; onRecover(): void; onModeChange?(mode: WorkspaceMode): void; onCurrentAssetChange?(): void; onContinueToMultiview?(assetId: string): void; onOpenTasks?(): void; onOpenAssets?(assetId: string): void; projectId?: string; api?: ApiClient; onImageImported?(): void; onGlbImported?(): void; referenceContext?: import("../../shared/api/client").ReferenceContextState; onReferenceContextChange?(patch: Partial<import("../../shared/api/client").ReferenceContextState>): void; workflowContexts?: WorkflowContexts; onWorkflowContextChange?(patch: Partial<WorkflowContexts>): void; imageGenerationJobId?: string | null; onImageJobQueued?(jobId: string): void; candidateResult?: { jobId: string; assetIds: string[] } | null }) {
  const item = labels[mode] ?? labels.error_diagnostics;
  const patchPromptImage = useCallback((prompt_image: WorkflowContexts["prompt_image"]) => {
    onWorkflowContextChange?.({ prompt_image });
    // The accepted rewrite is a new immutable Prompt version.  Keep the
    // reference context in lockstep so a later Agent/task refresh cannot load
    // the older pre-rewrite asset back into the editor.
    if (prompt_image.source_prompt_asset_id) {
      onReferenceContextChange?.({ merged_prompt_asset_id: prompt_image.source_prompt_asset_id });
    }
  }, [onReferenceContextChange, onWorkflowContextChange]);
  const patchTargetExtract = useCallback((target_extract: WorkflowContexts["target_extract"]) => onWorkflowContextChange?.({ target_extract }), [onWorkflowContextChange]);
  const patchMultiview = useCallback((multiview: WorkflowContexts["multiview"]) => onWorkflowContextChange?.({ multiview }), [onWorkflowContextChange]);
  const patchModel3d = useCallback((model3d: WorkflowContexts["model3d"]) => onWorkflowContextChange?.({ model3d }), [onWorkflowContextChange]);
  useEffect(() => {
    const prompt = workflowContexts?.prompt_image;
    const sourcePromptAssetId = prompt?.source_prompt_asset_id;
    if (
      !sourcePromptAssetId
      || (!prompt.zh_prompt.trim() && !prompt.en_prompt.trim())
      || referenceContext?.merged_prompt_asset_id === sourcePromptAssetId
    ) return;
    onReferenceContextChange?.({ merged_prompt_asset_id: sourcePromptAssetId });
  }, [
    onReferenceContextChange,
    referenceContext?.merged_prompt_asset_id,
    workflowContexts?.prompt_image.en_prompt,
    workflowContexts?.prompt_image.source_prompt_asset_id,
    workflowContexts?.prompt_image.zh_prompt,
  ]);
  if (mode === "prompt_image" && projectId && api) return <PromptImageWorkspace projectId={projectId} api={api} onModeChange={onModeChange ?? (() => undefined)} generationJobId={imageGenerationJobId} mergedPromptAssetId={referenceContext?.merged_prompt_asset_id} workflowContext={workflowContexts?.prompt_image} onWorkflowContextChange={patchPromptImage} onCurrentAssetChange={onCurrentAssetChange} onJobQueued={onImageJobQueued} />;
  if (mode === "image" && projectId && api) return <ImageWorkspace projectId={projectId} api={api} onModeChange={onModeChange ?? (() => undefined)} onModelJobQueued={(generation_job_id) => patchModel3d({ ...(workflowContexts?.model3d ?? DEFAULT_WORKSPACE.workflow_contexts.model3d), generation_job_id })} />;
  if (mode === "compare" && projectId && api) return <CompareWorkspace projectId={projectId} api={api} onModeChange={onModeChange ?? (() => undefined)} onCurrentAssetChange={onCurrentAssetChange} referenceContext={referenceContext} onReferenceContextChange={onReferenceContextChange} onImageJobQueued={onImageJobQueued} />;
  if (mode === "model3d" && projectId && api) return <ModelViewport projectId={projectId} api={api} workflowContext={workflowContexts?.model3d} onWorkflowContextChange={patchModel3d} onQueued={() => onModeChange?.("task_waiting")} onImported={onGlbImported} />;
  if (mode === "candidate" && projectId && api) return <CandidateWorkspace projectId={projectId} api={api} assetIds={candidateResult?.assetIds} sourceJobId={candidateResult?.jobId} onBackToTasks={candidateResult ? onOpenTasks : undefined} onSelected={() => { onCurrentAssetChange?.(); onModeChange?.("image"); }} />;
  if (mode === "selection" && projectId && api) return <SelectionWorkspace projectId={projectId} api={api} onDone={() => { onCurrentAssetChange?.(); onModeChange?.("image"); }} onCancel={() => onModeChange?.("image")} onQueued={() => onModeChange?.("task_waiting")} />;
  if (mode === "target_extract" && projectId && api) return <TargetExtractionWorkspace projectId={projectId} api={api} workflowContext={workflowContexts?.target_extract ?? DEFAULT_WORKSPACE.workflow_contexts.target_extract} onWorkflowContextChange={patchTargetExtract} onContinueToMultiview={(assetId) => { if (onContinueToMultiview) onContinueToMultiview(assetId); else onModeChange?.("multiview"); }} onOpenTasks={onOpenTasks} onOpenAssets={onOpenAssets} />;
  if (mode === "multiview" && projectId && api) return <MultiviewWorkspace projectId={projectId} api={api} workflowContext={workflowContexts?.multiview} onWorkflowContextChange={patchMultiview} modelWorkflowContext={workflowContexts?.model3d} onModelWorkflowContextChange={patchModel3d} onQueued={() => onModeChange?.("task_waiting")} onModeChange={(nextMode) => onModeChange?.(nextMode)} />;
  return <section className={`workspace-view workspace-${mode}`} aria-labelledby="workspace-title"><div className="workspace-grid" aria-hidden="true" /><div className="workspace-empty-card"><p className="eyebrow">图模工坊</p><h1 id="workspace-title">{item.title}</h1><p>{item.description}</p>{mode === "empty" && projectId && api && <div className="workspace-entry-actions"><ImportDropZone projectId={projectId} api={api} onImported={(kind) => kind === "glb" ? onGlbImported?.() : onImageImported?.()} />{onImageImported && <><ImportImageAction projectId={projectId} api={api} onImported={onImageImported} /><CaptureScreenAction projectId={projectId} api={api} onImported={onImageImported} /></>}{onGlbImported && <ImportGlbAction projectId={projectId} api={api} onImported={onGlbImported} />}</div>}{mode === "error_diagnostics" && <button className="primary" onClick={onRecover}>恢复默认工作台</button>}</div></section>;
}
