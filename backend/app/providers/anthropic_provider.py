from app.config import Settings


class AnthropicProvider:
    """Claude Haiku (5.5 by default).

    Haiku 5.5 runs adaptive thinking when `thinking` is omitted, which would
    put a hidden reasoning pass in front of every caption. Latency is the
    point here, so thinking is turned off explicitly; `disabled` is accepted
    only at effort low/medium/high, hence the explicit `low`.

    Haiku 4.5 rejects `effort`, `thinking` and `output_config` outright, so a
    model id still on the 4.x line (ANTHROPIC_MODEL=claude-haiku-4-5) gets a
    plain messages.create with nothing else attached. Sampling parameters
    are never sent: Haiku 5.5 rejects non-default values.
    """

    name = "anthropic"

    def __init__(self, client, settings: Settings) -> None:
        self._client = client
        self._s = settings

    def _latency_params(self) -> dict:
        if self._s.anthropic_model.startswith("claude-haiku-4"):
            return {}
        return {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}

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
