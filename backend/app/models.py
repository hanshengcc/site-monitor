"""SQLAlchemy ORM models."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, SmallInteger, String, Text, Boolean,
    Float, DateTime, ForeignKey, ARRAY, JSON, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Target(Base):
    __tablename__ = "targets"

    id = Column(Integer, primary_key=True)
    url = Column(Text, nullable=False)
    name = Column(Text)
    group = Column("group", Text, default="default")
    tags = Column(ARRAY(Text), default=[])
    check_interval = Column(Integer, default=300)
    shot_interval = Column(Integer, default=21600)
    expect_status = Column(SmallInteger, default=200)
    expect_keyword = Column(Text)
    protocol = Column(Text, default="https")
    user_agent = Column(Text)
    request_timeout = Column(Integer, default=15)
    follow_redirects = Column(Boolean, default=True)
    verify_ssl = Column(Boolean, default=False)
    request_headers = Column(JSON, default={})
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    status = relationship("TargetStatus", uselist=False, back_populates="target", lazy="joined", cascade="all, delete-orphan", passive_deletes=True)


class GlobalSetting(Base):
    __tablename__ = "global_settings"

    key = Column(Text, primary_key=True)
    value = Column(JSON, nullable=False)


class GroupSetting(Base):
    __tablename__ = "group_settings"

    group_name = Column(Text, primary_key=True)
    max_concurrency = Column(Integer, default=10)
    request_timeout = Column(Integer, default=15)
    user_agent = Column(Text)
    enabled = Column(Boolean, default=True)
    note = Column(Text)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class CheckResult(Base):
    __tablename__ = "check_results"

    id = Column(BigInteger, autoincrement=True, primary_key=True)
    target_id = Column(Integer, nullable=False)
    checked_at = Column(DateTime(timezone=True), primary_key=True, default=datetime.utcnow)
    status_code = Column(SmallInteger)
    latency_ms = Column(Integer)
    is_ok = Column(Boolean, default=False)
    error = Column(Text)


class Screenshot(Base):
    __tablename__ = "screenshots"

    id = Column(BigInteger, primary_key=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    taken_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    file_path = Column(Text, nullable=False)
    thumb_path = Column(Text)
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    page_title = Column(Text)
    console_errors = Column(Integer, default=0)
    failed_requests = Column(Integer, default=0)
    dom_text_length = Column(Integer, default=0)
    is_anomaly = Column(Boolean, default=False)
    anomaly_score = Column(Float, default=0)
    anomaly_reasons = Column(JSON, default=[])


class Baseline(Base):
    __tablename__ = "baselines"

    id = Column(Integer, primary_key=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, unique=True)
    screenshot_id = Column(BigInteger, ForeignKey("screenshots.id", ondelete="CASCADE"), nullable=False)
    set_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    set_by = Column(Text, default="auto")


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(BigInteger, primary_key=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    detected_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    anomaly_type = Column(Text, nullable=False)
    score = Column(Float, default=0)
    reasons = Column(JSON, default=[])
    screenshot_id = Column(BigInteger, ForeignKey("screenshots.id"))
    state = Column(Text, default="open")
    notified = Column(Boolean, default=False)
    resolved_at = Column(DateTime(timezone=True))


class AlertChannel(Base):
    __tablename__ = "alert_channels"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    channel_type = Column(Text, nullable=False)
    config = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class TargetStatus(Base):
    __tablename__ = "target_status"

    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), primary_key=True)
    is_ok = Column(Boolean)
    last_check_at = Column(DateTime(timezone=True))
    last_status_code = Column(SmallInteger)
    last_latency_ms = Column(Integer)
    last_error = Column(Text)
    last_screenshot_id = Column(BigInteger)
    last_screenshot_at = Column(DateTime(timezone=True))
    has_anomaly = Column(Boolean, default=False)
    consecutive_fails = Column(Integer, default=0)

    target = relationship("Target", back_populates="status")
