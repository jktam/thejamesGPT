from __future__ import annotations


async def read_bounded(stream, limit: int) -> bytes:
    """Read through EOF without letting a remote response exceed the byte budget."""
    chunks = []
    size = 0
    while True:
        chunk = await stream.read(min(65536, limit + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise ValueError("Response exceeded size limit")
        chunks.append(chunk)
