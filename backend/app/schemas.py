"""Pydantic schemas for API request/response."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, HttpUrl


# ---- Target ----
class TargetCreate(BaseModel):
    url: str
    name: Optional[str] = None
    group: str = "default"
    tags: list[str] = []
    check_interval: int = 300
    shot_interval: int = 21600
    expect_status: int = 200
    expect_keyword: Optional[str] = None
    protocol: str = "https"
    user_agent: Optional[str] = None
    request_timeout: int = 15
    follow_redirects: bool = True
    verify_ssl: bool = False
    request_headers: dict = {}
    enabled: bool = True


class TargetUpdate(BaseModel):
    url: Optional[str] = None
    name: Optional[str] = None
    group: Optional[str] = None
    tags: Optional[list[str]] = None
    check_interval: Optional[int] = None
    shot_interval: Optional[int] = None
    expect_status: Optional[int] = None
    expect_keyword: Optional[str] = None
    protocol: Optional[str] = None
    user_agent: Optional[str] = None
    request_timeout: Optional[int] = None
    follow_redirects: Optional[bool] = None
    verify_ssl: Optional[bool] = None
    request_headers: Optional[dict] = None
    enabled: Optional[bool] = None


class TargetStatusOut(BaseModel):
    is_ok: Optional[bool] = None
    last_check_at: Optional[datetime] = None
    last_status_code: Optional[int] = None
    last_latency_ms: Optional[int] = None
    last_error: Optional[str] = None
    last_screenshot_id: Optional[int] = None
    last_screenshot_at: Optional[datetime] = None
    has_anomaly: bool = False
    consecutive_fails: int = 0
    # SSL certificate
    ssl_valid: Optional[bool] = None
    ssl_error: Optional[str] = None
    ssl_issuer: Optional[str] = None
    ssl_subject: Optional[str] = None
    ssl_not_after: Optional[datetime] = None
    ssl_days_left: Optional[int] = None
    ssl_warning: Optional[str] = None
    ssl_checked_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TargetOut(BaseModel):
    id: int
    url: str
    name: Optional[str]
    group: str
    tags: list[str]
    check_interval: int
    shot_interval: int
    expect_status: int
    expect_keyword: Optional[str]
    protocol: str = "https"
    user_agent: Optional[str] = None
    request_timeout: int = 15
    follow_redirects: bool = True
    verify_ssl: bool = False
    request_headers: dict = {}
    enabled: bool
    created_at: datetime
    updated_at: datetime
    status: Optional[TargetStatusOut] = None

    class Config:
        from_attributes = True


class TargetBatchCreate(BaseModel):
    urls: list[str]
    group: str = "default"
    check_interval: int = 300
    shot_interval: int = 21600


# ---- Check Result ----
class CheckResultOut(BaseModel):
    id: int
    target_id: int
    checked_at: datetime
    status_code: Optional[int]
    latency_ms: Optional[int]
    is_ok: bool
    error: Optional[str]

    class Config:
        from_attributes = True


# ---- Screenshot ----
class ScreenshotOut(BaseModel):
    id: int
    target_id: int
    taken_at: datetime
    file_path: str
    thumb_path: Optional[str]
    file_size: Optional[int]
    width: Optional[int]
    height: Optional[int]
    page_title: Optional[str]
    console_errors: int = 0
    failed_requests: int = 0
    dom_text_length: int = 0
    is_anomaly: bool = False
    anomaly_score: float = 0
    anomaly_reasons: list = []

    class Config:
        from_attributes = True


# ---- Anomaly ----
class AnomalyOut(BaseModel):
    id: int
    target_id: int
    detected_at: datetime
    anomaly_type: str
    score: float
    reasons: list
    screenshot_id: Optional[int]
    state: str
    notified: bool
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class AnomalyUpdate(BaseModel):
    state: str  # acked, resolved, false_positive


# ---- Alert Channel ----
class AlertChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict
    enabled: bool = True


class AlertChannelOut(BaseModel):
    id: int
    name: str
    channel_type: str
    config: dict
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Dashboard ----
class DashboardStats(BaseModel):
    total_targets: int = 0
    enabled_targets: int = 0
    healthy: int = 0
    unhealthy: int = 0
    unknown: int = 0
    open_anomalies: int = 0
    screenshots_today: int = 0
