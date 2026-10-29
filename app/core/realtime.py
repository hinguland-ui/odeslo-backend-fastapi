"""Real-time push to open browser / app sessions over WebSocket.

How it works
- Every WebSocket joins "rooms": its own user room, its society room(s), and the global room.
- Code never sends while a database transaction is still open. Events are queued on the DB session and are
  delivered only after the commit succeeds, so a client that reacts to an event always reads the saved data.
- Chat and live-location use their own events. Everything else is covered by a coarse "refresh" signal that is sent
  to the society (or to everyone when the actor has no society) after any write; clients then refetch what they show.

This hub lives in one server process. With several workers, publish through Redis pub/sub instead (same API).
"""

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket
from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger("odeslo.realtime")

ALL = "all"
SUPER = "super"


def room_user(user_type: str, user_id: int) -> str:
    return f"u:{user_type}:{user_id}"


def room_society(society_id: int) -> str:
    return f"s:{society_id}"


def room_mobile(mobile: str) -> str:
    """A resident can own flats in several societies; all of their homes share this room."""
    return f"m:{mobile}"


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self.locks: dict[WebSocket, asyncio.Lock] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def join(self, ws: WebSocket, rooms: list[str]) -> None:
        self.locks[ws] = asyncio.Lock()
        for r in rooms:
            self.rooms[r].add(ws)

    def leave(self, ws: WebSocket) -> None:
        self.locks.pop(ws, None)
        for name in list(self.rooms):
            self.rooms[name].discard(ws)
            if not self.rooms[name]:
                del self.rooms[name]

    async def _deliver(self, rooms: list[str], message: dict) -> None:
        targets: set[WebSocket] = set()
        for r in rooms:
            targets |= self.rooms.get(r, set())
        for ws in list(targets):
            lock = self.locks.get(ws)
            if lock is None:
                continue
            try:
                async with lock:
                    await ws.send_json(message)
            except Exception:  # closed socket: forget it
                self.leave(ws)

    def publish(self, rooms: list[str], message: dict) -> None:
        """Safe to call from normal (threaded) request handlers."""
        loop = self.loop
        if not rooms or loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._deliver(list(dict.fromkeys(rooms)), message), loop)
        except RuntimeError:
            log.debug("realtime loop is not running")


hub = Hub()


# ------------------------------------------------------------------ queueing (delivered after commit)
def queue(db: Session, rooms: list[str], message: dict) -> None:
    db.info.setdefault("rt_events", []).append((rooms, message))


def rooms_for_user(db: Session, user_type: str, user_id: int) -> list[str]:
    rooms = [room_user(user_type, user_id)]
    if user_type == "resident":
        from app.modules.residents.models import Resident

        resident = db.get(Resident, user_id)
        if resident:
            rooms.append(room_mobile(resident.mobile))
    return rooms


def queue_user(db: Session, user_type: str, user_id: int, message: dict) -> None:
    queue(db, rooms_for_user(db, user_type, user_id), message)


def quiet(db: Session) -> None:
    """Skip the generic refresh signal for this request (chat, live location, mark-as-read)."""
    db.info["rt_quiet"] = True


@event.listens_for(Session, "after_flush")
def _mark_dirty(session: Session, _ctx) -> None:
    if session.new or session.dirty or session.deleted:
        session.info["rt_dirty"] = True


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    events = session.info.pop("rt_events", [])
    dirty = session.info.pop("rt_dirty", False)
    for rooms, message in events:
        hub.publish(rooms, message)
    actor = session.info.get("actor")
    if actor and dirty and not session.info.get("rt_quiet"):
        _user_type, society_id = actor
        hub.publish([room_society(society_id), SUPER] if society_id else [ALL], {"type": "refresh"})


@event.listens_for(Session, "after_soft_rollback")
def _drop_on_rollback(session: Session, _previous) -> None:
    session.info.pop("rt_events", None)
    session.info.pop("rt_dirty", None)
