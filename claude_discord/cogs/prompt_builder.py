"""Build a prompt string and collect image URLs from a Discord message.

Extracted from ClaudeChatCog to keep the Cog thin.  This module is a
pure function layer — it only depends on ``discord.Message`` and has no
Cog or Bot state.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile

import aiohttp
import discord

logger = logging.getLogger(__name__)

# Attachment filtering constants
ALLOWED_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
)
IMAGE_MIME_PREFIXES = ("image/",)

# File extensions treated as text when content_type is absent.
# Discord converts long pasted text to "message.txt" without a content_type.
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".log",
        ".sh",
        ".bash",
        ".zsh",
        ".html",
        ".css",
        ".xml",
        ".rst",
        ".sql",
        ".graphql",
        ".tf",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".cs",
        ".rb",
        ".php",
    }
)

# Image file extensions used as fallback when content_type is absent.
_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".svg",
    }
)
# Image storage directory
IMAGE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "ccdb_images")

# Ensure cache directory exists
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

MAX_ATTACHMENT_BYTES = (
    200_000  # 200 KB per file — Discord auto-converted messages can exceed 100 KB
)
MAX_IMAGE_BYTES = 5_000_000  # 5 MB per image
MAX_TOTAL_BYTES = 500_000  # 500 KB across all text attachments
MAX_ATTACHMENTS = 5
MAX_IMAGES = 4  # Claude supports up to 4 images per prompt


# Keywords that indicate the user wants a file sent/attached.
_SEND_FILE_KEYWORDS = (
    "送って",
    "ちょうだい",
    "添付して",
    "くれ",
    "送ってください",
    "ください",
    "attach",
    "send me",
    "send the file",
    "give me",
    "download",
)


def wants_file_attachment(prompt: str) -> bool:
    """Return True if *prompt* contains a file-send/attach request.

    Used to enable the ``.ccdb-attachments`` delivery mechanism for the
    session — Claude is instructed to write the paths it wants to send,
    and the bot attaches them when the session completes.
    """
    lower = prompt.lower()
    return any(kw in lower for kw in _SEND_FILE_KEYWORDS)


# Image data structure for Claude API
# type: "url" for web URLs, "base64" for local files
ImageSource = dict[str, str]


async def download_discord_attachment(attachment: discord.Attachment) -> ImageSource | None:
    """Download a Discord attachment and return as base64 image source.

    Discord CDN URLs require authentication and cannot be accessed by Claude API.
    This function downloads the image and converts it to base64 format.

    Returns:
        ImageSource dict with type "base64", or None if download fails.
    """
    try:
        # Download the image
        image_data = await attachment.read()

        # Determine media type from content_type or filename
        content_type = attachment.content_type or ""
        if not content_type:
            ext = os.path.splitext(attachment.filename.lower())[1]
            ext_to_mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
            }
            content_type = ext_to_mime.get(ext, "image/jpeg")

        # Encode as base64
        b64_data = base64.b64encode(image_data).decode("utf-8")

        return {
            "type": "base64",
            "media_type": content_type,
            "data": b64_data,
        }
    except Exception as e:
        logger.debug("Failed to download attachment %s: %s", attachment.filename, e)
        return None


async def build_prompt_and_images(message: discord.Message) -> tuple[str, list[ImageSource]]:
    """Build the prompt string and collect image attachment data.

    Text attachments (text/*, application/json, application/xml) are appended
    inline to the prompt.  Image attachments (image/*) are downloaded and
    converted to base64 for Claude API compatibility.

    Supports three image sources:
    1. message.attachments - uploaded/pasted images (downloaded to base64)
    2. message.embeds[].image - embed images (link previews, kept as URL)
    3. message.embeds[].thumbnail - embed thumbnails (kept as URL)

    Discord CDN URLs require authentication and cannot be accessed by Claude API,
    so uploaded images are downloaded and converted to base64.

    Both binary-file types that exceed size limits and unsupported types are
    silently skipped — never raise an error to the user.

    Returns:
        (prompt_text, image_sources) — List of ImageSource dicts for stream-json.
    """
    prompt = message.content or ""
    if not message.attachments and not message.embeds:
        return prompt, []

    total_bytes = 0
    sections: list[str] = []
    image_sources: list[ImageSource] = []

    # ---- Collect images from message.attachments (uploaded/pasted) ----
    for attachment in message.attachments[:MAX_ATTACHMENTS]:
        content_type = attachment.content_type or ""

        # When Discord auto-converts a long pasted message to a file, the
        # content_type may be absent.  Fall back to extension-based detection.
        if not content_type:
            ext = os.path.splitext(attachment.filename.lower())[1]
            if ext in _IMAGE_EXTENSIONS:
                content_type = "image/png"  # triggers CDN URL path below
            elif ext in _TEXT_EXTENSIONS:
                content_type = "text/plain"

        # ---- Image attachments → download and convert to base64 ----
        # Discord CDN URLs require auth, so we must download the image
        if content_type.startswith(IMAGE_MIME_PREFIXES):
            if len(image_sources) >= MAX_IMAGES:
                logger.debug("Skipping image %s: max images reached", attachment.filename)
                continue
            if attachment.size > MAX_IMAGE_BYTES:
                logger.debug(
                    "Skipping image %s: too large (%d bytes)",
                    attachment.filename,
                    attachment.size,
                )
                continue

            # Download Discord attachment and convert to base64
            img_source = await download_discord_attachment(attachment)
            if img_source:
                image_sources.append(img_source)
                logger.debug("Downloaded and converted image to base64: %s", attachment.filename)
            continue

        # ---- Text attachments → inline in prompt ----
        if not content_type.startswith(ALLOWED_MIME_PREFIXES):
            logger.debug(
                "Skipping attachment %s: unsupported type %s",
                attachment.filename,
                content_type,
            )
            continue
        total_bytes += min(attachment.size, MAX_ATTACHMENT_BYTES)
        if total_bytes > MAX_TOTAL_BYTES:
            logger.debug("Stopping attachment processing: total size exceeded")
            break
        try:
            data = await attachment.read()
            text = data.decode("utf-8", errors="replace")
            if len(text) > MAX_ATTACHMENT_BYTES:
                truncated_chars = MAX_ATTACHMENT_BYTES
                notice = (
                    f"\n... [truncated: showing first {truncated_chars // 1000}KB"
                    f" of {len(text) // 1000}KB]"
                )
                text = text[:truncated_chars] + notice
                logger.debug(
                    "Truncated attachment %s from %d to %d chars",
                    attachment.filename,
                    len(data),
                    truncated_chars,
                )
            sections.append(f"\n\n--- Attached file: {attachment.filename} ---\n{text}")
        except Exception:
            logger.debug("Failed to read attachment %s", attachment.filename, exc_info=True)
            continue

    # ---- Collect images from message.embeds (link previews, thumbnails) ----
    # These are public URLs, keep as-is
    for embed in message.embeds:
        if len(image_sources) >= MAX_IMAGES:
            break

        # Check embed.image (link preview)
        if embed.image and embed.image.url:
            image_sources.append({
                "type": "url",
                "url": embed.image.url,
            })
            logger.debug("Collected embed image URL: %.80s", embed.image.url)
            continue

        # Check embed.thumbnail
        if embed.thumbnail and embed.thumbnail.url:
            image_sources.append({
                "type": "url",
                "url": embed.thumbnail.url,
            })
            logger.debug("Collected embed thumbnail URL: %.80s", embed.thumbnail.url)
            continue

    return prompt + "".join(sections), image_sources
