"""CAPTCHA solving integration (2Captcha)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


async def solve_captcha(site_key: str, page_url: str) -> str | None:
    """Solve a reCAPTCHA using 2Captcha service.

    Returns the solution token or None if solving fails.
    """
    api_key = os.environ.get("TWOCAPTCHA_API_KEY")
    if not api_key:
        logger.warning("No 2Captcha API key configured, cannot solve CAPTCHA")
        return None

    try:
        from twocaptcha import TwoCaptcha

        solver = TwoCaptcha(api_key)
        result = solver.recaptcha(sitekey=site_key, url=page_url)
        token = result.get("code")
        logger.info("CAPTCHA solved successfully")
        return token
    except ImportError:
        logger.error("twocaptcha-python not installed. Install with: pip install twocaptcha-python")
        return None
    except Exception:
        logger.exception("Failed to solve CAPTCHA")
        return None
