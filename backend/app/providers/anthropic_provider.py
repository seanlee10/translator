from app.config import Settings


class AnthropicProvider:
    """Claude Haiku 4.5.

    Haiku 4.5 rejects the `effort` parameter and takes no `thinking` config,
    so this is a plain messages.create with nothing else attached.
    """

    name = "anthropic"

    def __init__(self, client, settings: Settings) -> None:
        self._client = client
        self._s = settings

    async def complete(self, system: str, prompt: str) -> str:
        message = await self._client.messages.create(
            model=self._s.anthropic_model,
            max_tokens=self._s.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in message.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""
