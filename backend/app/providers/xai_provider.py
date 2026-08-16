from app.config import Settings


class XAIProvider:
    """Grok 4.20, non-reasoning variant.

    Built for speed-critical requests: unlike the Groq/Qwen provider, there
    is no reasoning pass to disable, so reasoning_effort/reasoning_format
    must never be sent — this model doesn't have a reasoning mode to turn
    off, and Groq-specific parameters would be a bug here, not a no-op.
    """

    name = "xai"

    def __init__(self, client, settings: Settings) -> None:
        self._client = client
        self._s = settings

    async def complete(self, system: str, prompt: str) -> str:
        completion = await self._client.chat.completions.create(
            model=self._s.xai_model,
            max_completion_tokens=self._s.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or ""
