"""Alert dispatcher - sends notifications via configured channels."""
import httpx
from loguru import logger
from sqlalchemy import select

from backend.app.database import AsyncSessionLocal
from backend.app.models import AlertChannel, Anomaly, Target


async def send_webhook(url: str, payload: dict):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def send_dingtalk(webhook_url: str, title: str, text: str):
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
    }
    await send_webhook(webhook_url, payload)


async def send_feishu(webhook_url: str, title: str, text: str):
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": text}],
        },
    }
    await send_webhook(webhook_url, payload)


async def send_telegram(bot_token: str, chat_id: str, title: str, text: str):
    """Send alert via Telegram Bot API.

    Telegram supports HTML formatting.
    Config requires: {"bot_token": "123:ABC...", "chat_id": "-100xxx" or "@channel"}
    """
    import re
    # Convert **bold** to <b>bold</b> for Telegram HTML
    html_body = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    message = f"<b>{title}</b>\n\n{html_body}"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def format_alert_message(target: Target, anomaly: Anomaly) -> tuple[str, str]:
    """Format alert title and body."""
    title = f"🚨 站点异常: {target.name or target.url}"
    reasons_text = "\n".join(
        f"  - **{r['rule']}**: {r['detail']}" for r in (anomaly.reasons or [])
    )
    text = (
        f"**站点**: {target.name or ''} ({target.url})\n"
        f"**分组**: {target.group}\n"
        f"**异常类型**: {anomaly.anomaly_type}\n"
        f"**评分**: {anomaly.score}\n"
        f"**原因**:\n{reasons_text}\n"
        f"**时间**: {anomaly.detected_at}\n"
    )
    return title, text


def format_recovery_message(target: Target, anomaly: Anomaly) -> tuple[str, str]:
    """Format recovery notification title and body."""
    title = f"✅ 站点已恢复: {target.name or target.url}"
    text = (
        f"**站点**: {target.name or ''} ({target.url})\n"
        f"**分组**: {target.group}\n"
        f"**状态**: 站点已重新恢复正常访问\n"
        f"**之前异常**: {anomaly.anomaly_type}\n"
        f"**恢复时间**: {anomaly.resolved_at or '刚刚'}\n"
    )
    return title, text


async def dispatch_alerts():
    """Find un-notified anomalies (both open alerts and recovery alerts) and send notifications."""
    async with AsyncSessionLocal() as session:
        # Get enabled channels
        ch_rows = await session.execute(
            select(AlertChannel).where(AlertChannel.enabled == True)
        )
        channels = ch_rows.scalars().all()
        if not channels:
            return

        # Get un-notified anomalies (both newly opened and newly resolved)
        stmt = (
            select(Anomaly)
            .where(Anomaly.notified == False)
            .order_by(Anomaly.detected_at.desc())
            .limit(100)
        )
        rows = await session.execute(stmt)
        anomalies = rows.scalars().all()
        if not anomalies:
            return

        # Fetch related targets
        target_ids = {a.target_id for a in anomalies}
        t_rows = await session.execute(select(Target).where(Target.id.in_(target_ids)))
        target_map = {t.id: t for t in t_rows.scalars().all()}

        for anomaly in anomalies:
            target = target_map.get(anomaly.target_id)
            if not target:
                anomaly.notified = True
                continue

            if anomaly.state == "resolved":
                title, text = format_recovery_message(target, anomaly)
            else:
                title, text = format_alert_message(target, anomaly)

            for ch in channels:
                try:
                    if ch.channel_type == "webhook":
                        await send_webhook(ch.config["url"], {
                            "title": title, "text": text,
                            "target_url": target.url,
                            "anomaly_type": anomaly.anomaly_type,
                            "score": anomaly.score,
                            "state": anomaly.state,
                        })
                    elif ch.channel_type == "dingtalk":
                        await send_dingtalk(ch.config["url"], title, text)
                    elif ch.channel_type == "feishu":
                        await send_feishu(ch.config["url"], title, text)
                    elif ch.channel_type == "telegram":
                        await send_telegram(
                            ch.config["bot_token"],
                            ch.config["chat_id"],
                            title, text,
                        )
                    else:
                        logger.warning(f"Unknown channel type: {ch.channel_type}")
                except Exception as e:
                    logger.error(f"Alert send failed [{ch.name}]: {e}")

            anomaly.notified = True

        await session.commit()
        logger.info(f"Dispatched alerts/recoveries for {len(anomalies)} anomalies")
