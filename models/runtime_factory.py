import os

from models.base_model import BaseModel


class RuntimeModelFactory:
    """
    Provider descriptor factory used by the updated CLI and dashboard server.
    """

    @staticmethod
    def create_model(descriptor: str, max_tokens: int | None = None) -> BaseModel:
        provider, model_name, base_url, env_key = _parse_descriptor(descriptor)

        if provider == "openai":
            from models.openai_model import OpenAIModel

            return OpenAIModel(model_name or "gpt-4o", max_tokens=max_tokens)
        if provider == "anthropic":
            from models.anthropic_model import AnthropicModel

            return AnthropicModel(model_name or "claude-3-5-sonnet-20240620", max_tokens=max_tokens)
        if provider == "gemini":
            from models.reliable_gemini_model import ReliableGeminiModel

            return ReliableGeminiModel(model_name or "gemini-1.5-pro", max_tokens=max_tokens)
        if provider == "openrouter":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("OPENROUTER_API_KEY"), "https://openrouter.ai/api/v1", max_tokens=max_tokens)
        if provider == "ollama":
            from models.universal_model import UniversalModel

            return UniversalModel(
                model_name,
                os.getenv("OLLAMA_KEY", "ollama"),
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                max_tokens=max_tokens,
            )
        if provider == "kilo":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("KILO_API_KEY"), "https://api.kilo.ai/api/gateway/", max_tokens=max_tokens)
        if provider == "deepseek":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("DEEPSEEK_API_KEY"), "https://api.deepseek.com/v1", max_tokens=max_tokens)
        if provider == "groq":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("GROQ_API_KEY"), "https://api.groq.com/openai/v1", max_tokens=max_tokens)
        if provider == "mistral":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("MISTRAL_API_KEY"), "https://api.mistral.ai/v1", max_tokens=max_tokens)
        if provider == "together":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("TOGETHER_API_KEY"), "https://api.together.xyz/v1", max_tokens=max_tokens)
        if provider == "custom":
            from models.universal_model import UniversalModel

            if not base_url:
                raise ValueError("Custom model base_url missing")
            return UniversalModel(model_name, _required_env(env_key), base_url, max_tokens=max_tokens)
        raise ValueError(f"Unknown provider: {provider}")


def _parse_descriptor(descriptor: str) -> tuple[str, str, str, str]:
    provider, sep, payload = descriptor.partition(":")
    if not sep:
        raise ValueError("Model descriptor must look like provider:model")

    provider = provider.strip().lower()
    payload = payload.strip()
    if not payload:
        raise ValueError("Model name missing")

    if provider == "custom":
        model_name, base_url, env_key = _parse_custom_payload(payload)
        return provider, model_name, base_url, env_key

    return provider, payload, "", ""


def _parse_custom_payload(payload: str) -> tuple[str, str, str]:
    marker = ":http://"
    marker_index = payload.find(marker)
    if marker_index == -1:
        marker = ":https://"
        marker_index = payload.find(marker)
    if marker_index == -1:
        raise ValueError("Custom model descriptor must be custom:<model>:<base_url>:<env_key>")

    model_name = payload[:marker_index].strip()
    remainder = payload[marker_index + 1:].strip()
    base_url, sep, env_key = remainder.rpartition(":")
    if not model_name or not sep or not base_url or not env_key:
        raise ValueError("Custom model descriptor must be custom:<model>:<base_url>:<env_key>")
    return model_name, base_url, env_key


def _required_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"{key} missing")
    return value
