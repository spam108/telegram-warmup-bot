import asyncio
import os

from db import init_db, set_account_mode, close_db


async def fix_mode(account_id: int = 6) -> None:
    try:
        if not os.getenv("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL environment variable is not set")

        await init_db()
        await set_account_mode(account_id, "standard")
        await close_db()
        print(f"✅ Mode updated to standard for account {account_id}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(fix_mode())


