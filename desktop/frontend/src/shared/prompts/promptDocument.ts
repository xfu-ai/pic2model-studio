export const PROMPT_SCHEMA = "formweaver.prompt.v1";

export type ManagedPromptDocument = {
  schema: typeof PROMPT_SCHEMA;
  analysis: { zh: string; en: string };
  generation: { zh: string; en: string };
  constraints: { preserve: string[]; avoid: string[] };
};

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

export function parseManagedPrompt(text: string): ManagedPromptDocument {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("Prompt 不是有效的 JSON 文档。");
  }
  if (!value || typeof value !== "object") throw new Error("Prompt 文档结构无效。");
  const document = value as Partial<ManagedPromptDocument>;
  if (
    document.schema !== PROMPT_SCHEMA
    || typeof document.analysis?.zh !== "string"
    || typeof document.analysis?.en !== "string"
    || typeof document.generation?.zh !== "string"
    || typeof document.generation?.en !== "string"
    || !stringArray(document.constraints?.preserve)
    || !stringArray(document.constraints?.avoid)
  ) throw new Error(`Prompt 必须符合 ${PROMPT_SCHEMA} 协议。`);
  if (!document.generation.zh.trim() || !document.generation.en.trim()) {
    throw new Error("Prompt 缺少可编辑的中英文生成描述。");
  }
  return document as ManagedPromptDocument;
}

export function promptPair(text: string) {
  const document = parseManagedPrompt(text);
  return { zh: document.generation.zh.trim(), en: document.generation.en.trim() };
}

export function readablePrompt(text: string) {
  try {
    const document = parseManagedPrompt(text);
    return [document.generation.zh.trim(), document.generation.en.trim()]
      .filter((value, index, values) => value && values.indexOf(value) === index)
      .join("\n\n");
  } catch {
    return text.trim();
  }
}
