import { useEffect, useState } from "react";
import type { ApiClient, LocalProviderStatusDto, ServiceProviderStatusDto } from "../../shared/api/client";
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

function providerStateText(provider: { available: boolean; configured: boolean; reason: string | null }) {
  if (provider.available) return "可用";
  if (!provider.configured || provider.reason === "PROVIDER_NOT_CONFIGURED") return "未配置";
  if (provider.reason === "PROVIDER_AUTH_FAILED") return "凭据无效";
  if (provider.reason === "PROVIDER_RATE_LIMITED") return "请求受限";
  if (provider.reason === "not_checked") return "待检测";
  if (provider.reason === "model_not_installed") return "模型未安装";
  if (provider.reason === "runtime_not_configured") return "运行时未配置";
  if (provider.reason === "runtime_unavailable") return "运行时不可用";
  if (provider.reason === "response_invalid") return "响应异常";
  return "暂不可用";
}

export function SettingsDialog({ api, onClose }: { api: ApiClient; onClose(): void }) {
  const [blenderPath, setBlenderPath] = useState("");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [priority, setPriority] = useState<"tripo" | "meshy">("tripo");
  const [agentModel, setAgentModel] = useState<"deepseek-v4-flash" | "qwen3-vl:8b" | "qwen3-vl:4b">("qwen3-vl:8b");
  const [imageBackend, setImageBackend] = useState<"local" | "remote" | "auto">("auto");
  const [model3dBackend, setModel3dBackend] = useState<"local" | "remote" | "auto">("auto");
  const [probeInterval, setProbeInterval] = useState(300);
  const [providerStatus, setProviderStatus] = useState<ServiceProviderStatusDto | null>(null);
  const [localProviderStatus, setLocalProviderStatus] = useState<LocalProviderStatusDto | null>(null);
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

  const loadLocalProviderStatus = async () => {
    try {
      setLocalProviderStatus(await api.localProviders());
    } catch {
      setMessage("无法读取本地模型状态。");
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
      const configuredAgentModel = settings.agent_model;
      if (configuredAgentModel === "deepseek-v4-flash" || configuredAgentModel === "qwen3-vl:8b" || configuredAgentModel === "qwen3-vl:4b") {
        setAgentModel(configuredAgentModel);
      }
      const configuredImageBackend = settings.image_generation_backend;
      if (configuredImageBackend === "local" || configuredImageBackend === "remote" || configuredImageBackend === "auto") {
        setImageBackend(configuredImageBackend);
      }
      const configuredModel3dBackend = settings.model3d_generation_backend;
      if (configuredModel3dBackend === "local" || configuredModel3dBackend === "remote" || configuredModel3dBackend === "auto") {
        setModel3dBackend(configuredModel3dBackend);
      }
    }).catch(() => setMessage("无法读取设置。"));
    void loadProviderStatus();
    void loadLocalProviderStatus();
    const timer = window.setInterval(() => {
      void loadProviderStatus();
      void loadLocalProviderStatus();
    }, 15_000);
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

  const refreshLocalProviders = async () => {
    setChecking(true);
    try {
      setLocalProviderStatus(await api.refreshLocalProviders(requestId()));
      setMessage("本地模型状态已更新；检测不会下载模型，也不会启动生成任务。");
    } catch {
      setMessage("本地模型检测失败。");
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
      agent_model: agentModel,
      image_generation_backend: imageBackend,
      model3d_generation_backend: model3dBackend,
    };
    try {
      await api.updateSettings(patch, requestId());
      setMessage("设置已保存；新的生成任务会在创建前冻结到具体 Provider，任务开始后不会自动换模型。");
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
              文生图执行后端
              <select aria-label="文生图执行后端" value={imageBackend} onChange={(event) => setImageBackend(event.target.value as "local" | "remote" | "auto")}>
                <option value="local">本地 Z-Image-Turbo</option>
                <option value="auto">自动（本地可用时优先）</option>
                <option value="remote">远程服务</option>
              </select>
            </label>
            <label>
              图生 3D 执行后端
              <select aria-label="图生 3D 执行后端" value={model3dBackend} onChange={(event) => setModel3dBackend(event.target.value as "local" | "remote" | "auto")}>
                <option value="local">本地 TripoSR（仅单图）</option>
                <option value="auto">自动（单图本地优先）</option>
                <option value="remote">远程 Tripo3D</option>
              </select>
            </label>
            <p className="settings-note">TripoSR 只支持单图重建；多视图 3D 始终使用远程 Provider，并在提交前要求审批。</p>
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

          <section className="settings-section" aria-labelledby="agent-model-section-title">
            <div>
              <strong id="agent-model-section-title">Agent 对话模型</strong>
              <p>新建 Agent 对话将使用此设置；已经创建的对话会保持原有模型，避免中途改变上下文和执行行为。</p>
            </div>
            <label htmlFor="agent-model">
              Agent 模型
              <select id="agent-model" value={agentModel} onChange={(event) => setAgentModel(event.target.value as "deepseek-v4-flash" | "qwen3-vl:8b" | "qwen3-vl:4b")}>
                <option value="qwen3-vl:8b">Qwen3-VL 8B（本地 Ollama，默认）</option>
                <option value="qwen3-vl:4b">Qwen3-VL 4B（本地 Ollama）</option>
                <option value="deepseek-v4-flash">DeepSeek Agent（远程）</option>
              </select>
            </label>
          </section>

          <section className="settings-section" aria-labelledby="local-provider-title">
            <div className="settings-section-heading">
              <div>
                <strong id="local-provider-title">本地开源模型</strong>
                <p>状态检测只检查 Ollama、受控执行器和模型文件是否就绪，不下载权重，也不会启动对话、生图或 3D 生成。</p>
              </div>
              <button type="button" onClick={() => void refreshLocalProviders()} disabled={checking}>
                {checking ? "检测中…" : "检测本地模型"}
              </button>
            </div>
            <div className="local-provider-grid" role="status" aria-live="polite">
              {localProviderStatus?.providers.map((provider) => (
                <article className="local-provider-card" key={provider.profile}>
                  <div className="credential-card-heading">
                    <span className={`provider-dot ${provider.available ? "available" : "unavailable"}`} aria-hidden="true" />
                    <div>
                      <strong>{provider.label}</strong>
                      <small>{provider.model} · {providerStateText(provider)}</small>
                      <small>{provider.engine_version ? `引擎 ${provider.engine_version}` : provider.engine}</small>
                    </div>
                  </div>
                  <small>{provider.capabilities.join(" · ")}</small>
                  <small>{provider.license.identifier} · <a href={provider.license.source_url} target="_blank" rel="noreferrer">项目来源</a></small>
                  <small className="settings-note">{provider.license.notice}</small>
                </article>
              )) ?? <p>正在读取本地模型状态…</p>}
            </div>
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
