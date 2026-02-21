"""Scraper für Flatfox.ch Immobilien."""

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


class FlatfoxScraper(BaseScraper):
    """Scraper für flatfox.ch.

    Flatfox hat eine API-basierte Struktur.
    """

    BASE_URL = 'https://flatfox.ch'
    API_URL = 'https://flatfox.ch/api/v1/public'
    MAX_PAGES = 3

    def get_name(self) -> str:
        return 'flatfox'

    def build_search_url(self, page: int = 1) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        max_price = self.criteria.get('max_price', 2200000)
        offset = (page - 1) * 20

        # Flatfox API für Kaufobjekte in Zürich
        # offer_type=SALE = Kaufobjekte (nicht Miete)
        url = (
            f"{self.BASE_URL}/de/search/"
            f"?east=8.984375&north=47.694974&south=47.159840&west=8.349609"  # Kanton ZH Bounding Box
            f"&offer_type=SALE"
            f"&min_rooms={int(min_rooms)}"
            f"&max_price={max_price}"
            f"&ordering=-created"
        )
        if page > 1:
            url += f"&offset={offset}"

        # Debug: Log der verwendeten URL
        logger.info(f"[flatfox] URL: {url}")
        return url

    def search(self) -> List[Listing]:
        """Suche über mehrere Seiten."""
        all_listings = []
        seen_ids = set()

        for page in range(1, self.MAX_PAGES + 1):
            logger.info(f"[flatfox] Lade Seite {page}...")

            page_listings = self._search_page(page)

            if not page_listings:
                logger.info(f"[flatfox] Seite {page}: keine weiteren Listings")
                break

            new_count = 0
            for listing in page_listings:
                if listing.external_id not in seen_ids:
                    seen_ids.add(listing.external_id)
                    all_listings.append(listing)
                    new_count += 1

            logger.info(f"[flatfox] Seite {page}: {new_count} neue Listings")

            if len(page_listings) < 20:
                break

            time.sleep(random.uniform(1.5, 3.0))

        logger.info(f"[flatfox] Total: {len(all_listings)} Listings")
        return all_listings

    def _search_page(self, page: int) -> List[Listing]:
        """Lädt eine einzelne Seite."""
        pw = None
        browser = None
        context = None
        try:
            url = self.build_search_url(page)
            logger.debug(f"[flatfox] Fetching {url}")

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

            page_obj.goto(url, wait_until='networkidle', timeout=60000)
            page_obj.wait_for_timeout(3000)

            content = page_obj.content()

            # Versuche JSON-Daten zu extrahieren
            try:
                # Debug: Welche JSON-Scripts gibt es?
                json_debug = page_obj.evaluate('''
                    () => {
                        const scripts = document.querySelectorAll('script[type="application/json"]');
                        const info = [];
                        for (const s of scripts) {
                            try {
                                const data = JSON.parse(s.textContent);
                                const keys = Object.keys(data).slice(0, 10);
                                info.push({keys: keys, hasResults: !!data.results, hasObjects: !!data.objects});
                            } catch(e) {
                                info.push({error: e.message});
                            }
                        }
                        return JSON.stringify(info);
                    }
                ''')
                logger.info(f"[flatfox] JSON-Scripts Debug: {json_debug}")

                self._json_data = page_obj.evaluate('''
                    () => {
                        const scripts = document.querySelectorAll('script[type="application/json"]');
                        for (const s of scripts) {
                            try {
                                const data = JSON.parse(s.textContent);
                                if (data.results || data.objects) return s.textContent;
                            } catch(e) {}
                        }
                        return null;
                    }
                ''')
                if self._json_data:
                    logger.info(f"[flatfox] JSON-Daten gefunden ({len(self._json_data)} bytes)")
                else:
                    logger.warning("[flatfox] Keine JSON-Daten mit 'results' oder 'objects' gefunden")
            except Exception as e:
                logger.warning(f"[flatfox] JSON-Extraktion Fehler: {e}")
                self._json_data = None

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
            logger.error(f"[flatfox] Seite {page} Fehler: {e}")
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
        """Parst Listings."""
        listings = []

        # Versuche JSON-Daten zu parsen
        json_data = getattr(self, '_json_data', None)
        if json_data:
            try:
                data = json.loads(json_data)
                items = data.get('results', data.get('objects', []))
                for item in items:
                    listing = self._json_to_listing(item)
                    if listing:
                        listings.append(listing)
                if listings:
                    logger.info(f"[flatfox] {len(listings)} Listings via JSON")
                    return listings
            except Exception as e:
                logger.debug(f"[flatfox] JSON-Parsing fehlgeschlagen: {e}")

        # Fallback: HTML-Parsing (ACHTUNG: HTML enthält keinen offer_type!)
        logger.warning("[flatfox] Fallback auf HTML-Parsing - offer_type kann nicht verifiziert werden!")
        soup = BeautifulSoup(content, 'lxml')

        # Flatfox verwendet listing-cards
        cards = soup.find_all('a', href=re.compile(r'/flat/'))
        if not cards:
            cards = soup.find_all('div', class_=re.compile(r'listing|result-item', re.I))

        logger.info(f"[flatfox] {len(cards)} Listing-Cards gefunden")

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.debug(f"[flatfox] Card-Parsing Fehler: {e}")

        return listings

    def _json_to_listing(self, item: dict) -> Listing:
        """Konvertiert JSON-Item zu Listing."""
        try:
            listing = Listing()

            listing.external_id = f"ff-{item.get('pk', item.get('id', ''))}"
            listing.title = item.get('title', item.get('short_title', ''))

            # Debug: Log offer_type um zu verifizieren ob SALE (Kauf) oder RENT (Miete)
            offer_type = item.get('offer_type', 'UNKNOWN')
            object_category = item.get('object_category', 'UNKNOWN')
            logger.info(f"[flatfox] Listing {listing.external_id}: offer_type={offer_type}, object_category={object_category}")

            # Preis
            listing.price = item.get('price_display', item.get('price'))
            if isinstance(listing.price, str):
                listing.price = int(re.sub(r'\D', '', listing.price) or 0)

            # Zimmer/Fläche
            listing.rooms = item.get('number_of_rooms')
            listing.area_sqm = item.get('surface_living')

            # Adresse
            address_parts = []
            if item.get('street'):
                address_parts.append(item['street'])
            if item.get('zipcode'):
                address_parts.append(str(item['zipcode']))
            if item.get('city'):
                address_parts.append(item['city'])
            listing.address = ' '.join(address_parts)
            listing.city = item.get('city', '')

            # Koordinaten
            listing.latitude = item.get('latitude')
            listing.longitude = item.get('longitude')

            # URL
            slug = item.get('slug', item.get('pk', ''))
            listing.url = f"{self.BASE_URL}/flat/{slug}/"

            # Bild
            images = item.get('images', [])
            if images:
                listing.image_url = images[0].get('url', images[0].get('listing_url', ''))

            # Typ
            obj_type = item.get('object_type', '').lower()
            if 'house' in obj_type or 'villa' in obj_type:
                listing.property_type = 'einfamilienhaus'
            else:
                listing.property_type = 'wohnung'

            return listing if listing.external_id else None

        except Exception as e:
            logger.debug(f"[flatfox] JSON-Konvertierung Fehler: {e}")
            return None

    def _parse_card(self, card) -> Listing:
        """Parst eine HTML-Card."""
        listing = Listing()

        # URL und ID
        href = card.get('href', '')
        if not href:
            link = card.find('a', href=True)
            if link:
                href = link['href']

        if href:
            # Unterstütze sowohl /flat/123 als auch /flat/some-slug/
            match = re.search(r'/flat/([^/?]+)', href)
            if match:
                listing.external_id = f"ff-{match.group(1)}"
            else:
                # Fallback: Hash der URL
                listing.external_id = f"ff-{abs(hash(href))}"
            listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

        if not listing.external_id:
            return None

        # Text extrahieren
        text = card.get_text()

        # Preis
        price_match = re.search(r"CHF\s*([\d']+)", text)
        if price_match:
            listing.price = int(price_match.group(1).replace("'", ""))

        # Zimmer
        rooms_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Zimmer|Zi)', text)
        if rooms_match:
            listing.rooms = float(rooms_match.group(1))

        # Fläche
        area_match = re.search(r'(\d+)\s*m[²2]', text)
        if area_match:
            listing.area_sqm = int(area_match.group(1))

        # Ort
        location_match = re.search(r'(\d{4})\s+(\w+)', text)
        if location_match:
            listing.address = f"{location_match.group(1)} {location_match.group(2)}"
            listing.city = location_match.group(2)

        # Titel
        title_elem = card.find(['h2', 'h3', 'h4'])
        if title_elem:
            listing.title = title_elem.get_text(strip=True)

        # Bild
        img = card.find('img', src=True)
        if img:
            listing.image_url = img['src']

        return listing
