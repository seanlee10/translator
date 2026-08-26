"""One-off: confirm the SDK's handler style and message shape.

Run with a real DEEPGRAM_API_KEY. Prints the first few raw messages.
"""
import asyncio
import json
import math
import struct

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from app.config import get_settings
from app.deepgram_client import connect_params, message_to_dict
from app.direction import EN_TO_KO

SAMPLE_RATE = 16000


def tone(seconds: float) -> bytes:
    """A 440 Hz sine. Deepgram will transcribe nothing, but Results still flow."""
    frames = int(SAMPLE_RATE * seconds)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
        for i in range(frames)
    )


async def main() -> None:
    settings = get_settings()
    client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)
    seen = 0

    async with client.listen.v1.connect(
        **connect_params(settings, SAMPLE_RATE, EN_TO_KO)
    ) as connection:

        async def on_message(message):
            nonlocal seen
            seen += 1
            if seen <= 3:
                print(f"--- message {seen} ({type(message).__name__}) ---")
                print(json.dumps(message_to_dict(message), indent=2, default=str)[:1200])

        connection.on(EventType.MESSAGE, on_message)
        listener = asyncio.create_task(connection.start_listening())

        audio = tone(2.0)
        for i in range(0, len(audio), 3200):
            await connection.send_media(audio[i : i + 3200])
            await asyncio.sleep(0.1)

        await asyncio.sleep(2)
        listener.cancel()

    print(f"\nTotal messages: {seen}")
    if seen == 0:
        print("NO MESSAGES — the handler was not invoked. Check handler style.")


if __name__ == "__main__":
    asyncio.run(main())
