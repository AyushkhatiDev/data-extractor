-- Migration: minimize businesses table to core contact fields
-- Assumes MySQL. Run manually via your DB client.

-- 1) Add location column if missing (safe on fresh schema)
ALTER TABLE businesses
  ADD COLUMN location TEXT;

-- 2) Backfill location from address/city if present
UPDATE businesses
  SET location = TRIM(CONCAT_WS(', ', address, city))
  WHERE (location IS NULL OR location = '')
    AND (address IS NOT NULL OR city IS NOT NULL);

-- 3) Drop unused columns
ALTER TABLE businesses
  DROP COLUMN business_name,
  DROP COLUMN address,
  DROP COLUMN city,
  DROP COLUMN linkedin_url,
  DROP COLUMN google_maps_url,
  DROP COLUMN place_id,
  DROP COLUMN industry,
  DROP COLUMN latitude,
  DROP COLUMN longitude,
  DROP COLUMN description;

-- 4) Drop unused index
DROP INDEX idx_business_name ON businesses;
