import type { JobDto } from "../../shared/api/client";

export type JobResultKind =
  | "candidates"
  | "target_extract"
  | "multiview"
  | "model3d"
  | "fbx"
  | "asset";

export type JobPresentation = {
  title: string;
  running: string;
  completed: string;
  resultKind: JobResultKind;
};

const generic: JobPresentation = {
  title: "后台任务",
  running: "正在处理当前项目中的受管任务。",
  completed: "任务已完成，结果已保存到当前项目。",
  resultKind: "asset",
};

const registry: Record<string, JobPresentation> = {
  "image.analyze_content": {
    title: "分析内容参考图",
    running: "正在识别主体、构图和内容描述。",
    completed: "内容分析已经完成。",
    resultKind: "asset",
  },
  "image.analyze_style": {
    title: "分析风格参考图",
    running: "正在识别画风、材质、光照和色彩特征。",
    completed: "风格分析已经完成。",
    resultKind: "asset",
  },
  "image.evaluate_3d_suitability": {
    title: "检查图片的 3D 建模适用性",
    running: "正在评估主体完整性、视角和建模风险。",
    completed: "3D 建模适用性检查已经完成。",
    resultKind: "asset",
  },
  "prompt.rewrite": {
    title: "优化生成提示词",
    running: "正在整理和优化当前提示词。",
    completed: "提示词已经优化并保存。",
    resultKind: "asset",
  },
  "image.generate": {
    title: "根据 Prompt 生成图片",
    running: "正在按照已保存的提示词生成候选图片。",
    completed: "候选图片已经生成。",
    resultKind: "candidates",
  },
  "image.transform": {
    title: "转换图片风格",
    running: "正在根据参考信息转换当前图片。",
    completed: "图片风格转换已经完成。",
    resultKind: "candidates",
  },
  "image.generate_variants": {
    title: "生成图片候选",
    running: "正在基于合并后的 Prompt 生成多个候选版本。",
    completed: "候选版本已经生成。",
    resultKind: "candidates",
  },
  "image.upscale": {
    title: "放大并增强图片",
    running: "正在放大图片并补充细节。",
    completed: "增强后的图片已经生成。",
    resultKind: "candidates",
  },
  "image.upscale_local": {
    title: "本地放大并增强图片",
    running: "正在使用离线模型放大图片并补充细节。",
    completed: "本地增强后的图片已经生成。",
    resultKind: "candidates",
  },
  "image.remove_background": {
    title: "移除图片背景",
    running: "正在分离主体并移除背景。",
    completed: "透明背景图片已经生成。",
    resultKind: "candidates",
  },
  "image.inpaint_selection": {
    title: "AI 图片编辑",
    running: "正在根据选区和提示词重绘图片。",
    completed: "选中区域已经重绘。",
    resultKind: "candidates",
  },
  "element.split": {
    title: "提取建模目标",
    running: "正在从图片中拆分出干净的建模目标。",
    completed: "目标提取结果已经生成。",
    resultKind: "target_extract",
  },
  "element.export_transparent": {
    title: "导出透明目标图",
    running: "正在整理透明背景目标图。",
    completed: "透明目标图已经生成。",
    resultKind: "target_extract",
  },
  "selection.auto_suggest_boxes": {
    title: "识别可选目标区域",
    running: "正在识别图片中适合提取的目标区域。",
    completed: "候选目标区域已经识别。",
    resultKind: "asset",
  },
  "multiview.generate": {
    title: "生成三视图",
    running: "正在生成正面、侧面和背面参考图。",
    completed: "三视图已经生成。",
    resultKind: "multiview",
  },
  "multiview.regenerate_view": {
    title: "三视图修正",
    running: "正在重新生成选中的三视图方向。",
    completed: "选中的视图已经重新生成。",
    resultKind: "multiview",
  },
  "multiview.detect_regions": {
    title: "识别三视图区域",
    running: "正在定位正面、侧面和背面区域。",
    completed: "三视图区域已经识别。",
    resultKind: "multiview",
  },
  "multiview.validate": {
    title: "检查三视图",
    running: "正在检查三视图的一致性和完整性。",
    completed: "三视图检查已经完成。",
    resultKind: "multiview",
  },
  "model3d.generate": {
    title: "图片生成 3D 模型",
    running: "正在根据选定图片创建可预览的 3D 模型。",
    completed: "3D 模型已经生成。",
    resultKind: "model3d",
  },
  "model3d.import_local": {
    title: "导入本地 3D 模型",
    running: "正在检查并导入本地 3D 模型。",
    completed: "本地 3D 模型已经导入。",
    resultKind: "model3d",
  },
  "model3d.convert": {
    title: "转换为 FBX",
    running: "正在将当前模型转换为 FBX。",
    completed: "FBX 文件已经生成。",
    resultKind: "fbx",
  },
  "model3d.optimize": {
    title: "优化 3D 模型",
    running: "正在按照目标面数优化模型。",
    completed: "优化后的 3D 模型已经生成。",
    resultKind: "model3d",
  },
  "model3d.package": {
    title: "打包 3D 模型",
    running: "正在整理模型和关联文件。",
    completed: "3D 模型包已经生成。",
    resultKind: "asset",
  },
  "project.export_package": {
    title: "导出项目包",
    running: "正在整理项目资产和工作流状态。",
    completed: "项目包已经生成。",
    resultKind: "asset",
  },
};

export function jobPresentation(job: Pick<JobDto, "job_type">): JobPresentation {
  return registry[job.job_type ?? ""] ?? {
    ...generic,
    title: job.job_type ? `后台任务 · ${job.job_type}` : generic.title,
  };
}

export function jobSummary(job: JobDto): string {
  const presentation = jobPresentation(job);
  if (job.status === "succeeded") return presentation.completed;
  if (job.status === "failed") {
    return job.error?.user_message ?? `${presentation.title}未完成。`;
  }
  if (job.status === "cancelled") return "任务已取消，已有结果仍保留在项目中。";
  if (job.status === "interrupted") return "本地处理已中断，可以查看详情后决定是否重试。";
  return presentation.running;
}

export function resultActionLabel(job: JobDto): string {
  const count = job.output_asset_ids.length;
  switch (jobPresentation(job).resultKind) {
    case "candidates":
      return count > 1 ? `查看 ${count} 个候选图` : "查看候选图";
    case "target_extract":
      return "继续建模主体提取";
    case "multiview":
      return "查看三视图";
    case "model3d":
      return "预览 3D 模型";
    case "fbx":
      return "查看并导出 FBX";
    default:
      return count > 1 ? `查看 ${count} 个结果` : "查看结果";
  }
}

export function taskTypeOptions(jobs: JobDto[]) {
  return [...new Set(jobs.map((job) => job.job_type).filter((value): value is string => Boolean(value)))]
    .sort((left, right) => jobPresentation({ job_type: left }).title.localeCompare(jobPresentation({ job_type: right }).title))
    .map((value) => ({ value, label: jobPresentation({ job_type: value }).title }));
}
