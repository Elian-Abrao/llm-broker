from __future__ import annotations


DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_REASONING_EFFORT = "medium"

DEFAULT_CODEX_MODELS = [
    {
        "id": "gpt-5.4",
        "label": "gpt-5.4",
        "description": "Balanced default for Codex-backed chat workflows.",
        "recommended": True,
    },
    {
        "id": "gpt-5",
        "label": "gpt-5",
        "description": "General-purpose GPT-5 model for broader compatibility.",
    },
    {
        "id": "gpt-5-mini",
        "label": "gpt-5-mini",
        "description": "Lower-latency GPT-5 option for lighter tasks.",
    },
]

DEFAULT_CODEX_REASONING_EFFORTS = [
    {
        "id": "none",
        "label": "None",
        "description": "Fastest profile with reasoning effectively disabled.",
    },
    {
        "id": "low",
        "label": "Low",
        "description": "Light reasoning for straightforward tasks.",
    },
    {
        "id": "medium",
        "label": "Medium",
        "description": "Balanced reasoning depth for most requests.",
        "recommended": True,
    },
    {
        "id": "high",
        "label": "High",
        "description": "More deliberate reasoning for harder prompts.",
    },
    {
        "id": "xhigh",
        "label": "XHigh",
        "description": "Maximum reasoning depth for the hardest prompts.",
    },
]


def normalize_codex_model(model: str | None) -> str:
    normalized = (model or "").strip()
    if not normalized or normalized == "gpt-5-nano":
        return DEFAULT_CODEX_MODEL
    return normalized


def normalize_reasoning_effort(effort: str | None) -> str:
    normalized = (effort or "").strip().lower()
    if not normalized:
        return DEFAULT_REASONING_EFFORT
    if normalized == "minimal":
        return "low"
    if normalized in {"none", "low", "medium", "high", "xhigh"}:
        return normalized
    return DEFAULT_REASONING_EFFORT


def build_capabilities(*, authenticated: bool, account_email: str | None) -> dict[str, object]:
    return {
        "provider": "codex",
        "billingMode": "monthly",
        "requiresAuth": True,
        "authenticated": authenticated,
        "accountEmail": account_email,
        "defaultModel": DEFAULT_CODEX_MODEL,
        "defaultReasoningEffort": DEFAULT_REASONING_EFFORT,
        "models": DEFAULT_CODEX_MODELS,
        "reasoningEfforts": DEFAULT_CODEX_REASONING_EFFORTS,
    }
