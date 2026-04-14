import logging
import os
from openai import OpenAI
from models.base_model import BaseModel, get_model_timeout_seconds


class UniversalModel(BaseModel):
    """
    OpenAI-compatible wrapper for many providers (OpenRouter, DeepSeek, Qwen, local LLMs).
    Allows custom base_url and api_key.
    """
    def __init__(self, name: str, api_key: str, base_url: str = "https://api.openai.com/v1", max_tokens: int | None = None):
        super().__init__(name, max_tokens=max_tokens)
        self.timeout = get_model_timeout_seconds()
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
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

        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None) if choice else None
        content = getattr(message, "content", None) if message else None

        if not content or not str(content).strip():
            # Reasoning models (GLM, DeepSeek-R1, etc.) sometimes put the final
            # answer into `reasoning` or `reasoning_content` when the chat
            # wrapper drops it out of `content`. Fall back to those before
            # reporting the response as empty.
            for attr in ("reasoning_content", "reasoning"):
                fallback = getattr(message, attr, None) if message else None
                if fallback and str(fallback).strip():
                    return str(fallback)
            finish_reason = getattr(choice, "finish_reason", "unknown") if choice else "unknown"
            err = getattr(response, "error", None)
            logging.warning(
                "UniversalModel empty content: model=%s finish_reason=%s error=%s",
                self.model_name, finish_reason, err,
            )
            raise RuntimeError(
                f"Provider returned empty content (finish_reason={finish_reason}). "
                "Common causes: reasoning tokens filled the output budget, content "
                "filter triggered, or the upstream route is misconfigured. "
                "Try raising max_tokens, switching provider route, or using a different model."
            )

        return content
