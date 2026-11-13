"""
Configuração de LLM Providers.
Para chavear de provider, basta alterar LLM_PROVIDER no .env.

Providers suportados:
  ollama   → LLM local, gratuito, sem internet (recomendado para privacidade)
  openai   → GPT-4o, GPT-4o-mini, etc. Requer OPENAI_API_KEY
  groq     → Llama3, Mixtral ultra-rápido na cloud. Requer GROQ_API_KEY
  custom   → Qualquer servidor compatível com OpenAI API (LM Studio, etc.)
  webhook  → Envia POST para URL externa e usa campo "response" como resposta
"""

import os
from dataclasses import dataclass
from typing import Optional
from config import settings, tool_settings  # carrega o .env da raiz uma única vez


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    description: str


# ─── Definição dos providers ──────────────────────────────────────────────────

PROVIDERS: dict[str, ProviderConfig] = {

    "ollama": ProviderConfig(
        name="Ollama (Local)",
        base_url="http://localhost:11434/v1",
        api_key="ollama",                          # Ollama não precisa de key real
        model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        description="LLM local, gratuito, sem internet. Requer Ollama instalado.",
    ),

    "openai": ProviderConfig(
        name="OpenAI",
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        description="GPT-4o, GPT-4o-mini. Requer OPENAI_API_KEY.",
    ),

    "groq": ProviderConfig(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_key=os.getenv("GROQ_API_KEY", ""),
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        description="Llama3/Mixtral ultra-rápido na cloud. Requer GROQ_API_KEY.",
    ),

    "custom": ProviderConfig(
        name="Custom",
        base_url=os.getenv("CUSTOM_LLM_BASE_URL", "http://localhost:1234/v1"),
        api_key=os.getenv("CUSTOM_LLM_API_KEY", "local"),
        model=os.getenv("CUSTOM_LLM_MODEL", "local-model"),
        description="Qualquer servidor compatível com OpenAI API (LM Studio, etc.)",
    ),

    "webhook": ProviderConfig(
        name="Webhook",
        base_url=os.getenv("WEBHOOK_URL", ""),
        api_key=os.getenv("WEBHOOK_API_KEY", ""),
        model="webhook",
        description="Envia POST para URL externa. Requer WEBHOOK_URL e WEBHOOK_API_KEY.",
    ),
}


def get_active_provider() -> ProviderConfig:
    """
    Retorna o provider ativo com base em LLM_PROVIDER no .env.
    Faz validação básica de configuração.
    """
    provider_name = os.getenv("LLM_PROVIDER", "ollama").lower()

    if provider_name not in PROVIDERS:
        available = ", ".join(PROVIDERS.keys())
        raise ValueError(
            f"Provider '{provider_name}' desconhecido. "
            f"Disponíveis: {available}"
        )

    provider = PROVIDERS[provider_name]

    # Validar que a API key está definida (exceto Ollama e custom com key "local")
    if provider_name in ("openai", "groq") and not provider.api_key:
        key_var = f"{provider_name.upper()}_API_KEY"
        raise ValueError(
            f"Provider '{provider_name}' requer {key_var} definida no .env\n"
            f"Exemplo: {key_var}=sk-..."
        )

    if provider_name == "webhook":
        if not provider.base_url:
            raise ValueError(
                "Provider 'webhook' requer WEBHOOK_URL definida no .env\n"
                "Exemplo: WEBHOOK_URL=https://meu-servidor.com/webhook/endpoint"
            )
        if not provider.api_key:
            raise ValueError(
                "Provider 'webhook' requer WEBHOOK_API_KEY definida no .env"
            )

    return provider


def list_providers() -> None:
    """Imprime todos os providers disponíveis (útil para debug)."""
    active = os.getenv("LLM_PROVIDER", "ollama").lower()
    print("\n📋 LLM Providers disponíveis:")
    print("─" * 50)
    for key, p in PROVIDERS.items():
        marker = "→ ATIVO" if key == active else "  "
        print(f"{marker} [{key}] {p.name}")
        print(f"      Modelo : {p.model}")
        print(f"      Info   : {p.description}")
    print("─" * 50)
    print(f"Para chavear: alterar LLM_PROVIDER= no ficheiro .env\n")


def tool_calling_capability(provider):
    active = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if tool_settings.error:
        return False, tool_settings.error
    if not tool_settings.enabled:
        return False, "tools_disabled"
    if active == "webhook" or tool_settings.adapter != "openai-chat-completions":
        return False, "structured_adapter_unavailable"
    if (not tool_settings.provider_confirmed or tool_settings.provider != active
            or tool_settings.model != provider.model):
        return False, "provider_model_tools_unconfirmed"
    return True, ""
