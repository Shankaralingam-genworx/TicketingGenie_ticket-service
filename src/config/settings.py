"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    AUTH_SERVICE_URL: str
    APP_ENV: str = "development"
    APP_PORT: int = 8001
    TICKET_PRODUCT_CODE: str = "TKT"

    # JWT — shared secret with Auth Service
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"

    # Groq LLM for severity analysis
    GROQ_API_KEY: str
    GROQ_MODEL: str 
    
    EMAIL_FROM : str
    SMTP_HOST : str
    SMTP_PORT : str
    SMTP_USER : str
    SMTP_PASSWORD : str
    
    CELERY_BROKER_URL :str
    CELERY_RESULT_BACKEND :str
    
    
    # Inbound IMAP (support inbox polling for email-to-ticket intake)
    SUPPORT_EMAIL_HOST: str
    SUPPORT_EMAIL_USER: str
    SUPPORT_EMAIL_PASS: str
    SUPPORT_EMAIL_PORT: int = 993
    SUPPORT_EMAIL_FOLDER: str = "INBOX"
    EMAIL_POLL_INTERVAL: int = 60   # seconds between Celery Beat inbox polls

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
