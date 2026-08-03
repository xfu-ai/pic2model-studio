import {
  CloudCheck,
  Export,
  FirstAid,
  FloppyDisk,
  GearSix,
  ListChecks,
} from "@phosphor-icons/react";

export function TopBar({
  projectName,
  currentAssetName,
  readOnly,
  diagnosticsDisabled = false,
  onTasks,
  onExports,
  onDiagnostics,
  onSettings,
}: {
  projectName: string;
  currentAssetName?: string | null;
  readOnly: boolean;
  diagnosticsDisabled?: boolean;
  onTasks(): void;
  onExports(): void;
  onDiagnostics(): void;
  onSettings(): void;
}) {
  return (
    <header className="workbench-topbar">
      <div className="topbar-brand-row">
        <div className="topbar-brand" aria-label="图模工坊">
          <span className="topbar-brand-mark" aria-hidden="true" />
          <strong>图模工坊</strong>
        </div>
        <div className="topbar-actions">
          <span className="save-state">
            <FloppyDisk size={16} />
            {readOnly ? "只读" : "已保存"}
          </span>
          <button title="任务" aria-label="任务" onClick={onTasks}>
            <ListChecks size={19} />
          </button>
          <span className="service-state" title="本地服务正常">
            <CloudCheck size={17} />
            本地服务
          </span>
          <button title="导出" aria-label="导出" onClick={onExports}>
            <Export size={19} />
          </button>
          <button
            title="导出诊断包"
            aria-label="导出诊断包"
            disabled={diagnosticsDisabled}
            onClick={onDiagnostics}
          >
            <FirstAid size={19} />
          </button>
          <button title="设置" aria-label="设置" onClick={onSettings}>
            <GearSix size={19} />
          </button>
        </div>
      </div>
      <div className="topbar-context-row">
        <div className="topbar-project">
          <span>当前项目</span>
          <strong>{projectName}</strong>
        </div>
        <div className="topbar-breadcrumb">
          {currentAssetName ?? "未选择资产"}
        </div>
      </div>
    </header>
  );
}
