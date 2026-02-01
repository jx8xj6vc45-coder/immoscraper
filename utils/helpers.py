"""Hilfsfunktionen."""

import logging
import sys


def setup_logging(level=logging.INFO):
    """Konfiguriert Logging für die Applikation."""
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Externe Libraries leiser stellen
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('geopy').setLevel(logging.WARNING)


def format_price(price: int) -> str:
    """Formatiert Preis als CHF-String: 1'850'000."""
    if not price:
        return 'N/A'
    s = str(price)
    groups = []
    while s:
        groups.append(s[-3:])
        s = s[:-3]
    return "CHF " + "'".join(reversed(groups))


def format_score_bar(score: float, max_score: float = 105) -> str:
    """Erstellt eine visuelle Score-Leiste."""
    pct = min(score / max_score, 1.0)
    filled = int(pct * 20)
    return f"[{'█' * filled}{'░' * (20 - filled)}] {score:.0f}/{max_score:.0f}"
