"""PDF report generation using WeasyPrint."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.config import AppConfig

logger = logging.getLogger(__name__)


def generate_pdf(html_content: str, config: AppConfig) -> Path | None:
    """Generate a PDF from HTML content.

    Returns the path to the generated PDF file, or None on failure.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        logger.error("WeasyPrint not installed. Install with: pip install weasyprint")
        return None

    output_dir = Path(config.reports.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    pdf_path = output_dir / f"property_report_{date_str}.pdf"

    try:
        HTML(string=html_content).write_pdf(str(pdf_path))
        logger.info(f"PDF report generated: {pdf_path}")
        return pdf_path
    except Exception:
        logger.exception("Failed to generate PDF report")
        return None
