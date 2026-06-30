-- Apply migration 008 on managed PostgreSQL (mint schema).
-- Use when alembic cannot write to public schema.
--
--   psql "$DATABASE_URL" -f scripts/sql/008_category_subscriptions.sql
-- Or paste into psql after:  \c mintdatabase

CREATE SCHEMA IF NOT EXISTS mint;

ALTER TABLE mint.keywords
  ADD COLUMN IF NOT EXISTS is_curated BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS mint.user_category_subscriptions (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES mint.users(id),
  category_id UUID NOT NULL REFERENCES mint.news_categories(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, category_id)
);

CREATE INDEX IF NOT EXISTS ix_user_category_subscriptions_user_id
  ON mint.user_category_subscriptions (user_id);

CREATE INDEX IF NOT EXISTS ix_user_category_subscriptions_category_id
  ON mint.user_category_subscriptions (category_id);

-- Mark seed / default keywords as curated (idempotent).
UPDATE mint.keywords k
SET is_curated = true
FROM mint.news_categories c
WHERE k.organization_id = c.organization_id
  AND k.category_id = c.id
  AND k.owner_user_id IS NULL
  AND k.scope = 'organization'
  AND k.is_curated = false
  AND k.normalized_name IN (
    '보조금', '환경부', '전기차 정책', '충전 정책',
    '충전 인프라', '급속 충전', '완속 충전', '충전소',
    'ocpp', 'csms', 'cpo', 'emsp', 'plug & charge', 'iso 15118',
    '배터리', 'ess', 'v2g', '전력망',
    '충전 사업자', '완성차', '시장 동향',
    '충전 기술', '로밍', '결제'
  );

-- Tell alembic this revision is applied (skip if you will run alembic stamp instead).
CREATE TABLE IF NOT EXISTS mint.alembic_version (
  version_num VARCHAR(32) NOT NULL PRIMARY KEY
);
INSERT INTO mint.alembic_version (version_num)
VALUES ('008')
ON CONFLICT (version_num) DO UPDATE SET version_num = EXCLUDED.version_num;
