import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from .config import settings

# Cache MongoDB clients per event loop to prevent "attached to a different loop" errors
_clients = {}

async def get_db():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    if loop not in _clients:
        _clients[loop] = AsyncIOMotorClient(settings.MONGO_URL)
    
    return _clients[loop][settings.MONGO_DB]

async def init_db():
    db = await get_db()
    # Create indexes to ensure uniqueness and fast lookups
    await db.inboxes.create_index("address", unique=True)
    await db.inboxes.create_index("expires_at")
    await db.messages.create_index("inbox_id")
    await db.messages.create_index("attachments.id")
    await db.messages.create_index("expires_at")
