import {
  BoundingBox,
  Cube,
  HouseLine,
  ImageSquare,
  MagicWand,
  SquaresFour,
} from "@phosphor-icons/react";
import type { WorkspaceMode } from "../../shared/api/client";

export type WorkflowMode =
  | "image"
  | "compare"
  | "prompt_image"
  | "target_extract"
  | "multiview"
  | "model3d";

const stages = [
  {
    label: "素材工作台",
    summary: "管理输入与版本",
    items: [
      {
        mode: "image",
        label: "当前图片",
        shortLabel: "图片",
        icon: HouseLine,
        description: "查看当前图片、切换版本并选择后续操作",
      },
    ],
  },
  {
    label: "创意定稿",
    summary: "分析风格与生成图片",
    items: [
      {
        mode: "compare",
        label: "内容与风格分析",
        shortLabel: "风格分析",
        icon: MagicWand,
        description: "独立分析内容与风格参考，形成可复用的设计说明",
      },
      {
        mode: "prompt_image",
        label: "创意图生成",
        shortLabel: "创意图",
        icon: ImageSquare,
        description: "从受管描述生成并比较创意图片",
      },
    ],
  },
  {
    label: "建模准备",
    summary: "提取主体与制作三视图",
    items: [
      {
        mode: "target_extract",
        label: "建模主体提取",
        shortLabel: "主体提取",
        icon: BoundingBox,
        description: "从复杂图片中提取干净、可建模的主体",
      },
      {
        mode: "multiview",
        label: "三视图制作",
        shortLabel: "三视图",
        icon: SquaresFour,
        description: "确认正、侧、背结构视图及其有效范围",
      },
    ],
  },
  {
    label: "资产交付",
    summary: "处理、检查、导出",
    items: [
      {
        mode: "model3d",
        label: "3D 模型处理",
        shortLabel: "3D 模型",
        icon: Cube,
        description: "生成或导入模型，完成检查、优化与格式导出",
      },
    ],
  },
] as const;

export function WorkflowSwitcher({
  mode,
  onSelect,
}: {
  mode: WorkspaceMode;
  onSelect(mode: WorkflowMode): void;
}) {
  const selected =
    mode === "selection" || mode === "candidate" ? "image" : mode;

  return (
    <nav className="workflow-switcher" aria-label="产品工作台">
      <div className="workflow-stage-strip">
        {stages.map((stage) => {
          const active = stage.items.some((item) => item.mode === selected);
          return (
            <section
              key={stage.label}
              className={`workflow-stage${active ? " active" : ""}`}
              aria-label={stage.label}
            >
              <header className="workflow-stage-header">
                <strong>{stage.label}</strong>
                <small>{stage.summary}</small>
              </header>
              <div className="workflow-stage-tools">
                {stage.items.map(({ mode: itemMode, label, shortLabel, icon: Icon, description }) => (
                  <button
                    key={itemMode}
                    type="button"
                    className={selected === itemMode ? "active" : undefined}
                    aria-label={label}
                    aria-pressed={selected === itemMode}
                    onClick={() => onSelect(itemMode)}
                    title={description}
                  >
                    <Icon size={18} />
                    <span className="workflow-tab-full">{label}</span>
                    <span className="workflow-tab-short" aria-hidden="true">{shortLabel}</span>
                  </button>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </nav>
  );
}
