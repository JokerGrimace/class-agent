import re
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.config import settings


def extract_text_from_html(html_text: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    title = unescape(title_match.group(1).strip()) if title_match else ""

    cleaned = re.sub(r"<script.*?>.*?</script>", " ", html_text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<noscript.*?>.*?</noscript>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return title, cleaned


async def fetch_web_page(url: str, max_chars: Optional[int] = None) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are supported")

    async with httpx.AsyncClient(
        timeout=settings.web_fetch_timeout_seconds,
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    title, text = extract_text_from_html(response.text)
    limit = max_chars or settings.web_fetch_max_chars
    truncated = len(text) > limit
    if truncated:
        text = text[:limit]

    final_url = str(response.url)
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "content": text,
        "truncated": truncated,
        "citations": [{"title": title or final_url, "url": final_url}],
    }
