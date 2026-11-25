from app.config import Settings


class AnthropicProvider:
    """Claude Haiku (5.5 by default).

    Haiku 5.5 runs adaptive thinking when `thinking` is omitted, which would
    put a hidden reasoning pass in front of every caption. Latency is the
    point here, so thinking is turned off explicitly; `disabled` is accepted
    only at effort low/medium/high, hence the explicit `low`.

    These parameters are sent ONLY to the Haiku 5 line (an allowlist, not a
    denylist). Other Claude models disagree about them: Haiku 4.5 and
    Sonnet 4.5 reject `effort`, while Opus 5.5 and Sonnet 5.5 reject
    `thinking: disabled`. Any other ANTHROPIC_MODEL gets the plain
    messages.create that every model accepts. Sampling parameters are never
    sent: Haiku 5.5 rejects non-default values.
    """

    name = "anthropic"

    def __init__(self, client, settings: Settings) -> None:
        self._client = client
        self._s = settings

    def _latency_params(self) -> dict:
        if self._s.anthropic_model.startswith("claude-haiku-5"):
            return {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
        return {}

    async def complete(self, system: str, prompt: str) -> str:
        message = await self._client.messages.create(
            model=self._s.anthropic_model,
            max_tokens=self._s.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            **self._latency_params(),
        )
        # Read by type, never by position: a thinking-capable model can put
        # a thinking block first.
        for block in message.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""
