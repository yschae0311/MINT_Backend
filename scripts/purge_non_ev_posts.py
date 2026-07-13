#!/usr/bin/env python3
"""Hide (or soft-delete) posts that are not EV / charger related.

Uses the same keyword gate as crawl ingest (`passes_keyword_gate`):
keep only posts with a strong EV·전기차·충전기 신호.

Prerequisites:
  - DATABASE_URL set (loads MINT_Backend/.env)

Usage:
  cd MINT_Backend

  # Preview what would be hidden (default)
  .venv/bin/python scripts/purge_non_ev_posts.py --dry-run

  # Apply: set status=hidden for non-EV posts
  .venv/bin/python scripts/purge_non_ev_posts.py --apply

  # Soft-delete instead of hidden
  .venv/bin/python scripts/purge_non_ev_posts.py --apply --status deleted

Options:
  --organization-id UUID   Limit to one organization
  --limit N                Cap how many non-EV posts to change (after scan)
  --sample N               Print up to N sample titles that would be purged
  --batch-size N           Content fetch batch size (default 200)
  --include-already-hidden Also re-check posts already hidden
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models.enums import PostStatus
from app.models.organization import Organization
from app.models.post import Post
from app.search.post_content import mget_post_contents
from app.services.ev_relevance import passes_keyword_gate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Persist status changes (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    parser.add_argument("--organization-id", type=str, default="", help="Only this organization UUID")
    parser.add_argument(
        "--status",
        choices=("hidden", "deleted"),
        default="hidden",
        help="Target status for non-EV posts (default: hidden)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max posts to change (0 = no cap)")
    parser.add_argument("--sample", type=int, default=30, help="Sample titles to print")
    parser.add_argument("--batch-size", type=int, default=200, help="Content mget batch size")
    parser.add_argument(
        "--include-already-hidden",
        action="store_true",
        help="Also scan posts that are already hidden",
    )
    return parser.parse_args()


def load_orgs(db, organization_id: str) -> list[Organization]:
    if organization_id:
        org = db.get(Organization, UUID(organization_id))
        if not org:
            raise SystemExit(f"Organization not found: {organization_id}")
        return [org]
    return list(db.scalars(select(Organization).order_by(Organization.name)).all())


def candidate_statuses(*, include_already_hidden: bool) -> list[PostStatus]:
    statuses = [PostStatus.published, PostStatus.pending]
    if include_already_hidden:
        statuses.append(PostStatus.hidden)
    return statuses


def post_body(post: Post, content) -> str:
    parts: list[str] = []
    if content is not None:
        if getattr(content, "summary", None):
            parts.append(content.summary or "")
        body = getattr(content, "body", None) or getattr(content, "raw_content", None)
        if body:
            parts.append(body)
    if post.raw_content:
        parts.append(post.raw_content)
    if post.category:
        parts.append(post.category)
    return "\n".join(parts)[:8000]


def main() -> int:
    args = parse_args()
    apply = bool(args.apply) and not args.dry_run
    target = PostStatus.hidden if args.status == "hidden" else PostStatus.deleted
    statuses = candidate_statuses(include_already_hidden=args.include_already_hidden)

    db = SessionLocal()
    try:
        orgs = load_orgs(db, args.organization_id)
        total_keep = 0
        total_purge = 0
        samples: list[tuple[str, str, str]] = []

        for org in orgs:
            print(f"\n=== {org.name} ({org.id}) ===")
            q = (
                select(Post)
                .options(joinedload(Post.source))
                .where(
                    Post.organization_id == org.id,
                    Post.status.in_(statuses),
                    Post.status != PostStatus.deleted,
                )
                .order_by(Post.collected_at.desc())
            )
            posts = list(db.scalars(q).unique().all())
            print(f"scanned={len(posts)} statuses={[s.value for s in statuses]}")

            keep = 0
            purge_ids: list[UUID] = []

            for i in range(0, len(posts), args.batch_size):
                batch = posts[i : i + args.batch_size]
                contents = mget_post_contents(db, [p.id for p in batch])
                for post in batch:
                    content = contents.get(post.id)
                    url = ""
                    if content is not None and getattr(content, "original_url", None):
                        url = content.original_url or ""
                    if not url and post.source and post.source.url:
                        url = post.source.url
                    body = post_body(post, content)
                    if passes_keyword_gate(post.title or "", body, url):
                        keep += 1
                        continue
                    purge_ids.append(post.id)
                    if len(samples) < args.sample:
                        samples.append((org.name, post.status.value, (post.title or "")[:120]))

            if args.limit and len(purge_ids) > args.limit:
                purge_ids = purge_ids[: args.limit]

            print(f"keep_ev={keep} purge_non_ev={len(purge_ids)} target_status={target.value}")

            if apply and purge_ids:
                changed = 0
                for post_id in purge_ids:
                    post = db.get(Post, post_id)
                    if not post or post.status == target:
                        continue
                    post.status = target
                    changed += 1
                db.commit()
                print(f"applied={changed}")
            elif not apply:
                print("dry-run: no writes (pass --apply to persist)")

            total_keep += keep
            total_purge += len(purge_ids)

        print("\n--- sample non-EV titles ---")
        for org_name, status, title in samples:
            print(f"[{org_name}|{status}] {title}")

        print(f"\nTOTAL keep_ev={total_keep} purge_non_ev={total_purge} mode={'APPLY' if apply else 'DRY-RUN'}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
