import asyncio
import random
import string
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import logging

logger = logging.getLogger("tempmail.api")

from .config import settings
from .database import get_db, init_db
from .schemas import InboxOut, MessageSummaryOut, MessageDetailOut
from .cleanup import cleanup_loop
from .middleware.gateway_auth import GatewayAuthMiddleware
from .utils.log_helper import CentralLoggerMiddleware


def _random_local_part(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=length))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB (create indexes) asynchronously
    await init_db()
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="Temp Mail API", lifespan=lifespan)

app.add_middleware(GatewayAuthMiddleware)
app.add_middleware(CentralLoggerMiddleware, service_name="temp-mail")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


from typing import Dict, Set

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, address: str, websocket: WebSocket):
        await websocket.accept()
        addr_lower = address.lower()
        if addr_lower not in self.active_connections:
            self.active_connections[addr_lower] = set()
        self.active_connections[addr_lower].add(websocket)
        logger.info(f"WebSocket client connected to inbox: {addr_lower}")

    def disconnect(self, address: str, websocket: WebSocket):
        addr_lower = address.lower()
        if addr_lower in self.active_connections:
            self.active_connections[addr_lower].discard(websocket)
            if not self.active_connections[addr_lower]:
                del self.active_connections[addr_lower]
        logger.info(f"WebSocket client disconnected from inbox: {addr_lower}")

    async def broadcast_to_inbox(self, address: str, message: dict):
        addr_lower = address.lower()
        if addr_lower in self.active_connections:
            websockets = list(self.active_connections[addr_lower])
            for websocket in websockets:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending message to WebSocket: {e}")
                    self.disconnect(addr_lower, websocket)

manager = ConnectionManager()


@app.post("/api/internal/new-message")
async def internal_new_message(address: str, message_summary: dict):
    await manager.broadcast_to_inbox(address, message_summary)
    return {"status": "ok"}


@app.websocket("/api/inbox/{address}/ws")
async def websocket_endpoint(websocket: WebSocket, address: str):
    db = await get_db()
    try:
        await _require_inbox(address, db)
    except Exception:
        await websocket.accept()
        await websocket.close(code=4004, reason="Inbox expired or invalid")
        return
        
    await manager.connect(address, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(address, websocket)
    except Exception as e:
        logger.error(f"WebSocket exception for {address}: {e}")
        manager.disconnect(address, websocket)


@app.get("/api/domains")
def list_domains():
    return {"domains": settings.DOMAINS}


@app.post("/api/inbox", response_model=InboxOut)
async def create_inbox(
    local_part: Optional[str] = None,
    domain: Optional[str] = None,
    db=Depends(get_db)
):
    domain = domain or settings.DOMAINS[0]
    if domain not in settings.DOMAINS:
        raise HTTPException(400, f"Unsupported domain. Choose one of: {settings.DOMAINS}")

    local_part = (local_part or _random_local_part()).lower()
    address = f"{local_part}@{domain}"

    existing = await db.inboxes.find_one({"address": address})
    if existing:
        raise HTTPException(409, "That address is already taken, try another")

    inbox_id = uuid.uuid4().hex
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=settings.INBOX_TTL_MINUTES)

    inbox = {
        "_id": inbox_id,
        "address": address,
        "created_at": now,
        "expires_at": expires_at
    }

    await db.inboxes.insert_one(inbox)
    return inbox


@app.get("/api/inbox/{address}", response_model=InboxOut)
async def get_inbox(address: str, db=Depends(get_db)):
    inbox = await _require_inbox(address, db)
    return inbox


@app.post("/api/inbox/{address}/refresh", response_model=InboxOut)
async def refresh_inbox(address: str, db=Depends(get_db)):
    """Extend an inbox's lifetime."""
    inbox = await _require_inbox(address, db)
    new_expires_at = datetime.utcnow() + timedelta(minutes=settings.INBOX_TTL_MINUTES)
    
    await db.inboxes.update_one(
        {"_id": inbox["_id"]},
        {"$set": {"expires_at": new_expires_at}}
    )
    
    # Also update messages expiration so they don't expire before the inbox
    await db.messages.update_many(
        {"inbox_id": inbox["_id"]},
        {"$set": {"expires_at": new_expires_at}}
    )
    
    inbox["expires_at"] = new_expires_at
    return inbox


@app.delete("/api/inbox/{address}")
async def delete_inbox(address: str, db=Depends(get_db)):
    inbox = await _require_inbox(address, db)
    await db.inboxes.delete_one({"_id": inbox["_id"]})
    await db.messages.delete_many({"inbox_id": inbox["_id"]})
    return {"status": "deleted"}


@app.get("/api/inbox/{address}/messages", response_model=list[MessageSummaryOut])
async def list_messages(address: str, db=Depends(get_db)):
    inbox = await _require_inbox(address, db)
    
    messages = await db.messages.find(
        {"inbox_id": inbox["_id"]}
    ).sort("received_at", -1).to_list(length=None)
    
    out = []
    for m in messages:
        out.append({
            "id": m["_id"],
            "sender": m.get("sender"),
            "subject": m.get("subject"),
            "received_at": m["received_at"],
            "has_attachments": len(m.get("attachments", [])) > 0
        })
    return out


@app.get("/api/messages/{message_id}", response_model=MessageDetailOut)
async def get_message(message_id: str, db=Depends(get_db)):
    message = await db.messages.find_one({"_id": message_id})
    if not message:
        raise HTTPException(404, "Message not found or expired")
    
    return {
        "id": message["_id"],
        "sender": message.get("sender"),
        "recipient": message.get("recipient"),
        "subject": message.get("subject"),
        "text_body": message.get("text_body"),
        "html_body": message.get("html_body"),
        "received_at": message["received_at"],
        "attachments": [
            {
                "id": att["id"],
                "filename": att.get("filename"),
                "content_type": att.get("content_type"),
                "size": att.get("size", 0)
            } for att in message.get("attachments", [])
        ]
    }


@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: str, db=Depends(get_db)):
    res = await db.messages.delete_one({"_id": message_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Message not found or expired")
    return {"status": "deleted"}


@app.get("/api/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: str, db=Depends(get_db)):
    # Find the message containing this attachment ID
    message = await db.messages.find_one({"attachments.id": attachment_id})
    if not message:
        raise HTTPException(404, "Attachment not found or expired")
        
    # Extract the attachment details
    att = next((a for a in message.get("attachments", []) if a["id"] == attachment_id), None)
    if not att:
        raise HTTPException(404, "Attachment not found or expired")
        
    return Response(
        content=att["data"],
        media_type=att.get("content_type") or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{att.get("filename") or attachment_id}"'},
    )


async def _require_inbox(address: str, db) -> dict:
    inbox = await db.inboxes.find_one({"address": address.lower()})
    if not inbox:
        raise HTTPException(404, "Inbox not found or expired")
    if inbox["expires_at"] < datetime.utcnow():
        await db.inboxes.delete_one({"_id": inbox["_id"]})
        await db.messages.delete_many({"inbox_id": inbox["_id"]})
        raise HTTPException(404, "Inbox expired")
    return inbox


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}

