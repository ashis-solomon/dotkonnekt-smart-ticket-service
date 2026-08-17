"""Aggregated v1 API Router."""

from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.notes import router as notes_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.reminders import router as reminders_router
from app.api.v1.tickets import router as tickets_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(tickets_router)
api_v1_router.include_router(notes_router)
api_v1_router.include_router(reminders_router)
api_v1_router.include_router(notifications_router)
