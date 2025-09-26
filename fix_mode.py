import asyncio
import asyncpg

async def fix_mode():
    try:
        conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5433/commentbot')
        await conn.execute("UPDATE accounts SET mode = 'standard' WHERE id = 6")
        await conn.close()
        print("✅ Mode updated to standard for account 6")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(fix_mode())


