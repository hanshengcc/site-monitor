"""Application configuration."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://monitor:monitor123@localhost:5432/site_monitor"
    screenshots_dir: str = "./screenshots"
    playwright_concurrency: int = 8
    check_interval_minutes: int = 5
    screenshot_interval_minutes: int = 360  # 6h
    check_timeout: int = 15          # HTTP check timeout (seconds)
    screenshot_timeout: int = 30     # Screenshot timeout (seconds)
    default_user_agent: str = "SiteMonitor/1.0"
    viewport_width: int = 1280
    viewport_height: int = 800
    consecutive_fails_threshold: int = 3   # N次连续失败才告警
    max_concurrent_checks: int = 200       # HTTP检测并发数
    localhost_only: bool = True            # 只允许本机/SSH隧道访问(127.0.0.1)

    class Config:
        env_file = ".env"


settings = Settings()
