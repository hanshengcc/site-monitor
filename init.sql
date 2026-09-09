-- ============================================================
-- Site Monitor Schema
-- ============================================================

-- 监控目标
CREATE TABLE IF NOT EXISTS targets (
    id              SERIAL PRIMARY KEY,
    url             TEXT NOT NULL,
    name            TEXT,
    "group"         TEXT DEFAULT 'default',
    tags            TEXT[] DEFAULT '{}',
    check_interval  INTEGER DEFAULT 300,        -- HTTP检测间隔(秒)
    shot_interval   INTEGER DEFAULT 21600,      -- 截图间隔(秒) 默认6h
    expect_status   SMALLINT DEFAULT 200,
    expect_keyword  TEXT,
    protocol        TEXT DEFAULT 'https',
    user_agent      TEXT,
    request_timeout INTEGER DEFAULT 15,
    follow_redirects BOOLEAN DEFAULT TRUE,
    verify_ssl      BOOLEAN DEFAULT FALSE,
    request_headers JSONB DEFAULT '{}',
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 兼容老数据库字段迁移
ALTER TABLE targets ADD COLUMN IF NOT EXISTS protocol TEXT DEFAULT 'https';
ALTER TABLE targets ADD COLUMN IF NOT EXISTS user_agent TEXT;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS request_timeout INTEGER DEFAULT 15;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS follow_redirects BOOLEAN DEFAULT TRUE;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS verify_ssl BOOLEAN DEFAULT FALSE;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS request_headers JSONB DEFAULT '{}';

CREATE INDEX idx_targets_enabled ON targets(enabled);
CREATE INDEX idx_targets_group ON targets("group");
CREATE UNIQUE INDEX IF NOT EXISTS idx_targets_url_unique ON targets(url);

-- 检测结果 (按月分区)
CREATE TABLE IF NOT EXISTS check_results (
    id              BIGSERIAL,
    target_id       INTEGER NOT NULL,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status_code     SMALLINT,
    latency_ms      INTEGER,
    is_ok           BOOLEAN DEFAULT FALSE,
    error           TEXT,
    PRIMARY KEY (id, checked_at)
) PARTITION BY RANGE (checked_at);

CREATE INDEX idx_check_results_target ON check_results(target_id, checked_at DESC);

-- 创建当月及下月分区的函数
CREATE OR REPLACE FUNCTION create_monthly_partitions()
RETURNS void AS $$
DECLARE
    month_start DATE;
    month_end   DATE;
    part_name   TEXT;
BEGIN
    FOR i IN 0..2 LOOP
        month_start := date_trunc('month', CURRENT_DATE + (i || ' month')::interval)::date;
        month_end   := (month_start + interval '1 month')::date;
        part_name   := 'check_results_' || to_char(month_start, 'YYYY_MM');

        IF NOT EXISTS (
            SELECT 1 FROM pg_class WHERE relname = part_name
        ) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF check_results FOR VALUES FROM (%L) TO (%L)',
                part_name, month_start, month_end
            );
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

SELECT create_monthly_partitions();

-- 截图记录
CREATE TABLE IF NOT EXISTS screenshots (
    id              BIGSERIAL PRIMARY KEY,
    target_id       INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    taken_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    file_path       TEXT NOT NULL,
    thumb_path      TEXT,
    file_size       INTEGER,
    width           INTEGER,
    height          INTEGER,
    page_title      TEXT,
    console_errors  INTEGER DEFAULT 0,
    failed_requests INTEGER DEFAULT 0,
    dom_text_length INTEGER DEFAULT 0,
    is_anomaly      BOOLEAN DEFAULT FALSE,
    anomaly_score   REAL DEFAULT 0,
    anomaly_reasons JSONB DEFAULT '[]'
);

CREATE INDEX idx_screenshots_target ON screenshots(target_id, taken_at DESC);
CREATE INDEX idx_screenshots_anomaly ON screenshots(is_anomaly) WHERE is_anomaly = TRUE;

-- 基线截图
CREATE TABLE IF NOT EXISTS baselines (
    id              SERIAL PRIMARY KEY,
    target_id       INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    screenshot_id   BIGINT NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
    set_at          TIMESTAMPTZ DEFAULT NOW(),
    set_by          TEXT DEFAULT 'auto',
    UNIQUE(target_id)
);

-- 异常记录 / 事件
CREATE TABLE IF NOT EXISTS anomalies (
    id              BIGSERIAL PRIMARY KEY,
    target_id       INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    anomaly_type    TEXT NOT NULL,       -- http_error, keyword_error, white_screen, render_diff
    score           REAL DEFAULT 0,
    reasons         JSONB DEFAULT '[]',
    screenshot_id   BIGINT REFERENCES screenshots(id),
    state           TEXT DEFAULT 'open', -- open, acked, resolved, false_positive
    notified        BOOLEAN DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_anomalies_target ON anomalies(target_id, detected_at DESC);
CREATE INDEX idx_anomalies_state ON anomalies(state) WHERE state = 'open';

-- 告警渠道
CREATE TABLE IF NOT EXISTS alert_channels (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    channel_type    TEXT NOT NULL,       -- webhook, email, dingtalk, feishu
    config          JSONB NOT NULL,      -- {"url": "..."} or {"smtp_host":..., "to":...}
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 目标最新状态 (物化视图, 加速列表查询)
CREATE TABLE IF NOT EXISTS target_status (
    target_id       INTEGER PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    is_ok           BOOLEAN,
    last_check_at   TIMESTAMPTZ,
    last_status_code SMALLINT,
    last_latency_ms INTEGER,
    last_error      TEXT,
    last_screenshot_id BIGINT,
    last_screenshot_at TIMESTAMPTZ,
    has_anomaly     BOOLEAN DEFAULT FALSE,
    consecutive_fails INTEGER DEFAULT 0,
    ssl_valid       BOOLEAN,
    ssl_error       TEXT,
    ssl_issuer      TEXT,
    ssl_subject     TEXT,
    ssl_not_after   TIMESTAMPTZ,
    ssl_days_left   INTEGER,
    ssl_warning     TEXT,
    ssl_checked_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_target_status_is_ok ON target_status(is_ok);
CREATE INDEX IF NOT EXISTS idx_target_status_ssl_valid ON target_status(ssl_valid);
CREATE INDEX IF NOT EXISTS idx_target_status_ssl_days_left ON target_status(ssl_days_left ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_target_status_last_check_at ON target_status(last_check_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_targets_enabled_group ON targets(enabled, "group");

-- 全局系统设置
CREATE TABLE IF NOT EXISTS global_settings (
    key             TEXT PRIMARY KEY,
    value           JSONB NOT NULL
);

-- 分组并发与请求配置策略
CREATE TABLE IF NOT EXISTS group_settings (
    group_name      TEXT PRIMARY KEY,
    max_concurrency INTEGER DEFAULT 10,
    rate_limit      INTEGER DEFAULT 0,          -- 每秒最大请求数(QPS限制, 0表示不限制)
    request_timeout INTEGER DEFAULT 15,
    user_agent      TEXT,
    enabled         BOOLEAN DEFAULT TRUE,
    note            TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 兼容老数据库字段迁移
ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS rate_limit INTEGER DEFAULT 0;
