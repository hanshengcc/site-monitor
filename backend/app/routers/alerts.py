"""Anomalies and Alert channels router."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from backend.app.database import get_db
from backend.app.models import Anomaly, AlertChannel
from backend.app.schemas import (
    AnomalyOut, AnomalyUpdate, AlertChannelCreate, AlertChannelOut,
)

router = APIRouter(prefix="/api", tags=["alerts"])


# ---- Anomalies ----
@router.get("/anomalies", response_model=dict)
async def list_anomalies(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    state: str = Query(None),
    target_id: int = Query(None),
    db: AsyncSession = Depends(get_db),
):
    base = select(Anomaly)
    if state:
        base = base.where(Anomaly.state == state)
    if target_id:
        base = base.where(Anomaly.target_id == target_id)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar()
    stmt = base.order_by(Anomaly.detected_at.desc()).offset((page - 1) * size).limit(size)
    rows = await db.execute(stmt)
    items = [AnomalyOut.model_validate(r) for r in rows.scalars().all()]
    return {"total": total, "items": items, "page": page, "size": size}


@router.put("/anomalies/{anomaly_id}", response_model=AnomalyOut)
async def update_anomaly(anomaly_id: int, body: AnomalyUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(Anomaly).where(Anomaly.id == anomaly_id)
    row = await db.execute(stmt)
    anomaly = row.scalar_one_or_none()
    if not anomaly:
        raise HTTPException(404, "Anomaly not found")
    anomaly.state = body.state
    if body.state == "resolved":
        anomaly.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(anomaly)
    return anomaly


# ---- Alert Channels ----
@router.get("/channels", response_model=list[AlertChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(AlertChannel).order_by(AlertChannel.id))
    return [AlertChannelOut.model_validate(r) for r in rows.scalars().all()]


@router.post("/channels", response_model=AlertChannelOut)
async def create_channel(body: AlertChannelCreate, db: AsyncSession = Depends(get_db)):
    ch = AlertChannel(**body.model_dump())
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(AlertChannel).where(AlertChannel.id == channel_id)
    row = await db.execute(stmt)
    ch = row.scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Channel not found")
    await db.delete(ch)
    await db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Send a test message through the specified channel."""
    stmt = select(AlertChannel).where(AlertChannel.id == channel_id)
    row = await db.execute(stmt)
    ch = row.scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Channel not found")

    from backend.app.alerter import (
        send_webhook, send_dingtalk, send_feishu, send_telegram,
    )

    title = "🔔 Site Monitor 测试消息"
    text = (
        "**这是一条测试消息**\n\n"
        f"渠道名称: {ch.name}\n"
        f"渠道类型: {ch.channel_type}\n"
        f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        "如果您收到此消息，说明告警渠道配置正确 ✅"
    )

    try:
        if ch.channel_type == "webhook":
            await send_webhook(ch.config["url"], {"title": title, "text": text})
        elif ch.channel_type == "dingtalk":
            await send_dingtalk(ch.config["url"], title, text)
        elif ch.channel_type == "feishu":
            await send_feishu(ch.config["url"], title, text)
        elif ch.channel_type == "telegram":
            await send_telegram(
                ch.config["bot_token"], ch.config["chat_id"], title, text,
            )
        else:
            raise HTTPException(400, f"Unknown channel type: {ch.channel_type}")
    except Exception as e:
        raise HTTPException(500, f"发送失败: {str(e)}")

    return {"ok": True, "message": "Test message sent"}
