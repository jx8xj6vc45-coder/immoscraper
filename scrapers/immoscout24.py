"""Scraper für ImmoScout24.ch."""

import re
import json
import logging
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class ImmoScout24Scraper(BaseScraper):
    """Scraper für immoscout24.ch - grösste Schweizer Immobilienplattform.

    Extrahiert Listing-Daten aus dem window.__INITIAL_STATE__ JSON-Blob,
    der im gerenderten HTML eingebettet ist.
    """

    BASE_URL = 'https://www.immoscout24.ch'

    def get_name(self) -> str:
        return 'immoscout24'

    def build_search_url(self) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_rooms_int = int(min_rooms)

        return (
            f"{self.BASE_URL}/de/real-estate/buy/canton-zurich"
            f"?nrf={min_rooms_int}"
        )

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []

        # Strategie 0: JS-evaluierter __INITIAL_STATE__ (direkt aus Browser)
        js_state = getattr(self, '_js_initial_state', None)
        if js_state:
            try:
                data = json.loads(js_state)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[immoscout24] {len(result_listings)} Listings via JS evaluate")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[immoscout24] JS evaluate fehlgeschlagen: {e}")

        # Strategie 0b: JS-evaluierter __NEXT_DATA__
        js_next = getattr(self, '_js_next_data', None)
        if js_next:
            try:
                data = json.loads(js_next)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[immoscout24] {len(result_listings)} Listings via JS __NEXT_DATA__")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[immoscout24] JS __NEXT_DATA__ fehlgeschlagen: {e}")

        # Strategie 1: __INITIAL_STATE__ via Regex auf Raw-HTML
        listings.extend(self._parse_initial_state(content))

        # Strategie 2: __NEXT_DATA__ (Next.js) als Fallback
        if not listings:
            listings.extend(self._parse_next_data(content))

        # Strategie 3: page.evaluate() Ergebnis via JS-Extraktion
        if not listings:
            listings.extend(self._parse_via_evaluate(content))

        return listings

    def _extract_json_from_html(self, content: str, var_name: str) -> dict:
        """Extrahiert JSON aus einem window.VAR_NAME=... Pattern im HTML."""
        # Regex auf Raw-HTML - robuster als BeautifulSoup tag.string
        pattern = re.escape(var_name) + r'\s*=\s*(\{.+?\})\s*;?\s*</script>'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        # Alternativ: suche ohne </script> Anker (z.B. bei Zeilenumbruch)
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
            # Fallback: BeautifulSoup mit verschiedenen Suchmustern
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
            logger.warning("[immoscout24] Kein __INITIAL_STATE__ gefunden")
            return listings

        # Pfad: resultList.search.fullSearch.result.listings
        result_listings = self._find_listings_in_json(data)
        if not result_listings:
            logger.warning("[immoscout24] Keine Listings im JSON gefunden")
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

        # Suche rekursiv nach Listings
        result_listings = self._find_listings_in_json(data)
        if result_listings:
            logger.info(f"[immoscout24] {len(result_listings)} Listings via __NEXT_DATA__")
            for item in result_listings:
                listing = self._item_to_listing(item)
                if listing:
                    listings.append(listing)

        return listings

    def _parse_via_evaluate(self, content: str) -> List[Listing]:
        """Fallback: Suche nach JSON-Blobs die Listing-Daten enthalten."""
        listings = []

        # Suche nach grossen JSON-Objekten im HTML die 'listings' enthalten
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
        """Findet Listings-Array in verschachteltem JSON (verschiedene Pfade)."""
        # Bekannter Pfad
        try:
            return data['resultList']['search']['fullSearch']['result']['listings']
        except (KeyError, TypeError):
            pass

        # Alternative Pfade
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

        # Rekursive Suche nach einem 'listings'-Key mit einer Liste
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
            # Branding-Info ist auf der äusseren Ebene
            inner = item.get('listing', item)

            listing = Listing()
            listing.external_id = f"is24-{inner.get('id', '')}"

            # Lokalisierung (de/fr/it/en)
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
            # Fallback: rent.gross (falls doch Miete)
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
            listing.url = f"{self.BASE_URL}/de/d/buy/{inner.get('id', '')}"

            # Bilder aus Lokalisierung
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
            logger.debug(f"[immoscout24] Konvertierung fehlgeschlagen: {e}")
            return None
