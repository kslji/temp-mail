"""
Inbound SMTP receiver.

This is what actually "produces" mail into inboxes: once your domain's MX
record points at the machine running this, real mail sent to
random-string@yourdomain.com lands here, gets parsed, and is stored.

Run standalone with: python run_smtp.py
"""
import logging
import uuid
from email import message_from_bytes
from email.message import Message as EmailMessage
from datetime import datetime

# pyrefly: ignore [missing-import]
from aiosmtpd.controller import Controller
# pyrefly: ignore [missing-import]
from aiosmtpd.smtp import Envelope, Session

from .config import settings
from .database import get_db

logger = logging.getLogger("tempmail.smtp")


def _extract_bodies_and_attachments(msg: EmailMessage):
    text_body, html_body = None, None
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in disposition or part.get_filename():
                payload = part.get_payload(decode=True) or b""
                attachments.append({
                    "filename": part.get_filename(),
                    "content_type": content_type,
                    "size": len(payload),
                    "data": payload,
                })
                continue

            if content_type == "text/plain" and text_body is None:
                text_body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            elif content_type == "text/html" and html_body is None:
                html_body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        payload = msg.get_payload(decode=True) or b""
        decoded = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html_body = decoded
        else:
            text_body = decoded

    return text_body, html_body, attachments


class TempMailHandler:
    async def handle_RCPT(self, server, session: Session, envelope: Envelope,
                           address: str, rcpt_options):
        domain = address.split("@")[-1].lower()
        if domain not in settings.DOMAINS:
            return "550 relay not permitted for this domain"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session: Session, envelope: Envelope):
        raw = envelope.content
        if len(raw) > settings.MAX_MESSAGE_SIZE:
            return "552 message too large"

        parsed = message_from_bytes(raw)
        text_body, html_body, attachments = _extract_bodies_and_attachments(parsed)

        db = await get_db()
        now = datetime.utcnow()
        for rcpt in envelope.rcpt_tos:
            inbox = await db.inboxes.find_one({"address": rcpt.lower()})
            if inbox is None:
                # No one requested this address -- silently drop.
                continue

            # Ensure the inbox hasn't expired yet
            if inbox["expires_at"] < now:
                continue

            message_id = uuid.uuid4().hex
            
            # Format attachments
            msg_attachments = []
            for att in attachments:
                msg_attachments.append({
                    "id": uuid.uuid4().hex,
                    "filename": att["filename"],
                    "content_type": att["content_type"],
                    "size": att["size"],
                    "data": att["data"],
                })

            message = {
                "_id": message_id,
                "inbox_id": inbox["_id"],
                "sender": envelope.mail_from,
                "recipient": rcpt,
                "subject": parsed.get("Subject"),
                "text_body": text_body,
                "html_body": html_body,
                "raw_size": len(raw),
                "received_at": datetime.utcnow(),
                "expires_at": inbox["expires_at"],
                "attachments": msg_attachments,
            }
            await db.messages.insert_one(message)

        return "250 Message accepted for delivery"


def start_smtp_server() -> Controller:
    controller = Controller(
        TempMailHandler(),
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
    )
    controller.start()
    logger.info(f"SMTP server listening on {settings.SMTP_HOST}:{settings.SMTP_PORT}")
    return controller
