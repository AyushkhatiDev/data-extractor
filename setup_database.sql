-- Create database
CREATE DATABASE IF NOT EXISTS dataextractor;
USE dataextractor;

-- Create tables
CREATE TABLE IF NOT EXISTS extraction_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    keyword VARCHAR(255) NOT NULL,
    location VARCHAR(255) NOT NULL,
    source VARCHAR(50) NOT NULL,
    radius INT DEFAULT 5000,
    max_results INT DEFAULT 50,
    selected_fields TEXT NULL,
    list_type VARCHAR(100) NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    total_records INT DEFAULT 0,
    INDEX idx_status (status),
    INDEX idx_created (created_at)
);

CREATE TABLE IF NOT EXISTS businesses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id INT NOT NULL,
    name VARCHAR(500),
    email VARCHAR(255),
    phone VARCHAR(50),
    website VARCHAR(500),
    location TEXT,
    description TEXT,
    social_links TEXT,
    confidence_score FLOAT,
    owner VARCHAR(255),
    organization_type VARCHAR(50),
    parent_organization VARCHAR(200),
    division VARCHAR(200),
    source_url VARCHAR(500),
    extraction_method VARCHAR(50),
    llm_validity_score FLOAT,
    email_type VARCHAR(50),
    mx_valid BOOLEAN,
    disposable_domain BOOLEAN,
    heuristic_score FLOAT,
    final_confidence FLOAT,
    verification_status VARCHAR(20),
    rating FLOAT,
    review_count INT,
    hours TEXT,
    categories VARCHAR(500),
    price_level VARCHAR(10),
    source VARCHAR(50),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES extraction_tasks(id) ON DELETE CASCADE,
    INDEX idx_task (task_id),
    INDEX idx_email (email)
);
