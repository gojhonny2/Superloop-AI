import os
from openai import OpenAI
from models.base_model import BaseModel

class UniversalModel(BaseModel):
    """
    OpenAI-compatible wrapper for many providers (OpenRouter, DeepSeek, Qwen, local LLMs).
    Allows custom base_url and api_key.
    """
    def __init__(self, name: str, api_key: str, base_url: str = "https://api.openai.com/v1"):
        super().__init__(name)
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.7
        )
        
        return response.choices[0].message.content
