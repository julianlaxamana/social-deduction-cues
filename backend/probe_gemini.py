"""Check Gemini model access with a synthetic game claim."""

import asyncio
import os

from google import genai

from gemini_judgment import DEFAULT_MODEL, judge_play


async def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is missing from the backend environment.")
        return 1

    model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    try:
        async with asyncio.timeout(15):
            async with genai.Client(api_key=key, vertexai=False).aio as client:
                models = await client.models.list()
                supported = {
                    item.name.removeprefix("models/")
                    async for item in models
                    if item.name and "generateContent" in (item.supported_actions or [])
                }
                print("generateContent models:", ", ".join(sorted(supported)))
                if model not in supported:
                    print(f"{model} is unavailable. Set GEMINI_MODEL to an available Flash model.")
                    return 1
                result = await judge_play(
                    table_rank="Q", card_count=2,
                    transcript="Two queens.", speech_features={}, client=client,
                )
    except Exception as error:
        print(f"Gemini model discovery failed ({type(error).__name__}).")
        return 1

    if result.explanation == "Gemini could not provide a judgment.":
        print(f"{model} failed the generation probe. See the diagnostic above.")
        return 1
    print(f"{model} returned valid structured output: {result.judgment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
