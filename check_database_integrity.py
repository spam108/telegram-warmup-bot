import asyncio
import logging
import os

from db import (
    close_db,
    ensure_default_warmup_settings,
    ensure_warmup_settings_from_env,
    init_db,
    validate_database_integrity,
)


async def _run_check() -> int:
    logging.basicConfig(level=logging.INFO)

    if not os.getenv("DATABASE_URL"):
        logging.error("DATABASE_URL environment variable must be set for the integrity check")
        return 1

    try:
        await init_db()
        await ensure_default_warmup_settings()
        await ensure_warmup_settings_from_env()
        if await validate_database_integrity():
            logging.info("Database integrity check passed")
            return 0
        logging.error("Database integrity validation reported missing objects")
        return 2
    except Exception as exc:  # pragma: no cover - requires postgres
        logging.exception("Database integrity check failed: %s", exc)
        return 3
    finally:
        await close_db()


def main() -> None:
    exit_code = asyncio.run(_run_check())
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
