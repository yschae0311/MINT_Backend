#!/usr/bin/env python3
"""One-time recent-corpus keyword/category backfill (not a nightly job).

Re-runs ClassificationService.classify_post on the newest org posts so
PostKeyword density catches up after raising per-post keyword limits.
Does not add a second Gemini stack — uses the same classify path as crawl.

Usage:
  cd MINT_Backend
  python3 scripts/backfill_recent_classification.py --limit 500
  python3 scripts/backfill_recent_classification.py --limit 2000 --organization-id UUID
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_recent_classification")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--organization-id", type=UUID, default=None)
    args = parser.parse_args()

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.enums import PostStatus
    from app.models.organization import Organization
    from app.models.post import Post
    from app.services.personalization_service import ClassificationService

    db = SessionLocal()
    try:
        org_ids: list[UUID]
        if args.organization_id:
            org_ids = [args.organization_id]
        else:
            org_ids = list(db.scalars(select(Organization.id)).all())

        total_ok = 0
        total_fail = 0
        for org_id in org_ids:
            posts = list(
                db.scalars(
                    select(Post)
                    .where(
                        Post.organization_id == org_id,
                        Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
                    )
                    .order_by(Post.collected_at.desc())
                    .limit(max(1, min(args.limit, 2000)))
                ).all()
            )
            logger.info("org=%s posts=%s", org_id, len(posts))
            for post in posts:
                try:
                    ClassificationService(db).classify_post(post)
                    db.commit()
                    total_ok += 1
                except Exception as exc:  # noqa: BLE001 — batch must continue
                    db.rollback()
                    total_fail += 1
                    logger.warning("post=%s failed: %s", post.id, exc)
        logger.info("done ok=%s fail=%s", total_ok, total_fail)
        return 0 if total_fail == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
