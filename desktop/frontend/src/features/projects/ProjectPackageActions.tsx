import {
  CheckCircle,
  CircleNotch,
  DownloadSimple,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";
import { useState } from "react";
import type { ApiClient, ProjectDto } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import "./project-package-actions.css";

function requestId() {
  return `package-${crypto.randomUUID()}`;
}

type Notice = { tone: "success" | "error"; text: string };
type Activity = "choosing-export" | "exporting-project";

export function ProjectPackageActions({
  project,
  api,
  host = new HostClient(),
  onProject,
}: {
  project: ProjectDto;
  api: ApiClient;
  host?: HostClient;
  onProject(project: ProjectDto): void;
}) {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const busy = activity !== null;

  const exportProject = async () => {
    setActivity("choosing-export");
    setNotice(null);
    try {
      const exportCapabilityId = await host.chooseExportDirectory(project.id);
      if (!exportCapabilityId) return;
      setActivity("exporting-project");
      const result = await api.exportProject(project.id, exportCapabilityId, requestId());
      const fileName = typeof result.path === "string" ? result.path : null;
      setNotice({
        tone: "success",
        text: `项目备份已导出为 ${fileName ?? ".aipicproject 文件"}，包含当前项目的图片、3D 模型和编辑设置，可在本应用中重新打开。`,
      });
    } catch {
      setNotice({
        tone: "error",
        text: "未能生成项目备份文件。导出只会复制当前项目；正在编辑的图片、模型和工作记录仍保留在原处。",
      });
    } finally {
      setActivity(null);
    }
  };

  const activityText =
    activity === "choosing-export"
      ? "正在选择备份保存位置…"
      : activity === "exporting-project"
        ? "正在导出项目备份，请稍候…"
        : null;

  return (
    <section
      className="project-package-actions"
      aria-labelledby="project-package-title"
    >
      <p className="eyebrow">项目备份与迁移</p>
      <h1 id="project-package-title">备份或迁移项目</h1>
      <p>
        导出一个可重新打开的项目副本，适合备份、换电脑继续制作，或交给其他人继续编辑。
      </p>
      {(activityText || notice) && (
        <p
          className={`package-status ${activityText ? "loading" : notice?.tone}`}
          role="status"
        >
          {activityText ? <CircleNotch className="spin" size={18} /> : notice?.tone === "success" ? <CheckCircle size={18} /> : <WarningCircle size={18} />}
          {activityText ?? notice?.text}
        </p>
      )}
      <div className="package-action-groups">
        <section className="package-action-group" aria-labelledby="package-main">
          <h2 id="package-main">完整项目备份</h2>
          <p className="package-action-description">
            将受管图片、模型和工作记录打包为 .aipicproject 文件，可在本应用中再次打开。
          </p>
          <div className="package-action-row package-main-action">
            <button
              className="primary"
              disabled={busy}
              onClick={() => void exportProject()}
            >
              <DownloadSimple size={18} />
              导出项目备份…
            </button>
          </div>
        </section>
      </div>
      <span className="package-security-note">
        <ShieldCheck size={16} />
        导出位置由系统窗口选择；本应用不会把你的本地路径发送到服务端。
      </span>
    </section>
  );
}
