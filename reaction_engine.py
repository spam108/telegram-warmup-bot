"""Reaction management engine for channel and discussion messages."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

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
    reaction_chance: int = 20
    discussion_chance: int = 20
    reaction_limit: int = 1


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
        self.uses_kv_reaction_settings = False

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

        await self._determine_reaction_settings_storage()
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

    async def _get_table_columns(self, table_name: str) -> Set[str]:
        assert self.pool is not None
        query = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1"
        )
        rows = await self.pool.fetch(query, table_name)
        return {row["column_name"] for row in rows}

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

    async def _determine_reaction_settings_storage(self) -> None:
        self.reaction_settings_table = "reaction_settings"
        self.uses_kv_reaction_settings = False

        if not await self._table_exists(self.reaction_settings_table):
            return

        columns = await self._get_table_columns(self.reaction_settings_table)
        kv_columns = {"setting_key", "setting_value"}
        structured_columns = {"channel_id", "enabled", "reaction_emojis", "delay_seconds"}

        if kv_columns.issubset(columns):
            self.uses_kv_reaction_settings = True
            logger.info("✅ reaction_settings table detected as key-value storage")
            return

        if structured_columns.issubset(columns):
            return

        fallback = "reaction_engine_reaction_settings"
        self.reaction_settings_table = fallback

        if not await self._table_exists(fallback):
            return

        fallback_columns = await self._get_table_columns(fallback)
        if kv_columns.issubset(fallback_columns):
            self.uses_kv_reaction_settings = True

    async def create_reaction_tables(self) -> bool:
        assert self.pool is not None

        if not self.uses_kv_reaction_settings:
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

        account_id = getattr(client, "reaction_engine_account_id", None)

        settings = await self._build_reaction_settings(channel_id, message_id, account_id)
        if not settings.enabled:
            logger.debug("Reactions disabled for channel %s", channel_id)
            return

        if random.randint(1, 100) > settings.reaction_chance:
            logger.info(
                "🎲 Skip caching reactions for %s/%s: roll exceeded chance %s",
                channel_id,
                message_id,
                settings.reaction_chance,
            )
            return

        logger.info(
            "📊 Reaction settings for %s/%s: emojis=%s, limit=%s, chance=%s",
            channel_id,
            message_id,
            settings.reaction_emojis,
            settings.reaction_limit,
            settings.reaction_chance,
        )

        discussion_chat_id = await self._resolve_discussion_chat_id(client, channel_obj)

        await self.cache_channel_message(
            channel_id,
            message_id,
            discussion_chat_id,
            settings,
            account_id=account_id,
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

        account_id = getattr(client, "reaction_engine_account_id", None)
        settings = await self._build_reaction_settings(
            channel_id,
            channel_message_id,
            account_id,
            linked_data=linked_data,
        )

        if not settings.enabled:
            logger.debug("Reactions disabled for channel %s", channel_id)
            return

        if not settings.reaction_emojis:
            logger.debug("No reactions configured for channel %s", channel_id)
            return

        if random.randint(1, 100) > settings.discussion_chance:
            logger.info(
                "🎲 Skip discussion reaction for %s/%s: roll exceeded chance %s",
                discussion_chat_id,
                discussion_message_id,
                settings.discussion_chance,
            )
            return

        logger.info(
            "🎯 Applying reactions: %s to %s/%s",
            settings.reaction_emojis,
            discussion_chat_id,
            discussion_message_id,
        )

        await self._record_discussion_message(
            channel_id,
            channel_message_id,
            discussion_chat_id,
            discussion_message_id,
            settings,
        )

        success = await self.add_reaction_emojis(
            client,
            discussion_chat_id,
            discussion_message_id,
            settings.reaction_emojis,
            settings.delay_seconds,
            account_id,
            channel_id,
            channel_message_id,
        )

        if success:
            logger.info("✅ Reactions applied successfully: %s", settings.reaction_emojis)
        else:
            logger.error("❌ Failed to apply reactions: %s", settings.reaction_emojis)

    async def get_channel_reaction_settings(self, channel_id: int) -> ReactionSettings:
        assert self.pool is not None

        if self.uses_kv_reaction_settings:
            return await self._get_channel_reaction_settings_kv(channel_id)

        query = (
            f"SELECT enabled, reaction_emojis, delay_seconds "
            f"FROM {self.reaction_settings_table} WHERE channel_id = $1"
        )
        row = await self.pool.fetchrow(query, channel_id)

        if row:
            emojis = self._normalize_emoji_list(row.get("reaction_emojis"))
            if not emojis:
                emojis = list(DEFAULT_REACTION_EMOJIS)
            delay_seconds = int(row.get("delay_seconds") or 0)
            enabled = bool(row.get("enabled", True))
            return ReactionSettings(enabled=enabled, reaction_emojis=emojis, delay_seconds=delay_seconds)

        return ReactionSettings(enabled=True, reaction_emojis=list(DEFAULT_REACTION_EMOJIS), delay_seconds=0)

    async def _get_channel_reaction_settings_kv(self, channel_id: int) -> ReactionSettings:
        assert self.pool is not None

        keys_to_try = [f"reactions_channel_{channel_id}", "reactions_global_settings"]
        query = (
            f"SELECT setting_value FROM {self.reaction_settings_table} "
            "WHERE setting_key = $1 ORDER BY updated_at DESC NULLS LAST, id DESC LIMIT 1"
        )

        for setting_key in keys_to_try:
            row = await self.pool.fetchrow(query, setting_key)
            if not row:
                continue

            raw_value = row.get("setting_value")
            settings_dict = self._parse_reaction_settings_value(raw_value, setting_key)
            if settings_dict is None:
                continue

            if setting_key == keys_to_try[0]:
                logger.info(
                    "✅ Found reaction settings for channel %s via key %s",
                    channel_id,
                    setting_key,
                )
            else:
                logger.info("⚠️ Using global reaction settings for channel %s", channel_id)

            return self._reaction_settings_from_dict(settings_dict)

        logger.info("⚠️ Using default reaction settings for channel %s", channel_id)
        return ReactionSettings(enabled=True, reaction_emojis=list(DEFAULT_REACTION_EMOJIS), delay_seconds=0)

    def _parse_reaction_settings_value(
        self,
        raw_value: Any,
        setting_key: str,
    ) -> Optional[Dict[str, Any]]:
        if raw_value is None:
            return None

        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                logger.error("❌ Invalid JSON in reaction settings for key %s", setting_key)
                return None
        elif isinstance(raw_value, dict):
            parsed = raw_value
        else:
            logger.debug(
                "Unsupported type %s for reaction settings key %s", type(raw_value).__name__, setting_key
            )
            return None

        if isinstance(parsed, dict):
            return parsed

        logger.debug("Reaction settings value for key %s is not a dict", setting_key)
        return None

    def _reaction_settings_from_dict(self, settings_dict: Dict[str, Any]) -> ReactionSettings:
        enabled = bool(settings_dict.get("enabled", True))
        emojis = self._normalize_emoji_list(settings_dict.get("reaction_emojis"))
        if not emojis:
            emojis = list(DEFAULT_REACTION_EMOJIS)

        delay_value = settings_dict.get("delay_seconds", 0)
        try:
            delay_seconds = int(delay_value)
        except (TypeError, ValueError):
            logger.debug("Invalid delay_seconds value %r in reaction settings", delay_value)
            delay_seconds = 0

        return ReactionSettings(enabled=enabled, reaction_emojis=emojis, delay_seconds=delay_seconds)

    async def _get_account_reaction_settings(self, account_id: Optional[int]) -> Dict[str, Any]:
        if self.pool is None or account_id is None:
            return {}

        query = """
            SELECT
                reactions_enabled,
                reaction_emojis,
                reaction_chance,
                reaction_discussion_chance,
                reaction_limit_per_message
            FROM accounts
            WHERE id = $1
        """

        row = await self.pool.fetchrow(query, account_id)
        if row is None:
            return {}

        data = dict(row)
        data["reactions_enabled"] = bool(data.get("reactions_enabled", True))
        data["reaction_emojis"] = self._normalize_emoji_list(data.get("reaction_emojis"))
        data["reaction_chance"] = self._coerce_int(data.get("reaction_chance"), 20)
        data["reaction_discussion_chance"] = self._coerce_int(
            data.get("reaction_discussion_chance"), data["reaction_chance"]
        )
        default_limit = len(data["reaction_emojis"]) or 1
        data["reaction_limit_per_message"] = self._coerce_int(
            data.get("reaction_limit_per_message"), default_limit
        )
        if data["reaction_limit_per_message"] <= 0:
            data["reaction_limit_per_message"] = default_limit

        return data

    async def _build_reaction_settings(
        self,
        channel_id: int,
        channel_message_id: Optional[int],
        account_id: Optional[int],
        *,
        linked_data: Optional[Dict[str, Any]] = None,
    ) -> ReactionSettings:
        if linked_data and account_id is not None:
            cached_account = linked_data.get("account_id")
            if cached_account is not None and cached_account != account_id:
                linked_data = None

        base_settings = await self.get_channel_reaction_settings(channel_id)
        reaction_emojis = list(base_settings.reaction_emojis)
        delay_seconds = base_settings.delay_seconds

        post_data: Optional[Dict[str, Any]] = None
        if linked_data is not None:
            post_data = linked_data
        elif channel_message_id is not None:
            post_data = await self._get_linked_post(channel_id, channel_message_id, None)

        if post_data:
            post_emojis = self._normalize_emoji_list(post_data.get("reaction_emojis"))
            if post_emojis:
                reaction_emojis = post_emojis
            delay_seconds = self._coerce_int(post_data.get("delay_seconds"), delay_seconds)

        account_settings = await self._get_account_reaction_settings(account_id)
        reactions_enabled = account_settings.get("reactions_enabled", True)

        if not reaction_emojis:
            account_emojis = account_settings.get("reaction_emojis") or []
            if account_emojis:
                reaction_emojis = [str(emoji) for emoji in account_emojis if emoji]

        if not reaction_emojis:
            reaction_emojis = list(DEFAULT_REACTION_EMOJIS)

        reaction_chance = account_settings.get("reaction_chance", 20)
        discussion_chance = account_settings.get("reaction_discussion_chance", reaction_chance)
        limit_default = len(reaction_emojis) or 1
        reaction_limit = account_settings.get("reaction_limit_per_message", limit_default)
        if reaction_limit <= 0:
            reaction_limit = limit_default
        if reaction_limit and reaction_emojis:
            reaction_emojis = reaction_emojis[:reaction_limit]
            reaction_limit = len(reaction_emojis)

        enabled = bool(base_settings.enabled and reactions_enabled)

        return ReactionSettings(
            enabled=enabled,
            reaction_emojis=reaction_emojis,
            delay_seconds=delay_seconds,
            reaction_chance=reaction_chance,
            discussion_chance=discussion_chance,
            reaction_limit=reaction_limit,
        )

    async def cache_channel_message(
        self,
        channel_id: int,
        message_id: int,
        discussion_chat_id: Optional[int],
        settings: ReactionSettings,
        *,
        account_id: Optional[int] = None,
    ) -> None:
        payload = {
            "channel_id": channel_id,
            "channel_message_id": message_id,
            "discussion_chat_id": discussion_chat_id,
            "reaction_emojis": settings.reaction_emojis,
            "delay_seconds": settings.delay_seconds,
            "reaction_chance": settings.reaction_chance,
            "reaction_discussion_chance": settings.discussion_chance,
            "reaction_limit": settings.reaction_limit,
            "account_id": account_id,
            "enabled": settings.enabled,
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
                await self._log_reaction_event(
                    account_id,
                    channel_id,
                    message_id,
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
                await self._log_reaction_event(
                    account_id,
                    channel_id,
                    message_id,
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

    async def _log_reaction_event(
        self,
        account_id: Optional[int],
        channel_id: int,
        message_id: int,
        emoji: str,
        *,
        success: bool,
        error_message: Optional[str],
    ) -> None:
        if self.pool is None or account_id is None:
            return

        status = "success" if success else "error"
        query = """
            INSERT INTO reaction_logs (
                account_id,
                channel,
                message_id,
                emoji,
                status,
                error_message,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
        """

        await self.pool.execute(
            query,
            account_id,
            str(channel_id),
            message_id,
            emoji,
            status,
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
    def _coerce_int(value: Any, default: int) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

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
