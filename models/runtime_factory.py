import os

from models.base_model import BaseModel

class RuntimeModelFactory:
    """
    Provider descriptor factory used by the updated CLI and dashboard server.
    """

    @staticmethod
    def create_model(descriptor: str) -> BaseModel:
        parts = descriptor.split(":")
        provider = parts[0].lower()
        model_name = parts[1] if len(parts) > 1 else ""

        if provider == "openai":
            from models.openai_model import OpenAIModel

            return OpenAIModel(model_name or "gpt-4o")
        if provider == "anthropic":
            from models.anthropic_model import AnthropicModel

            return AnthropicModel(model_name or "claude-3-5-sonnet-20240620")
        if provider == "gemini":
            from models.reliable_gemini_model import ReliableGeminiModel

            return ReliableGeminiModel(model_name or "gemini-1.5-pro")
        if provider == "openrouter":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("OPENROUTER_API_KEY"), "https://openrouter.ai/api/v1")
        if provider == "kilo":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("KILO_API_KEY"), "https://api.kilo.ai/api/gateway/")
        if provider == "deepseek":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("DEEPSEEK_API_KEY"), "https://api.deepseek.com/v1")
        if provider == "groq":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("GROQ_API_KEY"), "https://api.groq.com/openai/v1")
        if provider == "mistral":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("MISTRAL_API_KEY"), "https://api.mistral.ai/v1")
        if provider == "together":
            from models.universal_model import UniversalModel

            return UniversalModel(model_name, _required_env("TOGETHER_API_KEY"), "https://api.together.xyz/v1")
        if provider == "custom":
            from models.universal_model import UniversalModel

            base_url = parts[2] if len(parts) > 2 else ""
            env_key = parts[3] if len(parts) > 3 else "CUSTOM_API_KEY"
            return UniversalModel(model_name, _required_env(env_key), base_url)
        raise ValueError(f"Unknown provider: {provider}")


def _required_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"{key} missing")
    return value
