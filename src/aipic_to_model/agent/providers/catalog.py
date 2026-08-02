"""Versioned provider metadata generated from the frozen Pi source tree.

The inventory is deliberately data-only.  Protocol selection lives in the
adapter registry and Agent Core is never allowed to branch on provider names.
"""

from __future__ import annotations

from dataclasses import dataclass

FROZEN_PI_COMMIT = "8eef62ed3ea62d646a7fad92fa583fc8d71fec17"
CATALOG_SCHEMA_VERSION = 1
CHAT_PROVIDER_IDS = (
    "amazon-bedrock",
    "ant-ling",
    "anthropic",
    "azure-openai-responses",
    "cerebras",
    "cloudflare-ai-gateway",
    "cloudflare-workers-ai",
    "deepseek",
    "fireworks",
    "github-copilot",
    "google",
    "google-vertex",
    "groq",
    "huggingface",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "mistral",
    "moonshotai",
    "moonshotai-cn",
    "nvidia",
    "openai",
    "openai-codex",
    "opencode",
    "opencode-go",
    "openrouter",
    "qwen-token-plan",
    "qwen-token-plan-cn",
    "radius",
    "together",
    "vercel-ai-gateway",
    "xai",
    "xiaomi",
    "xiaomi-token-plan-ams",
    "xiaomi-token-plan-cn",
    "xiaomi-token-plan-sgp",
    "zai",
    "zai-coding-cn",
)
ADAPTER_IDS = (
    "openai-completions",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "bedrock-converse-stream",
    "google-generative-ai",
    "google-vertex",
    "mistral-conversations",
    "pi-messages",
    "openrouter-images",
)


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    name: str
    base_url: str
    adapter_id: str
    credential_ref: str = ""
    environment: tuple[str, ...] = ()
    alternate_adapter_ids: tuple[str, ...] = ()
    dynamic_models: bool = False
    default_headers: tuple[tuple[str, str], ...] = ()

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return (self.adapter_id, *self.alternate_adapter_ids)


_DEFAULTS: dict[str, tuple[str, str, str, tuple[str, ...], tuple[str, ...], bool]] = {
    "amazon-bedrock": (
        "Amazon Bedrock",
        "",
        "bedrock-converse-stream",
        ("AWS_BEARER_TOKEN_BEDROCK", "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
        (),
        False,
    ),
    "ant-ling": (
        "Ant Ling",
        "https://api.ant-ling.com/v1",
        "openai-completions",
        ("ANT_LING_API_KEY",),
        (),
        False,
    ),
    "anthropic": (
        "Anthropic",
        "https://api.anthropic.com",
        "anthropic-messages",
        ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN"),
        (),
        False,
    ),
    "azure-openai-responses": (
        "Azure OpenAI Responses",
        "",
        "azure-openai-responses",
        ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
        (),
        False,
    ),
    "cerebras": (
        "Cerebras",
        "https://api.cerebras.ai/v1",
        "openai-completions",
        ("CEREBRAS_API_KEY",),
        (),
        False,
    ),
    "cloudflare-ai-gateway": (
        "Cloudflare AI Gateway",
        "",
        "openai-completions",
        ("CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_GATEWAY_ID"),
        ("anthropic-messages", "openai-responses"),
        False,
    ),
    "cloudflare-workers-ai": (
        "Cloudflare Workers AI",
        "",
        "openai-completions",
        ("CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"),
        (),
        False,
    ),
    "deepseek": (
        "DeepSeek",
        "https://api.deepseek.com",
        "openai-completions",
        ("DEEPSEEK_API_KEY",),
        (),
        False,
    ),
    "fireworks": (
        "Fireworks",
        "https://api.fireworks.ai/inference",
        "openai-completions",
        ("FIREWORKS_API_KEY",),
        ("anthropic-messages",),
        False,
    ),
    "github-copilot": (
        "GitHub Copilot",
        "https://api.individual.githubcopilot.com",
        "openai-responses",
        ("COPILOT_GITHUB_TOKEN",),
        ("anthropic-messages", "openai-completions"),
        False,
    ),
    "google": (
        "Google",
        "https://generativelanguage.googleapis.com/v1beta",
        "google-generative-ai",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        (),
        False,
    ),
    "google-vertex": (
        "Google Vertex",
        "",
        "google-vertex",
        (
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_CLOUD_API_KEY",
        ),
        (),
        False,
    ),
    "groq": (
        "Groq",
        "https://api.groq.com/openai/v1",
        "openai-completions",
        ("GROQ_API_KEY",),
        (),
        False,
    ),
    "huggingface": (
        "Hugging Face",
        "https://router.huggingface.co/v1",
        "openai-completions",
        ("HF_TOKEN",),
        (),
        False,
    ),
    "kimi-coding": (
        "Kimi Coding",
        "https://api.kimi.com/coding",
        "anthropic-messages",
        ("KIMI_API_KEY",),
        (),
        False,
    ),
    "minimax": (
        "MiniMax",
        "https://api.minimax.io/anthropic",
        "anthropic-messages",
        ("MINIMAX_API_KEY",),
        (),
        False,
    ),
    "minimax-cn": (
        "MiniMax China",
        "https://api.minimaxi.com/anthropic",
        "anthropic-messages",
        ("MINIMAX_CN_API_KEY",),
        (),
        False,
    ),
    "mistral": (
        "Mistral",
        "https://api.mistral.ai",
        "mistral-conversations",
        ("MISTRAL_API_KEY",),
        (),
        False,
    ),
    "moonshotai": (
        "Moonshot AI",
        "https://api.moonshot.ai/v1",
        "openai-completions",
        ("MOONSHOT_API_KEY",),
        (),
        False,
    ),
    "moonshotai-cn": (
        "Moonshot AI China",
        "https://api.moonshot.cn/v1",
        "openai-completions",
        ("MOONSHOT_API_KEY",),
        (),
        False,
    ),
    "nvidia": (
        "NVIDIA",
        "https://integrate.api.nvidia.com/v1",
        "openai-completions",
        ("NVIDIA_API_KEY",),
        (),
        False,
    ),
    "openai": (
        "OpenAI",
        "https://api.openai.com/v1",
        "openai-responses",
        ("OPENAI_API_KEY",),
        (),
        False,
    ),
    "openai-codex": (
        "OpenAI Codex",
        "https://chatgpt.com/backend-api",
        "openai-codex-responses",
        (),
        (),
        False,
    ),
    "opencode": (
        "OpenCode",
        "",
        "openai-completions",
        ("OPENCODE_API_KEY",),
        ("anthropic-messages", "google-generative-ai", "openai-responses"),
        False,
    ),
    "opencode-go": (
        "OpenCode Go",
        "",
        "openai-completions",
        ("OPENCODE_GO_API_KEY",),
        ("anthropic-messages", "openai-responses"),
        False,
    ),
    "openrouter": (
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "openai-completions",
        ("OPENROUTER_API_KEY",),
        (),
        False,
    ),
    "qwen-token-plan": (
        "Qwen Token Plan",
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "openai-completions",
        ("QWEN_API_KEY",),
        (),
        False,
    ),
    "qwen-token-plan-cn": (
        "Qwen Token Plan China",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "openai-completions",
        ("QWEN_API_KEY",),
        (),
        False,
    ),
    "radius": (
        "Radius",
        "https://gateway.radiustools.ai",
        "pi-messages",
        ("RADIUS_API_KEY",),
        (),
        True,
    ),
    "together": (
        "Together",
        "https://api.together.ai/v1",
        "openai-completions",
        ("TOGETHER_API_KEY",),
        (),
        False,
    ),
    "vercel-ai-gateway": (
        "Vercel AI Gateway",
        "https://ai-gateway.vercel.sh",
        "anthropic-messages",
        ("AI_GATEWAY_API_KEY",),
        ("openai-completions", "openai-responses"),
        False,
    ),
    "xai": (
        "xAI",
        "https://api.x.ai/v1",
        "openai-completions",
        ("XAI_API_KEY",),
        ("openai-responses",),
        False,
    ),
    "xiaomi": (
        "Xiaomi",
        "https://api.xiaomimimo.com/v1",
        "openai-completions",
        ("XIAOMI_API_KEY",),
        (),
        False,
    ),
    "xiaomi-token-plan-ams": (
        "Xiaomi Token Plan AMS",
        "https://token-plan-ams.xiaomimimo.com/v1",
        "openai-completions",
        ("XIAOMI_API_KEY",),
        (),
        False,
    ),
    "xiaomi-token-plan-cn": (
        "Xiaomi Token Plan China",
        "https://token-plan-cn.xiaomimimo.com/v1",
        "openai-completions",
        ("XIAOMI_API_KEY",),
        (),
        False,
    ),
    "xiaomi-token-plan-sgp": (
        "Xiaomi Token Plan SGP",
        "https://token-plan-sgp.xiaomimimo.com/v1",
        "openai-completions",
        ("XIAOMI_API_KEY",),
        (),
        False,
    ),
    "zai": (
        "ZAI",
        "https://api.z.ai/api/coding/paas/v4",
        "openai-completions",
        ("ZAI_API_KEY",),
        (),
        False,
    ),
    "zai-coding-cn": (
        "ZAI Coding China",
        "https://open.bigmodel.cn/api/coding/paas/v4",
        "openai-completions",
        ("ZAI_API_KEY",),
        (),
        False,
    ),
}


def frozen_descriptors() -> tuple[ProviderDescriptor, ...]:
    descriptors = tuple(
        ProviderDescriptor(
            provider_id,
            name,
            base_url,
            adapter_id,
            f"agent/{provider_id}/default",
            environment,
            alternate,
            dynamic,
        )
        for provider_id, (
            name,
            base_url,
            adapter_id,
            environment,
            alternate,
            dynamic,
        ) in _DEFAULTS.items()
    )
    return descriptors + (
        ProviderDescriptor(
            "openrouter-images",
            "OpenRouter Images",
            "https://openrouter.ai/api/v1",
            "openrouter-images",
            "agent/openrouter-images/default",
            ("OPENROUTER_API_KEY",),
        ),
    )


def validate_descriptors(descriptors: tuple[ProviderDescriptor, ...]) -> None:
    ids = [item.provider_id for item in descriptors]
    if len(ids) != len(set(ids)):
        raise ValueError("Provider descriptor IDs must be unique.")
    if {adapter_id for item in descriptors for adapter_id in item.adapter_ids} - set(ADAPTER_IDS):
        raise ValueError("Provider descriptor references an unknown adapter.")
    if any(
        not item.provider_id or not item.name or not item.credential_ref for item in descriptors
    ):
        raise ValueError(
            "Provider descriptors require non-empty IDs, names, and credential references."
        )
    if set(ids) != {*CHAT_PROVIDER_IDS, "openrouter-images"}:
        raise ValueError("Provider descriptor inventory differs from the frozen Pi inventory.")
