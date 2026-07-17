import asyncio
import logging
from datetime import datetime

from .config import settings
from .database import get_db

logger = logging.getLogger("tempmail.cleanup")


async def purge_expired() -> int:
    db = await get_db()
    now = datetime.utcnow()
    
    # Find all expired inboxes
    expired_inboxes = await db.inboxes.find({"expires_at": {"$lt": now}}).to_list(length=None)
    if not expired_inboxes:
        return 0
        
    expired_ids = [inbox["_id"] for inbox in expired_inboxes]
    
    # Delete the inboxes
    inbox_res = await db.inboxes.delete_many({"_id": {"$in": expired_ids}})
    
    # Delete messages associated with these inboxes
    await db.messages.delete_many({"inbox_id": {"$in": expired_ids}})
    
    return inbox_res.deleted_count


async def cleanup_loop():
    while True:
        try:
            removed = await purge_expired()
            if removed:
                logger.info(f"Purged {removed} expired inbox(es)")
        except Exception:
            logger.exception("Cleanup sweep failed")
        await asyncio.sleep(settings.CLEANUP_INTERVAL_SECONDS)
