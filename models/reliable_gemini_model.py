import os

import google.generativeai as genai

from models.base_model import BaseModel


class ReliableGeminiModel(BaseModel):
    """
    Gemini wrapper that actually honors the current system prompt per request.
    """

    def __init__(self, name: str = "gemini-1.5-pro"):
        super().__init__(name)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")
        genai.configure(api_key=api_key)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_prompt or None,
        )
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=4096,
                temperature=0.7,
            ),
        )
        return response.text
