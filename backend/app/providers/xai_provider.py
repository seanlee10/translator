from app.config import Settings


class XAIProvider:
    """Grok 4.20 non-reasoning by default; grok-4.3 also supported.

    The default is a `*-non-reasoning` model id, which has no reasoning mode
    at all, so nothing reasoning-related is sent to it. Benchmarked on en>zh
    it was faster than grok-4.3 with reasoning off (p50 820 vs 909 ms) and
    scored no worse, so it stays the default.

    grok-4.3 (XAI_MODEL=grok-4.3) reasons by default (measured: ~8 s and
    ~600 reasoning tokens for one caption line), so `reasoning_effort="none"`
    is mandatory for the latency budget, the same way it is for Groq/Qwen.
    It is also what xAI now serves behind the retired
    `grok-4-*-fast-non-reasoning` ids. It is the only model that gets the
    parameter: grok-4.5/4.6/4.7 cannot disable reasoning, and any other id
    gets the plain request.

    `reasoning_format` is Groq-only and is never sent to xAI.
    """

    name = "xai"

    def __init__(self, client, settings: Settings) -> None:
        self._client = client
        self._s = settings

    async def complete(self, system: str, prompt: str) -> str:
        # Allowlist, not "anything without non-reasoning in its name":
        # grok-4.5/4.6/4.7 cannot disable reasoning and may reject "none",
        # so an unknown id gets the plain request instead of a guess.
        extra = (
            {"reasoning_effort": "none"}
            if self._s.xai_model.startswith("grok-4.3")
            else {}
        )
        completion = await self._client.chat.completions.create(
            model=self._s.xai_model,
            **extra,
            max_completion_tokens=self._s.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or ""
