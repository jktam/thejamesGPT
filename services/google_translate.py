from __future__ import annotations

from services.http_service import post_json


async def translate_text(
    bot,
    text: str,
    target_language: str,
    source_language: str | None = None,
) -> str:
    if not bot.settings.google_api_key:
        raise RuntimeError("GOOGLE_GEO_PLACES_API_KEY is not configured")

    target_language = target_language.strip().lower()
    if source_language:
        source_language = source_language.strip().lower()

    data: dict[str, str] = {
        "q": text,
        "target": target_language,
        "key": bot.settings.google_api_key,
        "format": "text",
    }

    if source_language:
        data["source"] = source_language

    payload = await post_json(
        bot,
        "https://translation.googleapis.com/language/translate/v2",
        data=data,
    )

    data_block = payload.get("data", {})
    translations = data_block.get("translations", [])
    if not translations:
        raise RuntimeError("Translation returned no result")

    translated = translations[0].get("translatedText")
    if not translated:
        raise RuntimeError("Translation returned empty text")

    detected = translations[0].get("detectedSourceLanguage")
    if detected:
        return f"Detected source: `{detected}`\nTarget: `{target_language}`\n\n{translated}"

    return f"Target: `{target_language}`\n\n{translated}"