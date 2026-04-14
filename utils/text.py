def chunk_text(text: str, limit: int = 1900, max_chunks: int = 6) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(No response)"]

    chunks = []
    remaining = text

    while len(remaining) > limit and len(chunks) < max_chunks - 1:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


def build_choice_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def miles_to_meters(miles: float) -> int:
    return int(miles * 1609.34)