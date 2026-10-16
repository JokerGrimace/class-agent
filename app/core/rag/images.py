"""Image metadata helpers used by RAG ingestion and retrieval."""

from __future__ import annotations

import json
from urllib.parse import urljoin


def build_image(alt: str, path: str, metadata=None) -> dict:
    """Build a small, vector-store-safe image record."""
    path = str(path or "").strip()
    image = {
        "alt": str(alt or "").strip() or "相关图片",
        "path": path,
    }
    base_url = (metadata or {}).get("imageBaseUrl") or (metadata or {}).get("image_base_url")
    if path.startswith(("http://", "https://", "data:", "/")):
        image["url"] = path
    elif base_url:
        image["url"] = urljoin(str(base_url).rstrip("/") + "/", path)
    return image


def serialize_images(images) -> str:
    """Store image records as JSON because all supported stores accept strings."""
    return json.dumps(list(images or []), ensure_ascii=False, separators=(",", ":"))


def images_from_metadata(metadata) -> list[dict]:
    """Read current image metadata and the legacy ``relation_img`` field."""
    metadata = metadata or {}
    value = metadata.get("images") or metadata.get("related_images")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = [{"path": value}]
    elif isinstance(value, dict):
        value = [value]

    if not value and metadata.get("relation_img"):
        value = [{"path": metadata["relation_img"]}]

    normalized = []
    seen = set()
    for item in value or []:
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        reference = str(item.get("url") or item.get("path") or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        image = {
            "alt": str(item.get("alt") or "相关图片"),
            "path": str(item.get("path") or reference),
        }
        if item.get("url"):
            image["url"] = str(item["url"])
        normalized.append(image)
    return normalized


def append_images_to_content(content: str, metadata) -> str:
    """Append retrievable images to the text context sent to the model."""
    images = images_from_metadata(metadata)
    if not images:
        return str(content or "")
    markdown = [
        f"![{image['alt']}]({image.get('url') or image['path']})"
        for image in images
    ]
    return (
        f"{str(content or '').rstrip()}\n\n"
        "相关图片（回答与该内容相关时，请保留下列 Markdown 图片）：\n"
        + "\n".join(markdown)
    )
