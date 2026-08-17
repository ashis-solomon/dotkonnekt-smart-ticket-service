"""Pytest configuration, in-memory asyncpg mock fixture, and test client."""

import asyncio
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from uuid import UUID
import httpx
import pytest
import pytest_asyncio
import uuid6

from app.api.deps import get_current_user, get_db_pool
from app.celery_app import celery_app
from app.config import Settings, get_settings
from app.main import create_application
from app.schemas.user import UserResponse, UserRole


class FakeAsyncpgRecord(dict):
    """Dictionary subclass supporting record[key] and record attribute access."""
    def __getitem__(self, item):
        return super().__getitem__(item)


class InMemoryDB:
    """In-memory database engine for fast, isolated unit & integration tests."""
    def __init__(self):
        self.users: Dict[UUID, Dict[str, Any]] = {}
        self.refresh_tokens: Dict[UUID, Dict[str, Any]] = {}
        self.tickets: Dict[UUID, Dict[str, Any]] = {}
        self.ticket_notes: Dict[UUID, Dict[str, Any]] = {}
        self.reminders: Dict[UUID, Dict[str, Any]] = {}
        self.notifications: Dict[UUID, Dict[str, Any]] = {}

    def clear(self):
        self.users.clear()
        self.refresh_tokens.clear()
        self.tickets.clear()
        self.ticket_notes.clear()
        self.reminders.clear()
        self.notifications.clear()


class FakeAsyncpgConnection:
    """Mock connection emulating asyncpg query execution on InMemoryDB with whitespace-normalized matching."""
    def __init__(self, db: InMemoryDB):
        self.db = db

    def _normalize(self, query: str) -> str:
        return re.sub(r"\s+", " ", query.strip().upper())

    async def execute(self, query: str, *args) -> str:
        q = self._normalize(query)

        if q.startswith("CREATE") or "SELECT 1" in q:
            return "CREATE"

        # INSERT INTO users
        if "INSERT INTO USERS" in q:
            uid, email, pw_hash, name, role, created_at, updated_at = (
                args[0], args[1], args[2], args[3], args[4], args[5], args[6]
            )
            self.db.users[uid] = {
                "id": uid, "email": email, "password_hash": pw_hash,
                "full_name": name, "role": role, "is_active": True,
                "created_at": created_at, "updated_at": updated_at,
            }
            return "INSERT 1"

        # INSERT INTO refresh_tokens
        if "INSERT INTO REFRESH_TOKENS" in q:
            tid, uid, token_hash, expires_at, created_at = args[0], args[1], args[2], args[3], args[4]
            self.db.refresh_tokens[tid] = {
                "id": tid, "user_id": uid, "token_hash": token_hash,
                "expires_at": expires_at, "is_revoked": False, "created_at": created_at,
            }
            return "INSERT 1"

        # UPDATE refresh_tokens
        if "UPDATE REFRESH_TOKENS" in q and "IS_REVOKED = TRUE" in q:
            if "WHERE ID =" in q:
                tid = args[0]
                for r in self.db.refresh_tokens.values():
                    if str(r["id"]) == str(tid):
                        r["is_revoked"] = True
            elif "WHERE TOKEN_HASH =" in q:
                thash = args[0]
                for r in self.db.refresh_tokens.values():
                    if r["token_hash"] == thash:
                        r["is_revoked"] = True
            return "UPDATE 1"

        # INSERT INTO tickets
        if "INSERT INTO TICKETS" in q:
            tid, title, desc, email, priority, creator_id, assignee_id = (
                args[0], args[1], args[2], args[3], args[4], args[5], args[6]
            )
            now = datetime.now(timezone.utc)
            self.db.tickets[tid] = {
                "id": tid, "title": title, "description": desc, "customer_email": email,
                "status": "open", "priority": priority, "category": None,
                "ai_summary": None, "manual_triage_required": False,
                "created_by_id": creator_id, "assigned_to_id": assignee_id,
                "created_at": now, "updated_at": now,
            }
            return "INSERT 1"

        # UPDATE tickets (triage or status update)
        if "UPDATE TICKETS" in q:
            if "MANUAL_TRIAGE_REQUIRED = TRUE" in q:
                tid = args[0]
                for t in self.db.tickets.values():
                    if str(t["id"]) == str(tid):
                        t["manual_triage_required"] = True
                        t["updated_at"] = datetime.now(timezone.utc)
            elif "CATEGORY =" in q and "AI_SUMMARY =" in q:
                cat, prio, summary, tid = args[0], args[1], args[2], args[3]
                for t in self.db.tickets.values():
                    if str(t["id"]) == str(tid):
                        t["category"] = cat
                        t["priority"] = prio
                        t["ai_summary"] = summary
                        t["manual_triage_required"] = False
                        t["updated_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        # INSERT INTO ticket_notes
        if "INSERT INTO TICKET_NOTES" in q:
            nid, tid, author_id, content = args[0], args[1], args[2], args[3]
            self.db.ticket_notes[nid] = {
                "id": nid, "ticket_id": tid, "author_id": author_id,
                "content": content, "created_at": datetime.now(timezone.utc),
            }
            return "INSERT 1"

        # INSERT INTO reminders
        if "INSERT INTO REMINDERS" in q:
            rid, tid, creator_id, sched_for, msg = args[0], args[1], args[2], args[3], args[4]
            now = datetime.now(timezone.utc)
            self.db.reminders[rid] = {
                "id": rid, "ticket_id": tid, "created_by_id": creator_id,
                "scheduled_for": sched_for, "message": msg, "status": "pending",
                "created_at": now, "updated_at": now,
            }
            return "INSERT 1"

        # UPDATE reminders
        if "UPDATE REMINDERS" in q:
            if "STATUS = 'CANCELLED'" in q:
                if "WHERE TICKET_ID =" in q:
                    tid = args[0]
                    for r in self.db.reminders.values():
                        if str(r["ticket_id"]) == str(tid) and r["status"] == "pending":
                            r["status"] = "cancelled"
                            r["updated_at"] = datetime.now(timezone.utc)
                elif "WHERE ID =" in q:
                    rid = args[0]
                    for r in self.db.reminders.values():
                        if str(r["id"]) == str(rid):
                            r["status"] = "cancelled"
                            r["updated_at"] = datetime.now(timezone.utc)
            elif "STATUS = 'FIRED'" in q:
                rid = args[0]
                for r in self.db.reminders.values():
                    if str(r["id"]) == str(rid):
                        r["status"] = "fired"
                        r["updated_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        # INSERT INTO notifications
        if "INSERT INTO NOTIFICATIONS" in q:
            nid, rid, uid, msg = args[0], args[1], args[2], args[3]
            self.db.notifications[nid] = {
                "id": nid, "reminder_id": rid, "user_id": uid, "message": msg,
                "created_at": datetime.now(timezone.utc),
            }
            return "INSERT 1"

        return "OK"

    async def fetchrow(self, query: str, *args) -> Optional[FakeAsyncpgRecord]:
        q = self._normalize(query)

        if "INSERT INTO USERS" in q and "RETURNING" in q:
            await self.execute(query, *args)
            uid = args[0]
            return FakeAsyncpgRecord(self.db.users[uid])

        if "FROM USERS WHERE EMAIL =" in q:
            email = args[0]
            for u in self.db.users.values():
                if u["email"].lower() == email.lower():
                    return FakeAsyncpgRecord(u)
            return None

        if "FROM USERS WHERE ID =" in q:
            uid = args[0]
            for u in self.db.users.values():
                if str(u["id"]) == str(uid):
                    return FakeAsyncpgRecord(u)
            return None

        if "FROM REFRESH_TOKENS" in q and "JOIN USERS" in q:
            thash = args[0]
            for r in self.db.refresh_tokens.values():
                if r["token_hash"] == thash:
                    user = None
                    for u in self.db.users.values():
                        if str(u["id"]) == str(r["user_id"]):
                            user = u
                            break
                    if user:
                        combined = dict(r)
                        combined.update({
                            "email": user["email"],
                            "role": user["role"],
                            "is_active": user["is_active"],
                        })
                        return FakeAsyncpgRecord(combined)
            return None

        if "INSERT INTO TICKETS" in q and "RETURNING" in q:
            await self.execute(query, *args)
            tid = args[0]
            return FakeAsyncpgRecord(self.db.tickets[tid])

        if "FROM TICKETS WHERE ID =" in q:
            tid = args[0]
            for t in self.db.tickets.values():
                if str(t["id"]) == str(tid):
                    return FakeAsyncpgRecord(t)
            return None

        if "UPDATE TICKETS" in q and "RETURNING" in q:
            tid = args[-1]
            ticket = None
            for t in self.db.tickets.values():
                if str(t["id"]) == str(tid):
                    ticket = t
                    break

            if ticket:
                idx = 1
                for arg in args[:-1]:
                    if f"TITLE = ${idx}" in q:
                        ticket["title"] = arg
                    elif f"DESCRIPTION = ${idx}" in q:
                        ticket["description"] = arg
                    elif f"STATUS = ${idx}" in q:
                        ticket["status"] = arg
                        if arg in ["resolved", "closed"]:
                            for r in self.db.reminders.values():
                                if str(r["ticket_id"]) == str(tid) and r["status"] == "pending":
                                    r["status"] = "cancelled"
                    elif f"PRIORITY = ${idx}" in q:
                        ticket["priority"] = arg
                    elif f"CATEGORY = ${idx}" in q:
                        ticket["category"] = arg
                    elif f"ASSIGNED_TO_ID = ${idx}" in q:
                        ticket["assigned_to_id"] = arg
                    idx += 1
                ticket["updated_at"] = datetime.now(timezone.utc)
                return FakeAsyncpgRecord(ticket)
            return None

        if "INSERT INTO TICKET_NOTES" in q and "RETURNING" in q:
            await self.execute(query, *args)
            nid = args[0]
            return FakeAsyncpgRecord(self.db.ticket_notes[nid])

        if "INSERT INTO REMINDERS" in q and "RETURNING" in q:
            await self.execute(query, *args)
            rid = args[0]
            return FakeAsyncpgRecord(self.db.reminders[rid])

        if "UPDATE NOTIFICATIONS" in q and "RETURNING" in q:
            nid, uid = args[0], args[1]
            for n in self.db.notifications.values():
                if str(n["id"]) == str(nid) and str(n["user_id"]) == str(uid):
                    n["is_read"] = True
                    return FakeAsyncpgRecord(n)
            return None

        return None

    async def fetch(self, query: str, *args) -> List[FakeAsyncpgRecord]:
        q = self._normalize(query)

        if "FROM TICKETS" in q:
            result = list(self.db.tickets.values())

            # Assigned filter
            if "ASSIGNED_TO_ID =" in q:
                for arg in args:
                    if isinstance(arg, (UUID, str)) and len(str(arg)) == 36:
                        result = [t for t in result if str(t.get("assigned_to_id")) == str(arg)]
                        break

            # Status filter
            if "STATUS = $" in q:
                for arg in args:
                    if arg in ["open", "in_progress", "resolved", "closed"]:
                        result = [t for t in result if t.get("status") == arg]
                        break

            # Priority filter
            if "PRIORITY = $" in q:
                for arg in args:
                    if arg in ["low", "medium", "high"]:
                        result = [t for t in result if t.get("priority") == arg]
                        break

            # Category filter
            if "CATEGORY = $" in q:
                for arg in args:
                    if arg in ["billing", "technical", "account", "other"]:
                        result = [t for t in result if t.get("category") == arg]
                        break

            # Search term filter
            if "TITLE % $" in q or "DESCRIPTION % $" in q or "ILIKE $" in q:
                for arg in args:
                    if isinstance(arg, str) and not arg.startswith("%"):
                        q_lower = arg.lower()
                        result = [
                            t for t in result
                            if q_lower in t["title"].lower() or q_lower in t["description"].lower()
                        ]
                        break

            result.sort(key=lambda x: x["created_at"], reverse=True)

            page_size = args[-2] if len(args) >= 2 and isinstance(args[-2], int) else 20
            offset = args[-1] if len(args) >= 1 and isinstance(args[-1], int) else 0
            sliced = result[offset: offset + page_size]
            return [FakeAsyncpgRecord(r) for r in sliced]

        if "FROM TICKET_NOTES N JOIN USERS U" in q or ("FROM TICKET_NOTES" in q and "JOIN USERS" in q):
            tid = args[0]
            notes = [n for n in self.db.ticket_notes.values() if str(n["ticket_id"]) == str(tid)]
            notes.sort(key=lambda x: x["created_at"])
            enriched = []
            for n in notes:
                rec = dict(n)
                user = None
                for u in self.db.users.values():
                    if str(u["id"]) == str(n["author_id"]):
                        user = u
                        break
                rec["author_name"] = user["full_name"] if user else "Unknown"
                enriched.append(FakeAsyncpgRecord(rec))
            return enriched

        if "FROM REMINDERS" in q and "WHERE R.STATUS = 'PENDING'" in q:
            due = []
            now = datetime.now(timezone.utc)
            for r in self.db.reminders.values():
                if r["status"] == "pending" and r["scheduled_for"] <= now:
                    ticket = None
                    for t in self.db.tickets.values():
                        if str(t["id"]) == str(r["ticket_id"]):
                            ticket = t
                            break
                    if ticket:
                        rec = dict(r)
                        rec["ticket_status"] = ticket["status"]
                        rec["assigned_to_id"] = ticket["assigned_to_id"]
                        due.append(FakeAsyncpgRecord(rec))
            return due

        if "FROM REMINDERS WHERE TICKET_ID =" in q:
            tid = args[0]
            reminders = [r for r in self.db.reminders.values() if str(r["ticket_id"]) == str(tid)]
            reminders.sort(key=lambda x: x["scheduled_for"])
            return [FakeAsyncpgRecord(r) for r in reminders]

        if "FROM NOTIFICATIONS WHERE USER_ID =" in q:
            uid = args[0]
            notifs = [n for n in self.db.notifications.values() if str(n["user_id"]) == str(uid)]
            notifs.sort(key=lambda x: x["created_at"], reverse=True)
            return [FakeAsyncpgRecord(n) for n in notifs]

        return []

    async def fetchval(self, query: str, *args) -> Any:
        q = self._normalize(query)
        if "SELECT 1" in q:
            return 1
        if "SELECT COUNT(*)" in q:
            rows = await self.fetch(query, *args)
            return len(rows)
        row = await self.fetchrow(query, *args)
        if row:
            return next(iter(row.values()))
        return None


class FakeAsyncpgPool:
    """Mock connection pool for asyncpg."""
    def __init__(self, db: InMemoryDB):
        self.db = db
        self.conn = FakeAsyncpgConnection(db)

    class _AcquireContext:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    def acquire(self):
        return self._AcquireContext(self.conn)

    async def close(self):
        pass


@pytest.fixture(scope="session")
def in_memory_db():
    return InMemoryDB()


@pytest.fixture
def fake_db_pool(in_memory_db):
    in_memory_db.clear()
    return FakeAsyncpgPool(in_memory_db)


@pytest.fixture(autouse=True)
def configure_celery():
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )


@pytest_asyncio.fixture
async def async_client(fake_db_pool):
    app = create_application()
    app.state.db_pool = fake_db_pool

    app.dependency_overrides[get_db_pool] = lambda: fake_db_pool

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
