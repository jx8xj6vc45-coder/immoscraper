"""Scraper für Walde & Partner Immobilien."""

import re
import logging
import time
import random
import platform
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class WaldeScraper(BaseScraper):
    """Scraper für walde.ch - Zürcher Immobilienmakler."""

    BASE_URL = 'https://www.walde.ch'
    MAX_PAGES = 3

    def get_name(self) -> str:
        return 'walde'

    def build_search_url(self, page: int = 1) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        max_price = self.criteria.get('max_price', 2200000)

        # Walde Suche für Kaufobjekte
        url = (
            f"{self.BASE_URL}/immobilien/kaufen"
            f"?rooms_from={int(min_rooms)}"
            f"&price_to={max_price}"
            f"&page={page}"
        )
        return url

    def search(self) -> List[Listing]:
        """Suche über mehrere Seiten."""
        all_listings = []
        seen_ids = set()

        for page in range(1, self.MAX_PAGES + 1):
            logger.info(f"[walde] Lade Seite {page}...")

            page_listings = self._search_page(page)

            if not page_listings:
                logger.info(f"[walde] Seite {page}: keine weiteren Listings")
                break

            new_count = 0
            for listing in page_listings:
                if listing.external_id not in seen_ids:
                    seen_ids.add(listing.external_id)
                    all_listings.append(listing)
                    new_count += 1

            logger.info(f"[walde] Seite {page}: {new_count} neue Listings")

            if len(page_listings) < 12:
                break

            time.sleep(random.uniform(2.0, 4.0))

        logger.info(f"[walde] Total: {len(all_listings)} Listings")
        return all_listings

    def _search_page(self, page: int) -> List[Listing]:
        """Lädt eine einzelne Seite."""
        pw = None
        browser = None
        context = None
        try:
            url = self.build_search_url(page)
            logger.debug(f"[walde] Fetching {url}")

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

            # Scroll um Lazy Loading zu triggern
            page_obj.evaluate('window.scrollTo(0, document.body.scrollHeight / 2)')
            page_obj.wait_for_timeout(2000)

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
            logger.error(f"[walde] Seite {page} Fehler: {e}")
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
        """Parst Listings aus dem HTML-Content."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Walde verwendet verschiedene Card-Strukturen
        cards = soup.find_all('a', href=re.compile(r'/immobilien/\d+'))
        if not cards:
            cards = soup.find_all('article', class_=re.compile(r'property|listing', re.I))
        if not cards:
            cards = soup.find_all('div', class_=re.compile(r'property|listing|result', re.I))

        logger.info(f"[walde] {len(cards)} Listing-Cards gefunden")

        seen_urls = set()
        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    listings.append(listing)
            except Exception as e:
                logger.debug(f"[walde] Card-Parsing Fehler: {e}")

        return listings

    def _parse_card(self, card) -> Listing:
        """Parst eine einzelne Listing-Card."""
        listing = Listing()

        # URL und ID extrahieren
        if card.name == 'a':
            href = card.get('href', '')
        else:
            link = card.find('a', href=re.compile(r'/immobilien/'))
            href = link.get('href', '') if link else ''

        if not href:
            return None

        if href.startswith('/'):
            listing.url = f"{self.BASE_URL}{href}"
        else:
            listing.url = href

        # ID aus URL extrahieren
        id_match = re.search(r'/immobilien/(\d+)', href)
        if id_match:
            listing.external_id = f"walde-{id_match.group(1)}"
        else:
            listing.external_id = f"walde-{abs(hash(href))}"

        # Container für Textsuche
        container = card if card.name != 'a' else card

        text = container.get_text(' ', strip=True)

        # Titel
        title_elem = container.find(['h2', 'h3', 'h4'], class_=re.compile(r'title|name', re.I))
        if not title_elem:
            title_elem = container.find(['h2', 'h3', 'h4'])
        if title_elem:
            listing.title = title_elem.get_text(strip=True)

        # Preis
        price_match = re.search(r"(?:CHF|Fr\.?)\s*([\d'.,]+)", text)
        if price_match:
            listing.price = self._parse_price(price_match.group(1))

        # Zimmer
        rooms_match = re.search(r'(\d+\.?\d*)\s*(?:Zimmer|Zi\.|rooms)', text, re.IGNORECASE)
        if rooms_match:
            listing.rooms = float(rooms_match.group(1))

        # Fläche
        area_match = re.search(r'(\d+)\s*m[²2]', text)
        if area_match:
            listing.area_sqm = int(area_match.group(1))

        # Ort
        plz_match = re.search(r'(\d{4})\s+([A-ZÄÖÜ][a-zäöüéèê]+)', text)
        if plz_match:
            listing.city = plz_match.group(2)
            listing.address = f"{plz_match.group(1)} {plz_match.group(2)}"

        # Bild
        img = container.find('img')
        if img:
            img_url = (
                img.get('src') or
                img.get('data-src') or
                img.get('data-lazy-src') or
                ''
            )
            if not img_url or 'placeholder' in img_url.lower():
                srcset = img.get('srcset', '')
                if srcset:
                    img_url = srcset.split(',')[0].split()[0]

            if img_url and not img_url.startswith('data:'):
                if img_url.startswith('//'):
                    img_url = f"https:{img_url}"
                elif img_url.startswith('/'):
                    img_url = f"{self.BASE_URL}{img_url}"
                listing.image_url = img_url

        # Fallback: background-image
        if not listing.image_url:
            for elem in container.find_all(style=True):
                style = elem.get('style', '')
                bg_match = re.search(r'background-image:\s*url\([\'"]?([^\'")\s]+)[\'"]?\)', style)
                if bg_match:
                    img_url = bg_match.group(1)
                    if img_url.startswith('/'):
                        img_url = f"{self.BASE_URL}{img_url}"
                    listing.image_url = img_url
                    break

        # Typ
        listing.property_type = 'wohnung'
        if listing.title:
            title_lower = listing.title.lower()
            if any(w in title_lower for w in ['haus', 'villa', 'reihenhaus', 'doppelhaus', 'house']):
                listing.property_type = 'einfamilienhaus'

        return listing

    def _parse_price(self, price_str: str) -> int:
        """Parst einen Preis-String zu Integer."""
        cleaned = re.sub(r"[^\d]", "", price_str)
        return int(cleaned) if cleaned else 0
