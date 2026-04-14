import os
from dotenv import load_dotenv

load_dotenv()


DEFAULT_MODEL_TIMEOUT_SEC = 240.0
DEFAULT_MAX_TOKENS = 32000


def get_model_timeout_seconds(default: float = DEFAULT_MODEL_TIMEOUT_SEC) -> float:
    raw = os.getenv("SUPERLOOP_MODEL_TIMEOUT_SEC", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def clamp_max_tokens(value: int | float | None, default: int = DEFAULT_MAX_TOKENS) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < 1000:
        return 1000
    if n > 1_000_000:
        return 1_000_000
    return n


class BaseModel:
    """
    Abstract base class for all LLM models in the Superloop framework.
    """
    def __init__(self, model_name: str, max_tokens: int | None = None):
        self.model_name = model_name
        self.max_tokens = clamp_max_tokens(max_tokens)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """
        Takes a prompt and returns the LLM's response.
        Must be implemented by subclasses (e.g., OpenAIModel, AnthropicModel).
        """
        raise NotImplementedError("Subclasses must implement the generate method.")
