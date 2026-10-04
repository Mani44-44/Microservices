from typing import Literal, Optional

from pydantic import BaseModel


class HandleRequest(BaseModel):
    conversation_id: str
    user_id: str
    message: str
    intent: Literal["BOOKING_INQUIRY", "REFUND_REQUEST"]
    user_bearer_token: str


class HandleResponse(BaseModel):
    response_text: str
    ticket_id: Optional[str] = None
    escalated: bool = False
