-- Migration: Add email validation columns to businesses
-- and list_type to extraction_tasks
-- Date: 2026-03-23

ALTER TABLE businesses ADD COLUMN llm_validity_score FLOAT DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN email_type VARCHAR(50) DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN mx_valid BOOLEAN DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN disposable_domain BOOLEAN DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN heuristic_score FLOAT DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN final_confidence FLOAT DEFAULT NULL;
ALTER TABLE businesses ADD COLUMN verification_status VARCHAR(20) DEFAULT NULL;

ALTER TABLE extraction_tasks ADD COLUMN list_type VARCHAR(100) DEFAULT NULL;
