-- Migration: add government/nonprofit contact metadata fields to businesses
-- Date: 2026-03-23

ALTER TABLE businesses ADD COLUMN organization_type VARCHAR(50) DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN parent_organization VARCHAR(200) DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN division VARCHAR(200) DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN source_url VARCHAR(500) DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN extraction_method VARCHAR(50) DEFAULT NULL;
