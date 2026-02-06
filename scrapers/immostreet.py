"""Scraper für ImmoStreet.ch."""

import re
import json
import logging
import time
import random
import platform
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class ImmoStreetScraper(BaseScraper):
    """Scraper für immostreet.ch.

    Immobilienportal mit Fokus auf Deutschschweiz.
    """

    BASE_URL = 'https://www.immostreet.ch'
    MAX_PAGES = 3

    def get_name(self) -> str:
        return 'immostreet'

    def build_search_url(self, page: int = 1) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        max_price = self.criteria.get('max_price', 2200000)

        # ImmoStreet URL für Kaufobjekte in Zürich
        url = (
            f"{self.BASE_URL}/de/immobilien/kaufen/wohnung-haus/kanton-zuerich"
            f"?rooms={int(min_rooms)}"
            f"&price_max={max_price}"
            f"&sort=date_desc"
        )
        if page > 1:
            url += f"&page={page}"
        return url

    def search(self) -> List[Listing]:
        """Suche über mehrere Seiten."""
        all_listings = []
        seen_ids = set()

        for page in range(1, self.MAX_PAGES + 1):
            logger.info(f"[immostreet] Lade Seite {page}...")

            page_listings = self._search_page(page)

            if not page_listings:
                logger.info(f"[immostreet] Seite {page}: keine weiteren Listings")
                break

            new_count = 0
            for listing in page_listings:
                if listing.external_id not in seen_ids:
                    seen_ids.add(listing.external_id)
                    all_listings.append(listing)
                    new_count += 1

            logger.info(f"[immostreet] Seite {page}: {new_count} neue Listings")

            if len(page_listings) < 20:
                break

            time.sleep(random.uniform(2.0, 4.0))

        logger.info(f"[immostreet] Total: {len(all_listings)} Listings")
        return all_listings

    def _search_page(self, page: int) -> List[Listing]:
        """Lädt eine einzelne Seite."""
        pw = None
        browser = None
        context = None
        try:
            url = self.build_search_url(page)
            logger.debug(f"[immostreet] Fetching {url}")

            from playwright.sync_api import sync_playwright

            try:
                from playwright_stealth import stealth_sync
                has_stealth = True
            except ImportError:
                has_stealth = False

            pw = sync_playwright().start()

            use_firefox = platform.system() == 'Darwin'

            if use_firefox:
                browser = pw.firefox.launch(headless=True)
            else:
                launch_args = [
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-features=AsyncDns',
                ]
                browser = pw.chromium.launch(headless=True, args=launch_args)

            context = browser.new_context(
                locale='de-CH',
                timezone_id='Europe/Zurich',
                viewport={'width': 1920, 'height': 1080},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                ),
            )
            page_obj = context.new_page()

            if has_stealth:
                stealth_sync(page_obj)

            page_obj.goto(url, wait_until='domcontentloaded', timeout=60000)
            page_obj.wait_for_timeout(4000)

            content = page_obj.content()

            context.close()
            context = None

            listings = self.parse_listings(content)

            filtered = []
            for listing in listings:
                listing.platform = self.get_name()
                if self.meets_criteria(listing):
                    filtered.append(listing)

            return filtered

        except Exception as e:
            logger.error(f"[immostreet] Seite {page} Fehler: {e}")
            return []
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass

    def parse_listings(self, content: str) -> List[Listing]:
        """Parst Listings aus HTML."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Suche nach Listing-Cards
        cards = soup.find_all('article', class_=re.compile(r'listing|property|result', re.I))
        if not cards:
            cards = soup.find_all('div', class_=re.compile(r'listing-card|property-item|search-result', re.I))
        if not cards:
            cards = soup.find_all('a', href=re.compile(r'/immobilien/|/property/|/objekt/'))

        logger.info(f"[immostreet] {len(cards)} Listing-Cards gefunden")

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.debug(f"[immostreet] Card-Parsing Fehler: {e}")

        return listings

    def _parse_card(self, card) -> Listing:
        """Parst eine Listing-Card."""
        listing = Listing()

        # URL und ID
        link = card.find('a', href=True) if card.name != 'a' else card
        if link and link.get('href'):
            href = link['href']
            listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

            # ID aus URL
            match = re.search(r'/(\d+)(?:/|$|\?)', href)
            if match:
                listing.external_id = f"is-{match.group(1)}"
            else:
                match = re.search(r'id[=_](\d+)', href, re.I)
                if match:
                    listing.external_id = f"is-{match.group(1)}"
                else:
                    listing.external_id = f"is-{hash(href) % 100000}"

        if not listing.external_id:
            return None

        text = card.get_text()

        # Titel
        title_elem = card.find(['h1', 'h2', 'h3', 'h4'])
        if title_elem:
            listing.title = title_elem.get_text(strip=True)

        # Preis
        price_match = re.search(r"CHF\s*([\d']+)", text)
        if price_match:
            listing.price = int(price_match.group(1).replace("'", ""))

        # Zimmer
        rooms_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Zimmer|Zi\.?)', text, re.I)
        if rooms_match:
            listing.rooms = float(rooms_match.group(1))

        # Fläche
        area_match = re.search(r'(\d+)\s*m[²2]', text)
        if area_match:
            listing.area_sqm = int(area_match.group(1))

        # Ort
        location_match = re.search(r'(\d{4})\s+([A-ZÄÖÜa-zäöü\-]+)', text)
        if location_match:
            listing.address = f"{location_match.group(1)} {location_match.group(2)}"
            listing.city = location_match.group(2)

        # Bild
        img = card.find('img', src=True)
        if img:
            src = img.get('src') or img.get('data-src', '')
            if src:
                listing.image_url = src if src.startswith('http') else f"{self.BASE_URL}{src}"

        # Typ
        if 'haus' in text.lower() or 'villa' in text.lower():
            listing.property_type = 'einfamilienhaus'
        else:
            listing.property_type = 'wohnung'

        return listing
