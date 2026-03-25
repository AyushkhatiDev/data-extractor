-- Add per-user extraction usage tracking for one-time demo access
-- Compatible with MySQL versions that do not support ADD COLUMN IF NOT EXISTS.
SET @column_exists := (
	SELECT COUNT(*)
	FROM information_schema.COLUMNS
	WHERE TABLE_SCHEMA = DATABASE()
	  AND TABLE_NAME = 'users'
	  AND COLUMN_NAME = 'extraction_uses'
);

SET @migration_sql := IF(
	@column_exists = 0,
	'ALTER TABLE users ADD COLUMN extraction_uses INT NOT NULL DEFAULT 0',
	'SELECT 1'
);

PREPARE migration_stmt FROM @migration_sql;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;
