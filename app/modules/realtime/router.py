import asyncio

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.enums import AccountStatus, ResidentStatus, UserType
from app.core.realtime import ALL, SUPER, hub, room_mobile, room_society, room_user
from app.core.security import decode_token, use_ws_ticket

router = APIRouter(tags=["Realtime"])


def _rooms_for_token(payload: dict) -> list[str] | None:
    """Rooms this login may listen to, or None when the token is no longer valid."""
    from app.core.deps import _load_user
    from app.modules.residents.models import Resident
    from app.modules.service_partners.models import PartnerSociety

    with SessionLocal() as db:
        user = _load_user(db, payload.get("typ"), int(payload.get("sub")))
        if not user:
            return None
        if user.is_resident:
            if user.status == ResidentStatus.DEACTIVATED:
                return None
        elif user.status != AccountStatus.ACTIVE:
            return None

        rooms = [ALL, room_user(user.user_type, user.id)]
        if user.is_super_admin:
            rooms.append(SUPER)
        elif user.is_resident:
            rooms.append(room_mobile(user.mobile))
            homes = db.scalars(select(Resident.society_id).where(Resident.mobile == user.mobile,
                                                                 Resident.status != ResidentStatus.DEACTIVATED)).all()
            rooms += [room_society(s) for s in set(homes)]
        elif user.user_type == UserType.PARTNER:
            rooms += [room_society(s) for s in db.scalars(
                select(PartnerSociety.society_id).where(PartnerSociety.partner_id == user.id)).all()]
        elif user.society_id:
            rooms.append(room_society(user.society_id))
        return rooms


@router.websocket("/realtime/ws")
async def realtime_ws(websocket: WebSocket, ticket: str | None = Query(None), token: str | None = Query(None)):
    """Live updates for the logged-in user.

    The web app opens it with a one-minute, one-use `ticket` (POST /auth/ws-ticket). The phone app, which keeps its
    token in private storage, may pass the access `token` itself.
    """
    try:
        payload = decode_token(ticket or token or "")
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return
    if ticket:
        if payload.get("scope") != "ws" or not use_ws_ticket(payload):
            await websocket.close(code=4401)
            return
    elif payload.get("scope") != "access":
        await websocket.close(code=4401)
        return
    rooms = await run_in_threadpool(_rooms_for_token, payload)
    if rooms is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    hub.loop = asyncio.get_running_loop()
    hub.join(websocket, rooms)
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(websocket)
