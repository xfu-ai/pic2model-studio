import { useEffect, useState } from "react";
import type { ApiClient, ServiceProviderStatusDto } from "../../shared/api/client";
import "./settings-dialog.css";

const TRIPO_PROFILE = "tripo3d/default";
const MESHY_PROFILE = "meshy/default";
const GEMINI_PROFILE = "gemini/google/default";
const DEEPSEEK_PROFILE = "agent/deepseek/default";

const credentialProfiles = [
  { profile: TRIPO_PROFILE, label: "Tripo3D", description: "图片生成、图片编辑与 3D 生成" },
  { profile: MESHY_PROFILE, label: "Meshy", description: "图片生成与图片编辑" },
  { profile: GEMINI_PROFILE, label: "Gemini", description: "图片分析与提示词改写" },
  { profile: DEEPSEEK_PROFILE, label: "DeepSeek Agent", description: "Agent 对话与工作流编排" },
] as const;

function requestId() {
  return crypto.randomUUID();
}

function providerStateText(provider: ServiceProviderStatusDto["providers"][number]) {
  if (provider.available) return "可用";
  if (!provider.configured || provider.reason === "PROVIDER_NOT_CONFIGURED") return "未配置";
  if (provider.reason === "PROVIDER_AUTH_FAILED") return "凭据无效";
  if (provider.reason === "PROVIDER_RATE_LIMITED") return "请求受限";
  if (provider.reason === "not_checked") return "待检测";
  return "暂不可用";
}

export function SettingsDialog({ api, onClose }: { api: ApiClient; onClose(): void }) {
  const [blenderPath, setBlenderPath] = useState("");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [priority, setPriority] = useState<"tripo" | "meshy">("tripo");
  const [probeInterval, setProbeInterval] = useState(300);
  const [providerStatus, setProviderStatus] = useState<ServiceProviderStatusDto | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkingProfile, setCheckingProfile] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const loadProviderStatus = async () => {
    try {
      setProviderStatus(await api.serviceProviders());
    } catch {
      setMessage("无法读取生图服务状态。");
    }
  };

  useEffect(() => {
    void api.settings().then((settings) => {
      setBlenderPath(typeof settings.blender_path === "string" ? settings.blender_path : "");
      const configuredPriority = settings.image_provider_priority;
      if (Array.isArray(configuredPriority) && configuredPriority[0] === MESHY_PROFILE) {
        setPriority("meshy");
      }
      const interval = settings.provider_probe_interval_seconds;
      if (typeof interval === "number" && interval >= 60 && interval <= 3600) {
        setProbeInterval(interval);
      }
    }).catch(() => setMessage("无法读取设置。"));
    void loadProviderStatus();
    const timer = window.setInterval(() => void loadProviderStatus(), 15_000);
    return () => window.clearInterval(timer);
  }, [api]);

  const refreshProviders = async () => {
    setChecking(true);
    try {
      setProviderStatus(await api.refreshServiceProviders(requestId()));
      setMessage("全部服务状态已更新；检测不会创建生成任务或 Agent 对话。");
    } catch {
      setMessage("生图服务检测失败。");
    } finally {
      setChecking(false);
    }
  };

  const save = async () => {
    const imageProviderPriority = priority === "tripo"
      ? [TRIPO_PROFILE, MESHY_PROFILE]
      : [MESHY_PROFILE, TRIPO_PROFILE];
    const patch: Record<string, unknown> = {
      blender_path: blenderPath.trim() || null,
      image_provider_priority: imageProviderPriority,
      provider_probe_interval_seconds: probeInterval,
    };
    try {
      await api.updateSettings(patch, requestId());
      setMessage("设置已保存；后续文生图、图生图和图片编辑都会按当前优先级选择可用服务。");
      await refreshProviders();
    } catch {
      setMessage("设置保存失败。");
    }
  };

  const probeProvider = async (providerProfile: string) => {
    const providerLabel = credentialProfiles.find((item) => item.profile === providerProfile)?.label ?? "服务";
    setCheckingProfile(providerProfile);
    try {
      setProviderStatus(await api.probeServiceProvider(providerProfile, requestId()));
      setMessage(`${providerLabel} 凭据检测已完成。`);
    } catch {
      setMessage("服务凭据检测失败。");
    } finally {
      setCheckingProfile(null);
    }
  };

  const saveSecret = async (providerProfile: string) => {
    const secret = secrets[providerProfile]?.trim();
    if (!secret) return;
    setCheckingProfile(providerProfile);
    try {
      const result = await api.setSecret(providerProfile, secret, requestId());
      setSecrets((current) => ({ ...current, [providerProfile]: "" }));
      setProviderStatus(await api.probeServiceProvider(providerProfile, requestId()));
      setMessage(`凭据已保存（${result.mask}）并完成检测。`);
    } catch {
      setMessage("凭据保存或检测失败。");
    } finally {
      setCheckingProfile(null);
    }
  };

  return (
    <div className="dialog-backdrop">
      <section className="dialog settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <h2 id="settings-title">设置</h2>
        <div className="settings-scroll">
          <section className="settings-section" aria-labelledby="image-routing-title">
            <div>
              <strong id="image-routing-title">图片生成与编辑路由</strong>
              <p>文生图、图生图和图片编辑都会在提交前选择优先级最高且可用的服务；提交后不会自动换服务，以免重复扣费。</p>
            </div>
            <label>
              优先顺序
              <select value={priority} onChange={(event) => setPriority(event.target.value as "tripo" | "meshy")}>
                <option value="tripo">Tripo3D → Meshy</option>
                <option value="meshy">Meshy → Tripo3D</option>
              </select>
            </label>
            <label>
              自动检测间隔（秒）
              <input type="number" min={60} max={3600} step={60} value={probeInterval} onChange={(event) => setProbeInterval(Number(event.target.value))} />
            </label>
            <p className="settings-note">检测只读取账户/接口状态，不创建生图任务，因此不消耗生成额度；它仍会占用普通 API 请求与速率限制。建议保持 300 秒或更长。</p>
          </section>

          <section className="settings-section settings-secret" aria-labelledby="credential-title">
            <div className="settings-section-heading">
              <div>
                <strong id="credential-title">服务凭据</strong>
                <p>所有 API Key 均独立配置到系统凭据库。预检只读取服务元数据或账户状态，不创建生成任务或 Agent 对话。</p>
              </div>
              <button type="button" onClick={() => void refreshProviders()} disabled={checking}>
                {checking ? "全部检测中…" : "全部检测"}
              </button>
            </div>
            <div className="credential-grid" role="status" aria-live="polite">
              {credentialProfiles.map((definition) => {
                const provider = providerStatus?.providers.find((item) => item.profile === definition.profile);
                const busy = checkingProfile === definition.profile;
                return (
                  <article className="credential-card" key={definition.profile}>
                    <div className="credential-card-heading">
                      <span className={`provider-dot ${provider?.available ? "available" : "unavailable"}`} aria-hidden="true" />
                      <div>
                        <strong>{definition.label}</strong>
                        <small>{definition.description}</small>
                        <small>{provider ? `${provider.model} · ${providerStateText(provider)}` : "正在读取状态…"}</small>
                      </div>
                    </div>
                    <label>
                      {definition.label} API Key
                      <input
                        type="password"
                        value={secrets[definition.profile] ?? ""}
                        onChange={(event) => setSecrets((current) => ({ ...current, [definition.profile]: event.target.value }))}
                        placeholder="输入新 Key；已配置的 Key 不会回显"
                        autoComplete="new-password"
                      />
                    </label>
                    <div className="credential-actions">
                      <button type="button" onClick={() => void probeProvider(definition.profile)} disabled={busy || checking}>
                        {busy ? "检测中…" : "检测现有凭据"}
                      </button>
                      <button type="button" className="primary" onClick={() => void saveSecret(definition.profile)} disabled={busy || checking || !(secrets[definition.profile]?.trim())}>
                        保存并检测
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </section>

          <section className="settings-section" aria-labelledby="converter-title">
            <strong id="converter-title">本地模型转换</strong>
            <p>使用本机 Blender 导出完整 FBX；未配置时会自动查找已安装的 Blender，并保留基础几何兜底。</p>
            <label>Blender 可执行文件<input value={blenderPath} onChange={(event) => setBlenderPath(event.target.value)} placeholder="C:\\Program Files\\Blender Foundation\\Blender\\blender.exe" /></label>
          </section>
        </div>
        {message && <p className="settings-message">{message}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>关闭</button>
          <button type="button" className="primary" onClick={() => void save()}>保存设置</button>
        </div>
      </section>
    </div>
  );
}
