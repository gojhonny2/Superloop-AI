import os
from openai import OpenAI
from models.base_model import BaseModel

class OpenAIModel(BaseModel):
    """
    Wrapper for OpenAI models (GPT-4o, GPT-3.5-turbo, etc).
    Expects OPENAI_API_KEY in the environment.
    """
    def __init__(self, name: str = "gpt-4o"):
        super().__init__(name)
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables.")
            
        self.client = OpenAI(api_key=api_key)

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
            temperature=0.7 # Slight variance for creative problem solving
        )
        
        return response.choices[0].message.content
