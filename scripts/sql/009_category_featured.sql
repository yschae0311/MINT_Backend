-- Migration 009: news_categories.is_featured (org main fields)
-- Run after 008 if alembic is not used.

SET search_path TO mint;

ALTER TABLE news_categories
  ADD COLUMN IF NOT EXISTS is_featured BOOLEAN NOT NULL DEFAULT true;

-- Default: categories with seed keywords are org main fields.
UPDATE news_categories
SET is_featured = true
WHERE normalized_name IN (
  '정책/규제', '충전 인프라', 'csms/ocpp', '배터리/에너지', '시장/기업', '기술'
);

UPDATE news_categories
SET is_featured = false
WHERE normalized_name IN ('커뮤니티/현장', '기타')
  AND NOT EXISTS (
    SELECT 1 FROM keywords k
    WHERE k.category_id = news_categories.id AND k.is_curated = true
  );

INSERT INTO alembic_version (version_num)
VALUES ('009')
ON CONFLICT (version_num) DO UPDATE SET version_num = EXCLUDED.version_num;
