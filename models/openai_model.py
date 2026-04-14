import os
from openai import OpenAI
from models.base_model import BaseModel, get_model_timeout_seconds

class OpenAIModel(BaseModel):
    """
    Wrapper for OpenAI models (GPT-4o, GPT-3.5-turbo, etc).
    Expects OPENAI_API_KEY in the environment.
    """
    def __init__(self, name: str = "gpt-4o", max_tokens: int | None = None):
        super().__init__(name, max_tokens=max_tokens)

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables.")

        self.timeout = get_model_timeout_seconds()
        self.client = OpenAI(api_key=api_key, timeout=self.timeout)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """
        Calls the OpenAI ChatCompletion API.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.7,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )

        return response.choices[0].message.content
