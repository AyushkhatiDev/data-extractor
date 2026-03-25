import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL")

    if DATABASE_URL:
        # Production (Render - PostgreSQL)
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # Local (MySQL)
        DB_HOST = os.getenv('DB_HOST', 'localhost')
        DB_USER = os.getenv('DB_USER', 'root')
        DB_PASSWORD = os.getenv('DB_PASSWORD', '')
        DB_NAME = os.getenv('DB_NAME', 'dataextractor')
        DB_PORT = os.getenv('DB_PORT', '3306')

        SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Google API (optional – Playwright scraping does not require an API key)
    GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')
    
    # LinkedIn
    LINKEDIN_EMAIL = os.getenv('LINKEDIN_EMAIL', '')
    LINKEDIN_PASSWORD = os.getenv('LINKEDIN_PASSWORD', '')
    
    # Redis
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # Upload/Export folders
    EXPORT_FOLDER = 'exports'

    # Scraping
    PROXY_URL = os.getenv('PROXY_URL', '')

    # AI / LLM Configuration
    LLM_MODEL_PATH = os.getenv('LLM_MODEL_PATH', '')
    LLM_MODEL_URL = os.getenv('LLM_MODEL_URL', '')
    AI_LLM_PROVIDER = os.getenv('AI_LLM_PROVIDER', 'langextract').lower()
    AI_PRIMARY_MODEL = os.getenv('AI_PRIMARY_MODEL', 'Qwen-2.5-VL-7B-Instruct')
    AI_FALLBACK_MODEL = os.getenv('AI_FALLBACK_MODEL', 'Llama-3.1-8B-Instruct')
    AI_LLM_API_BASE_URL = os.getenv(
        'AI_LLM_API_BASE_URL',
        os.getenv('LANGEXTRACT_MODEL_URL', 'http://localhost:11434')
    )
    AI_DISABLE_API_KEYS = os.getenv('AI_DISABLE_API_KEYS', 'true').lower() == 'true'
    AI_LLM_API_KEY = '' if AI_DISABLE_API_KEYS else os.getenv('AI_LLM_API_KEY', '')
    AI_LLM_TIMEOUT = int(os.getenv('AI_LLM_TIMEOUT', '60'))
    AI_LLM_MAX_TOKENS = int(os.getenv('AI_LLM_MAX_TOKENS', '1024'))
    AI_LLM_TEMPERATURE = float(os.getenv('AI_LLM_TEMPERATURE', '0.1'))
    AI_MIN_CONFIDENCE = float(os.getenv('AI_MIN_CONFIDENCE', '0.55'))
    EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'all-MiniLM-L6-v2')
    AI_EXTRACTION_ENABLED = os.getenv('AI_EXTRACTION_ENABLED', 'true').lower() == 'true'
    
class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
