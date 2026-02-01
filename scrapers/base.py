"""Base Scraper und Listing-Datenklasse."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
import time
import random
import logging

logger = logging.getLogger(__name__)


@dataclass
class Listing:
    """Datenklasse für ein Immobilien-Inserat."""
    external_id: str = ''
    listing_hash: str = ''
    platform: str = ''

    # Basis
    title: str = ''
    description: str = ''
    price: Optional[int] = None
    rooms: Optional[float] = None
    area_sqm: Optional[int] = None
    address: str = ''
    city: str = ''

    # Geo
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Kriterien
    parking_spots: Optional[int] = None
    outdoor_space_sqm: Optional[int] = None
    outdoor_type: str = ''
    property_type: str = ''
    ownership_type: str = ''

    # URLs
    url: str = ''
    image_url: str = ''

    # Scores
    total_score: Optional[float] = None
    location_score: Optional[float] = None
    price_score: Optional[float] = None
    features_score: Optional[float] = None
    transport_score: Optional[float] = None
    education_score: Optional[float] = None
    grade: str = ''

    # Bildung
    maturitaetsquote: Optional[float] = None
    nearest_school_distance: Optional[float] = None
    gymnasium_nearby: bool = False
    nearby_schools: Optional[list] = None

    # Meta
    travel_time_to_hb: Optional[int] = None


# Globaler Browser-Kontext (wird einmal erstellt und wiederverwendet)
_browser = None
_playwright = None


def get_browser():
    """Gibt eine globale Browser-Instanz zurück (Singleton)."""
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        logger.info("Playwright Browser gestartet (Chromium headless)")
    return _browser


def close_browser():
    """Schliesst den globalen Browser."""
    global _browser, _playwright
    if _browser:
        _browser.close()
        _browser = None
    if _playwright:
        _playwright.stop()
        _playwright = None


class BaseScraper(ABC):
    """Abstrakte Basisklasse für alle Immobilien-Scraper."""

    def __init__(self, config):
        self.config = config
        self.criteria = config.get('search_criteria', {})

    @abstractmethod
    def get_name(self) -> str:
        """Name der Plattform."""

    @abstractmethod
    def build_search_url(self) -> str:
        """Baut Such-URL mit Filtern."""

    @abstractmethod
    def parse_listings(self, content: str) -> List[Listing]:
        """Parsed alle Listings aus dem Response-Content."""

    def search(self) -> List[Listing]:
        """Führt Suche mit Playwright (echtem Browser) durch."""
        try:
            url = self.build_search_url()
            logger.info(f"[{self.get_name()}] Fetching {url}")

            browser = get_browser()
            context = browser.new_context(
                locale='de-CH',
                timezone_id='Europe/Zurich',
                viewport={'width': 1920, 'height': 1080},
                user_agent=(
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/121.0.0.0 Safari/537.36'
                ),
            )
            page = context.new_page()

            try:
                # Seite laden und auf Netzwerk-Idle warten
                page.goto(url, wait_until='networkidle', timeout=30000)

                # Zusätzliche Wartezeit für JS-Rendering
                page.wait_for_timeout(3000)

                # Cookie-Banner wegklicken falls vorhanden
                for selector in [
                    'button:has-text("Akzeptieren")',
                    'button:has-text("Accept")',
                    'button:has-text("Alle akzeptieren")',
                    'button:has-text("OK")',
                    '[id*="cookie"] button',
                    '[class*="cookie"] button',
                ]:
                    try:
                        btn = page.locator(selector).first
                        if btn.is_visible(timeout=500):
                            btn.click()
                            page.wait_for_timeout(500)
                            break
                    except Exception:
                        continue

                content = page.content()
                listings = self.parse_listings(content)

                filtered = []
                for listing in listings:
                    listing.platform = self.get_name()
                    if self.meets_criteria(listing):
                        filtered.append(listing)

                logger.info(
                    f"[{self.get_name()}] {len(listings)} geparst, "
                    f"{len(filtered)} nach Filter"
                )

                # Rate limiting
                time.sleep(random.uniform(1.0, 2.5))

                return filtered

            finally:
                context.close()

        except Exception as e:
            logger.error(f"[{self.get_name()}] Fehler: {e}")
            return []

    def meets_criteria(self, listing: Listing) -> bool:
        """Prüft ob ein Inserat die Mindestkriterien erfüllt.

        Bei fehlenden Daten wird das Listing durchgelassen,
        damit es manuell geprüft werden kann.
        """
        max_price = self.criteria.get('max_price', 2_200_000)
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_area = self.criteria.get('min_area_sqm', 120)

        if listing.price and listing.price > max_price:
            return False
        if listing.rooms and listing.rooms < min_rooms:
            return False
        if listing.area_sqm and listing.area_sqm < min_area:
            return False
        if listing.ownership_type and listing.ownership_type == 'baurecht_only':
            return False

        return True

    def _parse_price(self, text: str) -> Optional[int]:
        """Extrahiert Preis aus Text wie '1'850'000' oder 'CHF 1,850,000'."""
        if not text:
            return None
        import re
        cleaned = re.sub(r'[^\d]', '', text)
        if cleaned:
            val = int(cleaned)
            # Plausibilität: Immobilienpreise in der Schweiz
            if 100_000 <= val <= 50_000_000:
                return val
        return None

    def _parse_rooms(self, text: str) -> Optional[float]:
        """Extrahiert Zimmerzahl aus Text wie '5.5 Zimmer'."""
        if not text:
            return None
        import re
        match = re.search(r'(\d+\.?\d*)', text)
        if match:
            return float(match.group(1))
        return None

    def _parse_area(self, text: str) -> Optional[int]:
        """Extrahiert Fläche in m² aus Text."""
        if not text:
            return None
        import re
        match = re.search(r'(\d+)\s*m', text)
        if match:
            return int(match.group(1))
        return None
