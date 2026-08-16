from app.config import Settings


class GroqProvider:
    """Qwen3.6-27B on Groq.

    Qwen is a thinking model. On a live-caption path the reasoning pass is
    pure latency, so reasoning_effort="none" and reasoning_format="hidden"
    are mandatory rather than tuning knobs.
    """

    name = "groq"

    def __init__(self, client, settings: Settings) -> None:
        self._client = client
        self._s = settings

    async def complete(self, system: str, prompt: str) -> str:
        completion = await self._client.chat.completions.create(
            model=self._s.groq_model,
            max_tokens=self._s.max_tokens,
            reasoning_effort="none",
            reasoning_format="hidden",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or ""
