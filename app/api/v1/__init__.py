"""MINT API v1 route registry.

Modules: auth, users, inquiries, sources, posts, reports, slack,
stats, chat, search, jobs (+ health).
"""
from fastapi import APIRouter

from app.api.v1 import auth, chat, health, inquiries, jobs, posts, reports, search, slack, sources, stats, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(inquiries.router, prefix="/inquiries", tags=["inquiries"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(slack.router, prefix="/slack", tags=["slack"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
