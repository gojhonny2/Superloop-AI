import os
import anthropic
from models.base_model import BaseModel, get_model_timeout_seconds

class AnthropicModel(BaseModel):
    """
    Wrapper for Anthropic models (Claude 3.5 Sonnet, Claude 3 Opus, etc).
    Expects ANTHROPIC_API_KEY in the environment.
    """
    def __init__(self, name: str = "claude-3-5-sonnet-20240620", max_tokens: int | None = None):
        super().__init__(name, max_tokens=max_tokens)

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment variables.")

        self.timeout = get_model_timeout_seconds()
        self.client = anthropic.Anthropic(api_key=api_key, timeout=self.timeout)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """
        Calls the Anthropic Messages API.
        """
        message = self.client.messages.create(
            model=self.model_name,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        # Anthropic returns a list of content blocks
        return message.content[0].text
