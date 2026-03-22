"""Repository for storing expand/collapse content for Discord messages."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class ExpandableContentRepository:
    """Stores expand/collapse content for Discord message views.

    This enables persistent expand/collapse functionality even after bot restarts.
    """

    def __init__(self, db_path: str = "data/sessions.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        """Initialize the database table."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS expandable_content (
                message_id INTEGER PRIMARY KEY,
                content_type TEXT NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        await self._db.commit()
        logger.info("ExpandableContentRepository initialized")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()

    async def store(
        self,
        message_id: int,
        content_type: str,
        content: str,
        title: str | None = None,
    ) -> None:
        """Store content for a message."""
        if not self._db:
            return
        await self._db.execute(
            """
            INSERT OR REPLACE INTO expandable_content (message_id, content_type, title, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, content_type, title, content, int(time.time())),
        )
        await self._db.commit()

    async def get(self, message_id: int) -> dict[str, Any] | None:
        """Retrieve content for a message."""
        if not self._db:
            return None
        async with self._db.execute(
            """
            SELECT content_type, title, content FROM expandable_content
            WHERE message_id = ?
            """,
            (message_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "type": row[0],
                    "title": row[1],
                    "content": row[2],
                }
        return None

    async def delete(self, message_id: int) -> None:
        """Delete content for a message."""
        if not self._db:
            return
        await self._db.execute(
            "DELETE FROM expandable_content WHERE message_id = ?",
            (message_id,),
        )
        await self._db.commit()

    async def cleanup_old(self, max_age_seconds: int = 86400) -> int:
        """Delete content older than max_age_seconds. Returns count of deleted rows."""
        if not self._db:
            return 0
        cutoff = int(time.time()) - max_age_seconds
        cursor = await self._db.execute(
            "DELETE FROM expandable_content WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount
