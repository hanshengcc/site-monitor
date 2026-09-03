"""Screenshot anomaly analyzer - multi-signal detection."""
import numpy as np
from PIL import Image
from loguru import logger


# 错误页面关键字 (中英文常见)
ERROR_KEYWORDS = [
    # HTTP errors
    "502 bad gateway", "503 service", "504 gateway", "500 internal server error",
    "404 not found", "403 forbidden", "401 unauthorized",
    "bad gateway", "service unavailable", "gateway timeout",
    # Server error pages
    "application error", "server error", "an error occurred",
    "this page isn't working", "this site can't be reached",
    "err_connection_refused", "err_name_not_resolved",
    "nginx error", "apache error", "iis error",
    # Chinese common
    "域名过期", "未备案", "该页面不存在", "网站维护中", "暂时无法访问",
    "页面不存在", "系统错误", "服务器错误", "网站已关闭",
    "该内容已下架", "访问被拒绝", "域名未绑定",
    # Parking / default pages
    "domain for sale", "this domain", "buy this domain",
    "website is under construction", "coming soon",
    "index of /",
]


def analyze_screenshot(
    image_path: str,
    page_title: str = "",
    console_errors: int = 0,
    failed_requests: int = 0,
    dom_text_length: int = 0,
) -> dict:
    """
    Analyze a screenshot for rendering anomalies.

    Returns:
        {
            "is_anomaly": bool,
            "score": float (0-100),
            "reasons": [{"rule": str, "detail": str, "weight": float}, ...]
        }
    """
    reasons = []
    score = 0.0

    # ---- Rule 1: Page title keyword check ----
    title_lower = page_title.lower().strip()
    for kw in ERROR_KEYWORDS:
        if kw in title_lower:
            reasons.append({"rule": "keyword_in_title", "detail": f"Title contains '{kw}'", "weight": 40})
            score += 40
            break

    # ---- Rule 2: DOM text too short (empty page / broken render) ----
    if dom_text_length < 50:
        reasons.append({"rule": "empty_page", "detail": f"DOM text length={dom_text_length} < 50", "weight": 30})
        score += 30
    elif dom_text_length < 200:
        reasons.append({"rule": "sparse_page", "detail": f"DOM text length={dom_text_length} < 200", "weight": 10})
        score += 10

    # ---- Rule 3: Too many console errors ----
    if console_errors > 10:
        w = min(20, console_errors)
        reasons.append({"rule": "console_errors", "detail": f"{console_errors} console errors", "weight": w})
        score += w

    # ---- Rule 4: High failed request ratio ----
    if failed_requests > 5:
        w = min(15, failed_requests * 2)
        reasons.append({"rule": "failed_requests", "detail": f"{failed_requests} failed requests", "weight": w})
        score += w

    # ---- Rule 5: White screen / solid color detection ----
    try:
        img = Image.open(image_path).convert("RGB")
        arr = np.array(img, dtype=np.float32)

        # Overall pixel standard deviation
        pixel_std = arr.std()
        if pixel_std < 5:
            reasons.append({"rule": "solid_color", "detail": f"Pixel std={pixel_std:.1f} (nearly solid color)", "weight": 50})
            score += 50
        elif pixel_std < 15:
            reasons.append({"rule": "low_contrast", "detail": f"Pixel std={pixel_std:.1f} (very low contrast)", "weight": 20})
            score += 20

        # Check if >90% of pixels are very similar (white/black screen)
        mean_color = arr.mean(axis=(0, 1))
        dist = np.sqrt(((arr - mean_color) ** 2).sum(axis=2))
        uniform_ratio = (dist < 10).mean()
        if uniform_ratio > 0.95:
            reasons.append({
                "rule": "uniform_screen",
                "detail": f"{uniform_ratio:.1%} pixels near mean color (RGB={mean_color.astype(int).tolist()})",
                "weight": 40,
            })
            score += 40

    except Exception as e:
        logger.warning(f"Image analysis failed for {image_path}: {e}")
        reasons.append({"rule": "analysis_error", "detail": str(e), "weight": 10})
        score += 10

    # Cap score
    score = min(100, score)
    is_anomaly = score >= 30

    return {
        "is_anomaly": is_anomaly,
        "score": round(score, 1),
        "reasons": reasons,
    }
