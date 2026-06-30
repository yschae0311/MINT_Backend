#!/usr/bin/env python3
"""Merge duplicate / similar organization keywords (run after alembic 008).

Prerequisites:
  - Migration 008 applied (`alembic upgrade head`)
  - DATABASE_URL set (loads MINT_Backend/.env)

Usage:
  cd MINT_Backend

  # 1) DB migration
  alembic upgrade head

  # 2) Preview merges (no writes)
  python3 scripts/merge_duplicate_keywords.py --dry-run

  # 3) Apply merges
  python3 scripts/merge_duplicate_keywords.py --apply

Options:
  --organization-id UUID   Limit to one organization
  --apply                  Persist merges (default: dry-run only)
  --dry-run                Explicit dry-run (default when --apply omitted)

After --apply, if Elasticsearch is enabled, re-sync affected posts or run your
index rebuild so keyword_ids in ES stay consistent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

# Project root must precede site-packages (another `app` package may be installed).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.enums import KeywordScope, KeywordStatus
from app.models.organization import Organization
from app.models.personalization import Keyword
from app.services.personalization_service import (
    TaxonomyService,
    find_duplicate_keyword_pairs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist keyword merges (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned merges without writing (default)",
    )
    parser.add_argument(
        "--organization-id",
        type=str,
        default="",
        help="Only process this organization UUID",
    )
    return parser.parse_args()


def load_organizations(db, organization_id: str) -> list[Organization]:
    if organization_id:
        org = db.get(Organization, UUID(organization_id))
        if not org:
            raise SystemExit(f"Organization not found: {organization_id}")
        return [org]
    return list(db.scalars(select(Organization).order_by(Organization.name)).all())


def process_organization(
    db,
    taxonomy: TaxonomyService,
    org: Organization,
    *,
    apply: bool,
) -> int:
    taxonomy.ensure_defaults(org.id)
    db.commit()

    keywords = list(
        db.scalars(
            select(Keyword).where(
                Keyword.organization_id == org.id,
                Keyword.scope == KeywordScope.organization,
                Keyword.owner_user_id.is_(None),
                Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
            )
        ).all()
    )
    pairs = find_duplicate_keyword_pairs(keywords)
    if not pairs:
        print(f"[{org.name}] duplicate 없음 (조직 키워드 {len(keywords)}개)")
        return 0

    print(f"[{org.name}] 병합 후보 {len(pairs)}건 (조직 키워드 {len(keywords)}개)")
    for source, target in pairs:
        print(
            f"  - '{source.name}' ({source.status.value}, usage={source.usage_count})"
            f" -> '{target.name}' ({target.status.value}, curated={target.is_curated})"
        )
        if apply:
            taxonomy.merge_keywords(source, target, organization_id=org.id)
    if apply:
        db.commit()
        print(f"[{org.name}] 병합 {len(pairs)}건 적용 완료")
    return len(pairs)


def main() -> int:
    args = parse_args()
    apply = args.apply
    if apply and args.dry_run:
        print("error: --apply 와 --dry-run 을 동시에 사용할 수 없습니다.", file=sys.stderr)
        return 2

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== merge_duplicate_keywords ({mode}) ===")

    db = SessionLocal()
    try:
        orgs = load_organizations(db, args.organization_id.strip())
        taxonomy = TaxonomyService(db)
        total = 0
        for org in orgs:
            total += process_organization(db, taxonomy, org, apply=apply)
        print(f"=== 총 병합 후보 {total}건 ===")
        if not apply and total:
            print("적용하려면: python3 scripts/merge_duplicate_keywords.py --apply")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
