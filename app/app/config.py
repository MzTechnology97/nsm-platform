import os
from dataclasses import dataclass
from urllib.parse import quote_plus


def required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Network Security Platform")
    app_env: str = os.getenv("APP_ENV", "development")
    app_timezone: str = os.getenv("APP_TIMEZONE", "Europe/Rome")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    db_host: str = os.getenv("DB_HOST", "postgres")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_user: str = os.getenv("DB_USER", "network_platform")
    db_name: str = os.getenv("DB_NAME", "network_platform")

    redis_host: str = os.getenv("REDIS_HOST", "redis")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))

    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "nsp_session")
    session_cookie_secure: bool = (
        os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    )
    session_max_age_seconds: int = int(
        os.getenv("SESSION_MAX_AGE_SECONDS", "28800")
    )

    @property
    def postgres_password(self):
        return required("POSTGRES_PASSWORD")

    @property
    def redis_password(self):
        return required("REDIS_PASSWORD")

    @property
    def app_secret_key(self):
        return required("APP_SECRET_KEY")

    @property
    def encryption_master_key(self):
        return required("ENCRYPTION_MASTER_KEY")

    @property
    def database_url(self):
        return (
            f"postgresql+psycopg://{self.db_user}:"
            f"{quote_plus(self.postgres_password)}@{self.db_host}:{self.db_port}/"
            f"{self.db_name}"
        )

    @property
    def redis_url(self):
        return (
            f"redis://:{quote_plus(self.redis_password)}@"
            f"{self.redis_host}:{self.redis_port}/{self.redis_db}"
        )


settings = Settings()
