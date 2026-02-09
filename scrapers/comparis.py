"""Scraper für Comparis.ch Immobilien."""

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


class ComparisScraper(BaseScraper):
    """Scraper für comparis.ch Immobilien.

    Extrahiert Listing-Daten via Playwright Browser-Automatisierung.
    """

    BASE_URL = 'https://www.comparis.ch'
    MAX_PAGES = 3

    def get_name(self) -> str:
        return 'comparis'

    def build_search_url(self, page: int = 1) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        max_price = self.criteria.get('max_price', 2200000)

        # Comparis URL-Struktur für Kaufobjekte in Zürich
        url = (
            f"{self.BASE_URL}/immobilien/result/list"
            f"?requestobject=%7B%22DealType%22%3A%2220%22%2C"  # 20 = Kaufen
            f"%22SiteId%22%3A%220%22%2C"
            f"%22RootPropertyTypes%22%3A%5B%5D%2C"
            f"%22PropertyTypes%22%3A%5B%5D%2C"
            f"%22RoomsFrom%22%3A%22{int(min_rooms)}%22%2C"
            f"%22RoomsTo%22%3Anull%2C"
            f"%22FloorSearchType%22%3A%220%22%2C"
            f"%22LivingSpaceFrom%22%3Anull%2C"
            f"%22LivingSpaceTo%22%3Anull%2C"
            f"%22PriceFrom%22%3Anull%2C"
            f"%22PriceTo%22%3A%22{max_price}%22%2C"
            f"%22ComparisPointsMin%22%3A%220%22%2C"
            f"%22AdAgeMax%22%3A%220%22%2C"
            f"%22AdAgeInHoursMax%22%3Anull%2C"
            f"%22Keyword%22%3A%22%22%2C"
            f"%22WithImagesOnly%22%3Anull%2C"
            f"%22WithPointsOnly%22%3Anull%2C"
            f"%22Radius%22%3Anull%2C"
            f"%22MinAvailableDate%22%3A%221753-01-01%22%2C"
            f"%22MinChangeDate%22%3A%221753-01-01%22%2C"
            f"%22LocationSearchString%22%3A%22Kanton%20Z%C3%BCrich%22%2C"
            f"%22Sort%22%3A%225%22%2C"  # 5 = Neueste zuerst
            f"%22HasBalcony%22%3Afalse%2C"
            f"%22HasTerrace%22%3Afalse%2C"
            f"%22HasFireplace%22%3Afalse%2C"
            f"%22HasDishwasher%22%3Afalse%2C"
            f"%22HasWashingMachine%22%3Afalse%2C"
            f"%22HasLift%22%3Afalse%2C"
            f"%22HasParking%22%3Afalse%2C"
            f"%22PetsAllowed%22%3Afalse%2C"
            f"%22MinersStandard%22%3Anull%2C"
            f"%22Page%22%3A%22{page}%22%7D"
        )
        return url

    def search(self) -> List[Listing]:
        """Überschreibt die Basis-Suche um mehrere Seiten zu laden."""
        all_listings = []
        seen_ids = set()

        for page in range(1, self.MAX_PAGES + 1):
            logger.info(f"[comparis] Lade Seite {page}...")

            page_listings = self._search_page(page)

            if not page_listings:
                logger.info(f"[comparis] Seite {page}: keine weiteren Listings")
                break

            new_count = 0
            for listing in page_listings:
                if listing.external_id not in seen_ids:
                    seen_ids.add(listing.external_id)
                    all_listings.append(listing)
                    new_count += 1

            logger.info(f"[comparis] Seite {page}: {new_count} neue Listings")

            if len(page_listings) < 20:
                break

            time.sleep(random.uniform(2.0, 4.0))

        logger.info(f"[comparis] Total: {len(all_listings)} Listings")
        return all_listings

    def _search_page(self, page: int) -> List[Listing]:
        """Lädt eine einzelne Seite."""
        pw = None
        browser = None
        context = None
        try:
            url = self.build_search_url(page)
            logger.debug(f"[comparis] Fetching {url}")

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
            page_obj.wait_for_timeout(3000)

            # Scroll durch die Seite um Lazy Loading zu triggern
            for i in range(5):
                page_obj.evaluate(f'window.scrollTo(0, {(i + 1) * 500})')
                page_obj.wait_for_timeout(800)

            # Zurück nach oben und nochmal warten
            page_obj.evaluate('window.scrollTo(0, 0)')
            page_obj.wait_for_timeout(1000)

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
            logger.error(f"[comparis] Seite {page} Fehler: {e}")
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

        # Comparis nutzt data-testid für Listing-Cards
        cards = soup.find_all('div', {'data-testid': re.compile(r'result-list-item')})
        if not cards:
            # Fallback: Suche nach Listing-Containern
            cards = soup.find_all('a', href=re.compile(r'/immobilien/marktplatz/details/show/'))

        logger.info(f"[comparis] {len(cards)} Listing-Cards gefunden")

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.debug(f"[comparis] Card-Parsing Fehler: {e}")

        return listings

    def _parse_card(self, card) -> Listing:
        """Parst eine einzelne Listing-Card."""
        listing = Listing()

        # External ID aus href extrahieren
        link = card.find('a', href=re.compile(r'/immobilien/'))
        if not link:
            link = card if card.name == 'a' else None

        if link and link.get('href'):
            href = link['href']
            # ID aus URL extrahieren
            match = re.search(r'/show/(\d+)', href)
            if match:
                listing.external_id = f"cp-{match.group(1)}"
                listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

        if not listing.external_id:
            return None

        # Titel
        title_elem = card.find(['h2', 'h3', 'span'], class_=re.compile(r'title|heading', re.I))
        if title_elem:
            listing.title = title_elem.get_text(strip=True)

        # Preis
        price_elem = card.find(string=re.compile(r"CHF|Fr\.|'"))
        if price_elem:
            price_text = price_elem.get_text() if hasattr(price_elem, 'get_text') else str(price_elem)
            price_match = re.search(r"[\d']+", price_text.replace(' ', ''))
            if price_match:
                listing.price = int(price_match.group().replace("'", ""))

        # Zimmer und Fläche
        text_content = card.get_text()
        rooms_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Zimmer|Zi\.)', text_content)
        if rooms_match:
            listing.rooms = float(rooms_match.group(1))

        area_match = re.search(r'(\d+)\s*m[²2]', text_content)
        if area_match:
            listing.area_sqm = int(area_match.group(1))

        # Adresse/Ort
        location_elem = card.find(string=re.compile(r'\d{4}\s+\w+'))
        if location_elem:
            loc_text = location_elem.get_text() if hasattr(location_elem, 'get_text') else str(location_elem)
            listing.address = loc_text.strip()
            # Stadt extrahieren
            city_match = re.search(r'\d{4}\s+(.+)', loc_text)
            if city_match:
                listing.city = city_match.group(1).strip()

        # Bild - verschiedene Methoden probieren
        img_url = None

        # 1. Suche nach picture/source Element (moderne Lazy Loading)
        picture = card.find('picture')
        if picture:
            source = picture.find('source')
            if source:
                srcset = source.get('srcset', '')
                if srcset and 'data:image' not in srcset:
                    img_url = srcset.split(',')[0].split()[0]

        # 2. Normales img Element
        if not img_url:
            img = card.find('img')
            if img:
                # Prüfe verschiedene Attribute
                for attr in ['src', 'data-src', 'data-lazy', 'data-original', 'data-lazy-src']:
                    val = img.get(attr, '')
                    if val and 'data:image' not in val and 'placeholder' not in val.lower():
                        img_url = val
                        break

                # Srcset als Fallback
                if not img_url:
                    srcset = img.get('srcset', '')
                    if srcset and 'data:image' not in srcset:
                        img_url = srcset.split(',')[0].split()[0]

        # 3. Fallback: Style mit background-image
        if not img_url:
            for elem in card.find_all(style=True):
                style = elem.get('style', '')
                bg_match = re.search(r'background-image:\s*url\([\'"]?([^\'")\s]+)[\'"]?\)', style)
                if bg_match:
                    img_url = bg_match.group(1)
                    break

        # URL normalisieren
        if img_url:
            if img_url.startswith('//'):
                img_url = f"https:{img_url}"
            elif img_url.startswith('/'):
                img_url = f"{self.BASE_URL}{img_url}"
            listing.image_url = img_url

        return listing
