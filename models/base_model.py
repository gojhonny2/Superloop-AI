import os
from dotenv import load_dotenv

load_dotenv()

class BaseModel:
    """
    Abstract base class for all LLM models in the Superloop framework.
    """
    def __init__(self, model_name: str):
        self.model_name = model_name

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """
        Takes a prompt and returns the LLM's response.
        Must be implemented by subclasses (e.g., OpenAIModel, AnthropicModel).
        """
        raise NotImplementedError("Subclasses must implement the generate method.")
