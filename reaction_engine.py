"""Reaction management engine for channel and discussion messages."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from db import DEFAULT_REACTION_EMOJIS, DatabaseNotInitialized, _require_pool
except ImportError as e:  # pragma: no cover - defensive fallback
    logger = logging.getLogger(__name__)
    logger.error("❌ Failed to import database modules: %s", e)
    DEFAULT_REACTION_EMOJIS = ["👍", "❤️", "🔥", "👏"]

    class DatabaseNotInitialized(Exception):
        pass

    def _require_pool():
        raise DatabaseNotInitialized("Database not available")

try:  # pragma: no cover - optional dependency
    import redis.asyncio as redis_asyncio  # type: ignore
except ImportError:  # pragma: no cover - executed when redis is unavailable
    redis_asyncio = None  # type: ignore

logger = logging.getLogger(__name__)


class InMemoryCache:
    """Simple in-memory cache that mimics the subset of Redis API we need."""

    def __init__(self) -> None:
        self._data: Dict[str, tuple[float, str]] = {}
        self._max_size = 1000  # Prevent memory leaks

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._data.items() if expires_at <= now]
        for key in expired:
            self._data.pop(key, None)

    async def setex(self, key: str, seconds: int, value: str) -> None:
        self._purge()
        if len(self._data) >= self._max_size:
            oldest_key = min(self._data.keys(), key=lambda k: self._data[k][0])
            del self._data[oldest_key]
        self._data[key] = (time.monotonic() + max(1, seconds), value)

    async def get(self, key: str) -> Optional[str]:
        self._purge()
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    async def keys(self, pattern: str) -> List[str]:
        self._purge()
        return [key for key in self._data if fnmatch(key, pattern)]

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


@dataclass(slots=True)
class ReactionSettings:
    enabled: bool
    reaction_emojis: List[str]
    delay_seconds: int


class ReactionEngine:
    """High level reaction orchestration for linked channel discussions."""

    CACHE_TTL_SECONDS = 3600
    REACTION_COOLDOWN_SECONDS = 0.5

    def __init__(self) -> None:
        self.redis: Any = InMemoryCache()
        self.pool = None
        self.initialized = False
        self.reaction_settings_table = "reaction_settings"
        self.linked_posts_table = "linked_posts"
        self.post_reactions_table = "post_reactions"

    async def initialize(self) -> bool:
        """Initialise database/redis connections and ensure schema exists."""

        try:
            if self.initialized:
                return True

            try:
                self.pool = _require_pool()
            except DatabaseNotInitialized as exc:
                logger.error("❌ ReactionEngine requires the database pool: %s", exc)
                return False

            try:
                await self.ensure_reaction_tables_exist()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("❌ Failed to ensure reaction tables: %s", exc)
                return False

            self.redis = await self._init_cache()
            self.initialized = True
            logger.info("✅ ReactionEngine initialized successfully")
            return True
        except Exception as exc:
            logger.error("❌ Unexpected error in ReactionEngine.initialize(): %s", exc)
            return False

    async def _init_cache(self) -> Any:
        redis_url = os.getenv("REACTION_ENGINE_REDIS_URL") or os.getenv("REDIS_URL")
        if not redis_url or redis_asyncio is None:
            if not redis_url:
                logger.info("⚠️ Using in-memory cache for ReactionEngine (no REDIS_URL provided)")
            else:
                logger.info("⚠️ Using in-memory cache for ReactionEngine (redis library unavailable)")
            return InMemoryCache()

        try:
            redis = redis_asyncio.from_url(  # type: ignore[union-attr]
                redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await redis.ping()
            logger.info("✅ Connected to Redis for ReactionEngine")
            return redis
        except Exception as exc:  # pragma: no cover - depends on environment
            logger.warning(
                "⚠️ Falling back to in-memory cache for ReactionEngine: %s",
                exc,
            )
            return InMemoryCache()

    async def ensure_reaction_tables_exist(self) -> bool:
        if self.pool is None:
            raise DatabaseNotInitialized("ReactionEngine database pool not ready")

        self.reaction_settings_table = await self._resolve_table_name(
            "reaction_settings",
            ("channel_id", "enabled", "reaction_emojis", "delay_seconds"),
        )
        self.linked_posts_table = await self._resolve_table_name(
            "linked_posts",
            (
                "channel_id",
                "channel_message_id",
                "discussion_chat_id",
                "discussion_message_id",
            ),
        )
        self.post_reactions_table = await self._resolve_table_name(
            "post_reactions",
            (
                "channel_id",
                "channel_message_id",
                "discussion_chat_id",
                "discussion_message_id",
                "account_id",
                "emoji",
            ),
        )

        await self.create_reaction_tables()
        logger.info("✅ Reaction tables verified")
        return True

    async def _table_exists(self, table_name: str) -> bool:
        assert self.pool is not None
        query = "SELECT to_regclass($1)"
        result = await self.pool.fetchval(query, f"public.{table_name}")
        return result is not None

    async def _table_has_columns(self, table_name: str, columns: Sequence[str]) -> bool:
        assert self.pool is not None
        query = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1"
        )
        rows = await self.pool.fetch(query, table_name)
        existing = {row["column_name"] for row in rows}
        return all(column in existing for column in columns)

    async def _resolve_table_name(self, table_name: str, required_columns: Sequence[str]) -> str:
        if not await self._table_exists(table_name):
            return table_name

        if await self._table_has_columns(table_name, required_columns):
            return table_name

        fallback = f"reaction_engine_{table_name}"
        logger.warning(
            "⚠️ Existing table %s is missing required columns %s; using %s instead",
            table_name,
            ", ".join(required_columns),
            fallback,
        )
        return fallback

    async def create_reaction_tables(self) -> bool:
        assert self.pool is not None

        await self.pool.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.reaction_settings_table} (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT UNIQUE NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                reaction_emojis TEXT[],
                delay_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await self.pool.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.linked_posts_table} (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                channel_message_id BIGINT NOT NULL,
                discussion_chat_id BIGINT,
                discussion_message_id BIGINT,
                reaction_emojis TEXT[],
                delay_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, channel_message_id)
            )
            """
        )

        await self.pool.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.post_reactions_table} (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                channel_message_id BIGINT NOT NULL,
                discussion_chat_id BIGINT NOT NULL,
                discussion_message_id BIGINT NOT NULL,
                account_id BIGINT NOT NULL,
                emoji TEXT NOT NULL,
                reacted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN NOT NULL DEFAULT TRUE,
                error_message TEXT,
                UNIQUE(discussion_chat_id, discussion_message_id, account_id, emoji)
            )
            """
        )

        return True

    async def handle_linked_channel_message(self, client: Any, message: Any) -> None:
        if hasattr(message, "_reaction_engine_processed"):
            return
        message._reaction_engine_processed = True

        if not self.initialized:
            logger.warning("ReactionEngine not initialized")
            return

        channel_obj = getattr(message, "chat", None)
        channel_id = getattr(channel_obj, "id", None)
        message_id = getattr(message, "id", None)
        if channel_id is None or message_id is None:
            return

        settings = await self.get_channel_reaction_settings(channel_id)
        if not settings.enabled:
            logger.debug("Reactions disabled for channel %s", channel_id)
            return

        discussion_chat_id = await self._resolve_discussion_chat_id(client, channel_obj)

        await self.cache_channel_message(
            channel_id,
            message_id,
            discussion_chat_id,
            settings,
        )

    async def handle_discussion_message(self, client: Any, message: Any) -> None:
        if hasattr(message, "_reaction_engine_processed"):
            return
        message._reaction_engine_processed = True

        if not self.initialized:
            logger.warning("ReactionEngine not initialized")
            return

        discussion_chat = getattr(message, "chat", None)
        discussion_chat_id = getattr(discussion_chat, "id", None)
        discussion_message_id = getattr(message, "id", None)
        if discussion_chat_id is None or discussion_message_id is None:
            return

        channel_message_id = self._extract_channel_message_id(message)
        if channel_message_id is None:
            logger.debug(
                "Unable to determine linked channel message for discussion %s/%s",
                discussion_chat_id,
                discussion_message_id,
            )
            return

        linked_data = await self.find_linked_channel_message(
            None,
            channel_message_id,
            discussion_chat_id=discussion_chat_id,
        )

        channel_id = None if linked_data is None else linked_data.get("channel_id")
        if channel_id is None:
            channel_id = await self._resolve_channel_id_from_discussion(client, discussion_chat)

        if channel_id is None:
            logger.debug(
                "No channel id for discussion message %s/%s",
                discussion_chat_id,
                discussion_message_id,
            )
            return

        if linked_data is None:
            linked_data = await self.find_linked_channel_message(
                channel_id,
                channel_message_id,
                discussion_chat_id=discussion_chat_id,
            )

        settings = None
        if linked_data is not None and linked_data.get("reaction_emojis") is not None:
            settings = ReactionSettings(
                enabled=True,
                reaction_emojis=list(linked_data.get("reaction_emojis") or []),
                delay_seconds=int(linked_data.get("delay_seconds") or 0),
            )

        if settings is None:
            settings = await self.get_channel_reaction_settings(channel_id)

        if not settings.enabled:
            logger.debug("Reactions disabled for channel %s", channel_id)
            return

        if not settings.reaction_emojis:
            logger.debug("No reactions configured for channel %s", channel_id)
            return

        await self._record_discussion_message(
            channel_id,
            channel_message_id,
            discussion_chat_id,
            discussion_message_id,
            settings,
        )

        account_id = getattr(client, "reaction_engine_account_id", None)
        await self.add_reaction_emojis(
            client,
            discussion_chat_id,
            discussion_message_id,
            settings.reaction_emojis,
            settings.delay_seconds,
            account_id,
            channel_id,
            channel_message_id,
        )

    async def get_channel_reaction_settings(self, channel_id: int) -> ReactionSettings:
        assert self.pool is not None
        query = (
            f"SELECT enabled, reaction_emojis, delay_seconds "
            f"FROM {self.reaction_settings_table} WHERE channel_id = $1"
        )
        row = await self.pool.fetchrow(query, channel_id)

        if row:
            emojis = self._normalize_emoji_list(row.get("reaction_emojis"))
            delay_seconds = int(row.get("delay_seconds") or 0)
            enabled = bool(row.get("enabled"))
            return ReactionSettings(enabled=enabled, reaction_emojis=emojis, delay_seconds=delay_seconds)

        return ReactionSettings(enabled=False, reaction_emojis=list(DEFAULT_REACTION_EMOJIS), delay_seconds=0)

    async def cache_channel_message(
        self,
        channel_id: int,
        message_id: int,
        discussion_chat_id: Optional[int],
        settings: ReactionSettings,
    ) -> None:
        payload = {
            "channel_id": channel_id,
            "channel_message_id": message_id,
            "discussion_chat_id": discussion_chat_id,
            "reaction_emojis": settings.reaction_emojis,
            "delay_seconds": settings.delay_seconds,
            "timestamp": datetime.utcnow().isoformat(),
        }

        key = self._cache_key(channel_id, message_id)
        try:
            await self.redis.setex(key, self.CACHE_TTL_SECONDS, json.dumps(payload))
        except Exception as exc:  # pragma: no cover - redis specific failure
            logger.warning("Failed to cache channel message %s/%s: %s", channel_id, message_id, exc)

        await self._record_linked_post(
            channel_id,
            message_id,
            discussion_chat_id,
            settings,
        )

    async def find_linked_channel_message(
        self,
        channel_id: Optional[int],
        channel_message_id: int,
        *,
        discussion_chat_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        cache_key = None
        if channel_id is not None:
            cache_key = self._cache_key(channel_id, channel_message_id)
            try:
                cached = await self.redis.get(cache_key)
            except Exception:  # pragma: no cover - redis specific failure
                cached = None
            if cached:
                try:
                    return json.loads(cached)
                except json.JSONDecodeError:
                    logger.debug("Invalid cache payload for %s", cache_key)

        if self.pool is None:
            return None

        row = await self._get_linked_post(channel_id, channel_message_id, discussion_chat_id)
        if row is None:
            return None

        return dict(row)

    async def add_reaction_emojis(
        self,
        client: Any,
        chat_id: int,
        message_id: int,
        emojis: Iterable[str],
        delay_seconds: int,
        account_id: Optional[int],
        channel_id: int,
        channel_message_id: int,
    ) -> bool:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        any_sent = False
        emojis_list = [emoji for emoji in emojis if emoji]
        if not emojis_list:
            return False

        for emoji in emojis_list:
            try:
                if await self._has_recorded_reaction(chat_id, message_id, account_id, emoji):
                    logger.debug(
                        "Reaction %s already recorded for discussion %s/%s",
                        emoji,
                        chat_id,
                        message_id,
                    )
                    continue

                await client.send_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    emoji=emoji,
                )
                await self._record_reaction_result(
                    channel_id,
                    channel_message_id,
                    chat_id,
                    message_id,
                    account_id,
                    emoji,
                    success=True,
                    error_message=None,
                )
                logger.info(
                    "✅ Reaction added: %s to discussion %s/%s",
                    emoji,
                    chat_id,
                    message_id,
                )
                any_sent = True
                await asyncio.sleep(self.REACTION_COOLDOWN_SECONDS)
            except Exception as exc:  # pragma: no cover - depends on Telegram runtime
                logger.error("❌ Failed to add reaction %s: %s", emoji, exc)
                await self._record_reaction_result(
                    channel_id,
                    channel_message_id,
                    chat_id,
                    message_id,
                    account_id,
                    emoji,
                    success=False,
                    error_message=str(exc),
                )

        return any_sent

    async def _record_linked_post(
        self,
        channel_id: int,
        channel_message_id: int,
        discussion_chat_id: Optional[int],
        settings: ReactionSettings,
    ) -> None:
        if self.pool is None:
            return

        query = f"""
            INSERT INTO {self.linked_posts_table} (
                channel_id,
                channel_message_id,
                discussion_chat_id,
                reaction_emojis,
                delay_seconds,
                created_at,
                updated_at
            ) VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (channel_id, channel_message_id) DO UPDATE SET
                discussion_chat_id = COALESCE(EXCLUDED.discussion_chat_id, {self.linked_posts_table}.discussion_chat_id),
                reaction_emojis = EXCLUDED.reaction_emojis,
                delay_seconds = EXCLUDED.delay_seconds,
                updated_at = CURRENT_TIMESTAMP
        """
        await self.pool.execute(
            query,
            channel_id,
            channel_message_id,
            discussion_chat_id,
            settings.reaction_emojis,
            settings.delay_seconds,
        )

    async def _record_discussion_message(
        self,
        channel_id: int,
        channel_message_id: int,
        discussion_chat_id: int,
        discussion_message_id: int,
        settings: ReactionSettings,
    ) -> None:
        if self.pool is None:
            return

        query = f"""
            UPDATE {self.linked_posts_table}
            SET discussion_chat_id = COALESCE(discussion_chat_id, $3),
                discussion_message_id = $4,
                reaction_emojis = COALESCE($5, reaction_emojis),
                delay_seconds = $6,
                updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = $1 AND channel_message_id = $2
        """
        await self.pool.execute(
            query,
            channel_id,
            channel_message_id,
            discussion_chat_id,
            discussion_message_id,
            settings.reaction_emojis,
            settings.delay_seconds,
        )

    async def _has_recorded_reaction(
        self,
        discussion_chat_id: int,
        discussion_message_id: int,
        account_id: Optional[int],
        emoji: str,
    ) -> bool:
        if self.pool is None or account_id is None:
            return False

        query = (
            f"SELECT 1 FROM {self.post_reactions_table} "
            "WHERE discussion_chat_id = $1 AND discussion_message_id = $2 "
            "AND account_id = $3 AND emoji = $4"
        )
        row = await self.pool.fetchrow(
            query,
            discussion_chat_id,
            discussion_message_id,
            account_id,
            emoji,
        )
        return row is not None

    async def _record_reaction_result(
        self,
        channel_id: int,
        channel_message_id: int,
        discussion_chat_id: int,
        discussion_message_id: int,
        account_id: Optional[int],
        emoji: str,
        *,
        success: bool,
        error_message: Optional[str],
    ) -> None:
        if self.pool is None or account_id is None:
            return

        query = f"""
            INSERT INTO {self.post_reactions_table} (
                channel_id,
                channel_message_id,
                discussion_chat_id,
                discussion_message_id,
                account_id,
                emoji,
                reacted_at,
                success,
                error_message
            ) VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP, $7, $8)
            ON CONFLICT (discussion_chat_id, discussion_message_id, account_id, emoji)
            DO UPDATE SET
                reacted_at = CURRENT_TIMESTAMP,
                success = EXCLUDED.success,
                error_message = EXCLUDED.error_message
        """
        await self.pool.execute(
            query,
            channel_id,
            channel_message_id,
            discussion_chat_id,
            discussion_message_id,
            account_id,
            emoji,
            success,
            error_message,
        )

    async def _get_linked_post(
        self,
        channel_id: Optional[int],
        channel_message_id: int,
        discussion_chat_id: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        if self.pool is None:
            return None

        if channel_id is not None:
            query = (
                f"SELECT * FROM {self.linked_posts_table} "
                "WHERE channel_id = $1 AND channel_message_id = $2 "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            row = await self.pool.fetchrow(query, channel_id, channel_message_id)
            if row is not None:
                return dict(row)

        if discussion_chat_id is not None:
            query = (
                f"SELECT * FROM {self.linked_posts_table} "
                "WHERE discussion_chat_id = $1 AND channel_message_id = $2 "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            row = await self.pool.fetchrow(query, discussion_chat_id, channel_message_id)
            if row is not None:
                return dict(row)

        return None

    async def _resolve_discussion_chat_id(self, client: Any, channel_obj: Any) -> Optional[int]:
        linked_chat = getattr(channel_obj, "linked_chat", None)
        if linked_chat is not None:
            return getattr(linked_chat, "id", None)

        channel_id = getattr(channel_obj, "id", None)
        if channel_id is None:
            return None

        try:
            chat = await client.get_chat(channel_id)
        except Exception as exc:  # pragma: no cover - depends on Telegram runtime
            logger.debug("Failed to fetch linked chat for %s: %s", channel_id, exc)
            return None

        linked_chat = getattr(chat, "linked_chat", None)
        return getattr(linked_chat, "id", None)

    async def _resolve_channel_id_from_discussion(self, client: Any, discussion_chat: Any) -> Optional[int]:
        linked_chat = getattr(discussion_chat, "linked_chat", None)
        if linked_chat is not None:
            channel_id = getattr(linked_chat, "id", None)
            if channel_id is not None:
                return channel_id

        discussion_chat_id = getattr(discussion_chat, "id", None)
        if discussion_chat_id is None:
            return None

        try:
            chat = await client.get_chat(discussion_chat_id)
        except Exception as exc:  # pragma: no cover - depends on Telegram runtime
            logger.debug(
                "Failed to resolve channel id from discussion %s: %s",
                discussion_chat_id,
                exc,
            )
            return None

        linked_chat = getattr(chat, "linked_chat", None)
        return getattr(linked_chat, "id", None)

    def _extract_channel_message_id(self, message: Any) -> Optional[int]:
        direct = getattr(message, "reply_to_top_message_id", None)
        if direct is not None:
            return direct

        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            return getattr(reply, "id", None) or getattr(reply, "message_id", None)

        return getattr(message, "top_msg_id", None)

    @staticmethod
    def _normalize_emoji_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if isinstance(value, tuple):
            return [str(item) for item in value if item]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if item]
            except json.JSONDecodeError:
                return [value]
        return []

    @staticmethod
    def _cache_key(channel_id: int, message_id: int) -> str:
        return f"channel_message:{channel_id}:{message_id}"


__all__ = ["ReactionEngine", "ReactionSettings"]
