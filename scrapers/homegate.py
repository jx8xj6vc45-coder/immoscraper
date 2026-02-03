"""Scraper für Homegate.ch."""

import re
import json
import logging
import time
import random
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class HomegateScraper(BaseScraper):
    """Scraper für homegate.ch.

    Extrahiert Listing-Daten aus dem window.__INITIAL_STATE__ JSON-Blob.
    Gleiche Datenstruktur wie ImmoScout24.ch.
    Unterstützt Pagination (mehrere Seiten).
    """

    BASE_URL = 'https://www.homegate.ch'
    MAX_PAGES = 5  # Maximal 5 Seiten laden (ca. 100 Listings)

    def get_name(self) -> str:
        return 'homegate'

    def build_search_url(self, page: int = 1) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_rooms_int = int(min_rooms)

        url = (
            f"{self.BASE_URL}/buy/real-estate/canton-zurich/matching-list"
            f"?ac={min_rooms_int}"
        )
        if page > 1:
            url += f"&ep={page}"
        return url

    def search(self) -> List[Listing]:
        """Überschreibt die Basis-Suche um mehrere Seiten zu laden."""
        all_listings = []
        seen_ids = set()

        for page in range(1, self.MAX_PAGES + 1):
            logger.info(f"[homegate] Lade Seite {page}...")

            # Führe die Basis-Suche für diese Seite aus
            page_listings = self._search_page(page)

            if not page_listings:
                logger.info(f"[homegate] Seite {page}: keine weiteren Listings")
                break

            # Deduplizierung
            new_count = 0
            for listing in page_listings:
                if listing.external_id not in seen_ids:
                    seen_ids.add(listing.external_id)
                    all_listings.append(listing)
                    new_count += 1

            logger.info(f"[homegate] Seite {page}: {new_count} neue Listings")

            # Wenn weniger als 20 Listings, sind wir am Ende
            if len(page_listings) < 20:
                break

            # Rate limiting zwischen Seiten
            time.sleep(random.uniform(2.0, 4.0))

        logger.info(f"[homegate] Total: {len(all_listings)} Listings von {page} Seiten")
        return all_listings

    def _search_page(self, page: int) -> List[Listing]:
        """Lädt eine einzelne Seite und gibt die Listings zurück."""
        pw = None
        browser = None
        try:
            url = self.build_search_url(page)
            logger.debug(f"[homegate] Fetching {url}")

            from playwright.sync_api import sync_playwright
            try:
                from playwright_stealth import stealth_sync
                has_stealth = True
            except ImportError:
                has_stealth = False

            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )

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

            page_obj.goto(url, wait_until='networkidle', timeout=45000)
            page_obj.wait_for_timeout(3000)

            # JS-State extrahieren
            try:
                js_state = page_obj.evaluate(
                    '() => { try { return JSON.stringify(window.__INITIAL_STATE__); } catch(e) { return null; } }'
                )
                if js_state and js_state != 'null':
                    self._js_initial_state = js_state
            except Exception:
                self._js_initial_state = None

            content = page_obj.content()
            listings = self.parse_listings(content)

            # Filter anwenden
            filtered = []
            for listing in listings:
                listing.platform = self.get_name()
                if self.meets_criteria(listing):
                    filtered.append(listing)

            context.close()
            return filtered

        except Exception as e:
            logger.error(f"[homegate] Seite {page} Fehler: {e}")
            return []
        finally:
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
        listings = []

        # Strategie 0: JS-evaluierter __INITIAL_STATE__ (direkt aus Browser)
        js_state = getattr(self, '_js_initial_state', None)
        if js_state:
            try:
                data = json.loads(js_state)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[homegate] {len(result_listings)} Listings via JS evaluate")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[homegate] JS evaluate fehlgeschlagen: {e}")

        # Strategie 0b: JS-evaluierter __NEXT_DATA__
        js_next = getattr(self, '_js_next_data', None)
        if js_next:
            try:
                data = json.loads(js_next)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[homegate] {len(result_listings)} Listings via JS __NEXT_DATA__")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[homegate] JS __NEXT_DATA__ fehlgeschlagen: {e}")

        # Strategie 1: __INITIAL_STATE__ via Regex auf Raw-HTML
        listings.extend(self._parse_initial_state(content))

        # Strategie 2: __NEXT_DATA__ (Next.js) als Fallback
        if not listings:
            listings.extend(self._parse_next_data(content))

        # Strategie 3: JSON-Blob Suche
        if not listings:
            listings.extend(self._parse_via_evaluate(content))

        return listings

    def _extract_json_from_html(self, content: str, var_name: str) -> dict:
        """Extrahiert JSON aus einem window.VAR_NAME=... Pattern im HTML."""
        pattern = re.escape(var_name) + r'\s*=\s*(\{.+?\})\s*;?\s*</script>'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        pattern2 = re.escape(var_name) + r'\s*=\s*(\{.*?\})\s*;\s*$'
        match2 = re.search(pattern2, content, re.DOTALL | re.MULTILINE)
        if match2:
            return json.loads(match2.group(1))

        return None

    def _parse_initial_state(self, content: str) -> List[Listing]:
        """Extrahiert Listings aus window.__INITIAL_STATE__ JSON."""
        listings = []

        data = self._extract_json_from_html(content, 'window.__INITIAL_STATE__')
        if not data:
            soup = BeautifulSoup(content, 'lxml')
            for tag in soup.find_all('script'):
                text = tag.string or tag.get_text()
                if text and '__INITIAL_STATE__' in text:
                    try:
                        idx = text.index('__INITIAL_STATE__')
                        eq_idx = text.index('=', idx)
                        json_text = text[eq_idx + 1:].strip().rstrip(';')
                        data = json.loads(json_text)
                        break
                    except (ValueError, json.JSONDecodeError):
                        continue

        if not data:
            logger.warning("[homegate] Kein __INITIAL_STATE__ gefunden")
            return listings

        result_listings = self._find_listings_in_json(data)
        if not result_listings:
            logger.warning("[homegate] Keine Listings im JSON gefunden")
            return listings

        for item in result_listings:
            listing = self._item_to_listing(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_next_data(self, content: str) -> List[Listing]:
        """Fallback: Extrahiert aus __NEXT_DATA__ (Next.js)."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')
        tag = soup.find('script', id='__NEXT_DATA__')
        if not tag:
            return listings

        try:
            text = tag.string or tag.get_text()
            data = json.loads(text)
        except (json.JSONDecodeError, AttributeError):
            return listings

        result_listings = self._find_listings_in_json(data)
        if result_listings:
            logger.info(f"[homegate] {len(result_listings)} Listings via __NEXT_DATA__")
            for item in result_listings:
                listing = self._item_to_listing(item)
                if listing:
                    listings.append(listing)

        return listings

    def _parse_via_evaluate(self, content: str) -> List[Listing]:
        """Fallback: Suche nach JSON-Blobs die Listing-Daten enthalten."""
        listings = []

        for match in re.finditer(r'(\{"resultList":\{.*?\})\s*;?\s*</script>', content, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except json.JSONDecodeError:
                continue

        return listings

    def _find_listings_in_json(self, data: dict) -> list:
        """Findet Listings-Array in verschachteltem JSON."""
        try:
            return data['resultList']['search']['fullSearch']['result']['listings']
        except (KeyError, TypeError):
            pass

        for path in [
            ['props', 'pageProps', 'resultList', 'search', 'fullSearch', 'result', 'listings'],
            ['props', 'pageProps', 'listings'],
            ['props', 'pageProps', 'searchResult', 'listings'],
            ['searchResult', 'listings'],
            ['data', 'searchResult', 'listings'],
        ]:
            obj = data
            try:
                for key in path:
                    obj = obj[key]
                if isinstance(obj, list) and len(obj) > 0:
                    return obj
            except (KeyError, TypeError):
                continue

        return self._find_key_recursive(data, 'listings', max_depth=6)

    def _find_key_recursive(self, obj, target_key, max_depth=6, depth=0):
        """Sucht rekursiv nach einem Key der eine Liste enthält."""
        if depth > max_depth:
            return None
        if isinstance(obj, dict):
            if target_key in obj and isinstance(obj[target_key], list) and len(obj[target_key]) > 3:
                return obj[target_key]
            for v in obj.values():
                result = self._find_key_recursive(v, target_key, max_depth, depth + 1)
                if result:
                    return result
        return None

    def _item_to_listing(self, item: dict) -> Listing:
        """Konvertiert ein JSON-Item in ein Listing-Objekt."""
        try:
            inner = item.get('listing', item)

            listing = Listing()
            listing.external_id = f"hg-{inner.get('id', '')}"

            # Lokalisierung
            localization = inner.get('localization', {})
            primary_key = localization.get('primary', 'de')
            primary_loc = localization.get(primary_key, {})
            text_data = primary_loc.get('text', {})

            listing.title = text_data.get('title', '')
            listing.description = text_data.get('description', '')

            # Adresse
            addr = inner.get('address', {})
            loc = addr.get('locality', '')
            plz = addr.get('postalCode', '')
            street = addr.get('street', '')
            listing.city = loc
            listing.address = f"{street}, {plz} {loc}".strip(', ')

            geo = addr.get('geoCoordinates', {})
            listing.latitude = geo.get('latitude')
            listing.longitude = geo.get('longitude')

            # Preise - Kaufobjekte: prices.buy.price
            prices = inner.get('prices', {})
            buy_price = prices.get('buy', {})
            if isinstance(buy_price, dict):
                listing.price = buy_price.get('price')
            if not listing.price:
                rent_price = prices.get('rent', {})
                if isinstance(rent_price, dict):
                    listing.price = rent_price.get('gross')

            # Eigenschaften
            chars = inner.get('characteristics', {})
            listing.rooms = chars.get('numberOfRooms')
            living_space = chars.get('livingSpace')
            if living_space:
                listing.area_sqm = int(living_space)

            # URL
            listing.url = f"{self.BASE_URL}/buy/{inner.get('id', '')}"

            # Bilder
            attachments = primary_loc.get('attachments', []) or []
            for att in attachments:
                if att.get('type') == 'IMAGE' and att.get('url'):
                    listing.image_url = att['url']
                    break

            # Typ
            categories = inner.get('categories', [])
            cat_str = str(categories).upper()
            if 'HOUSE' in cat_str or 'VILLA' in cat_str:
                listing.property_type = 'einfamilienhaus'
            else:
                listing.property_type = 'wohnung'

            return listing

        except Exception as e:
            logger.debug(f"[homegate] Konvertierung fehlgeschlagen: {e}")
            return None
