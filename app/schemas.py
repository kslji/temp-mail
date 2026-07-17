from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class InboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address: str
    created_at: datetime
    expires_at: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: Optional[str]
    content_type: Optional[str]
    size: int


class MessageSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: Optional[str]
    subject: Optional[str]
    received_at: datetime
    has_attachments: bool = False


class MessageDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: Optional[str]
    recipient: Optional[str]
    subject: Optional[str]
    text_body: Optional[str]
    html_body: Optional[str]
    received_at: datetime
    attachments: List[AttachmentOut] = []
