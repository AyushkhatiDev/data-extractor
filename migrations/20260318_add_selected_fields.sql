-- Add selected_fields support for field-level extraction/export control
SET @db_name = DATABASE();

SET @column_exists = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = @db_name
      AND table_name = 'extraction_tasks'
      AND column_name = 'selected_fields'
);

SET @sql = IF(
    @column_exists = 0,
    'ALTER TABLE extraction_tasks ADD COLUMN selected_fields TEXT NULL AFTER max_results',
    'SELECT 1'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
