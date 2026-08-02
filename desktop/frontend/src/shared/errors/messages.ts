const messages: Record<string, string> = {
  SECURITY_AUTH_REQUIRED: "本地服务会话已失效，请重新启动应用。",
  SECURITY_ORIGIN_REJECTED: "应用安全校验未通过，请重新启动应用。",
  PROJECT_NOT_FOUND: "找不到此项目；请从最近项目中重新打开。",
  PROJECT_READ_ONLY: "项目为只读状态；请另存为后继续。",
  SCHEMA_VALIDATION_FAILED: "请求格式无效，未执行任何更改。",
  NETWORK_ERROR: "本地服务暂时不可用，请稍后重试。",
};

export function userMessage(code: string): string {
  return messages[code] ?? "操作未完成。可查看诊断并重试。";
}
