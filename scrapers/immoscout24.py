"""Scraper für ImmoScout24.ch."""

import re
import json
import logging
import os
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class ImmoScout24Scraper(BaseScraper):
    """Scraper für immoscout24.ch - grösste Schweizer Immobilienplattform.

    Verwendet mehrere Strategien zur Datenextraktion:
    1. window.__INITIAL_STATE__ JSON
    2. __NEXT_DATA__ JSON (Next.js)
    3. Direkte HTML-Parsing der Listing-Cards
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

        # Debug: HTML speichern für Analyse
        self._save_debug_html(content)

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

        # Strategie 0c: Andere Window-Variablen durchsuchen
        js_window_vars = getattr(self, '_js_window_vars', {})
        for var_name, var_content in js_window_vars.items():
            try:
                data = json.loads(var_content)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[immoscout24] {len(result_listings)} Listings via {var_name}")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception):
                continue

        # Strategie 1: __INITIAL_STATE__ via Regex auf Raw-HTML
        listings.extend(self._parse_initial_state(content))
        if listings:
            return listings

        # Strategie 2: __NEXT_DATA__ (Next.js) als Fallback
        listings.extend(self._parse_next_data(content))
        if listings:
            return listings

        # Strategie 3: Suche nach JSON-Blobs im HTML
        listings.extend(self._parse_json_blobs(content))
        if listings:
            return listings

        # Strategie 4: Direkte HTML-Parsing der Listing-Cards
        listings.extend(self._parse_html_cards(content))

        return listings

    def _save_debug_html(self, content: str):
        """Speichert HTML zur Analyse in data/debug/."""
        try:
            debug_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            debug_file = os.path.join(debug_dir, 'immoscout24_last.html')
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.debug(f"[immoscout24] HTML gespeichert: {debug_file}")
        except Exception as e:
            logger.debug(f"[immoscout24] Debug-HTML speichern fehlgeschlagen: {e}")

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

    def _parse_json_blobs(self, content: str) -> List[Listing]:
        """Fallback: Suche nach JSON-Blobs die Listing-Daten enthalten."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Suche in allen Script-Tags nach JSON mit Listings
        for tag in soup.find_all('script'):
            text = tag.string or tag.get_text() or ''
            if len(text) < 500:  # Zu kurz für Listing-Daten
                continue

            # Versuche JSON zu extrahieren
            for pattern in [
                r'(\{["\']resultList["\'].*\})',
                r'(\{["\']listings["\'].*\})',
                r'(\{["\']searchResult["\'].*\})',
                r'(\{["\']props["\'].*\})',
            ]:
                for match in re.finditer(pattern, text, re.DOTALL):
                    try:
                        # Versuche das JSON zu parsen
                        json_str = match.group(1)
                        data = json.loads(json_str)
                        result_listings = self._find_listings_in_json(data)
                        if result_listings:
                            logger.info(f"[immoscout24] {len(result_listings)} Listings via JSON-Blob")
                            for item in result_listings:
                                listing = self._item_to_listing(item)
                                if listing:
                                    listings.append(listing)
                            return listings
                    except (json.JSONDecodeError, Exception):
                        continue

        return listings

    def _parse_html_cards(self, content: str) -> List[Listing]:
        """Fallback: Extrahiert Listings direkt aus HTML-Elementen."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Suche nach Listing-Links mit typischen ImmoScout24 URL-Patterns
        # Format: /de/d/wohnung-kaufen/... oder /de/d/haus-kaufen/...
        listing_links = soup.find_all('a', href=re.compile(r'/de/d/(wohnung|haus|immobilie)-(kaufen|mieten)/'))

        seen_urls = set()
        for link in listing_links:
            href = link.get('href', '')
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Versuche Listing-ID aus URL zu extrahieren
            id_match = re.search(r'/(\d+)(?:\?|$)', href)
            if not id_match:
                continue

            listing = Listing()
            listing.external_id = f"is24-{id_match.group(1)}"
            listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

            # Versuche Titel zu finden (im Link oder parent)
            card = link.find_parent(['article', 'div', 'li'])
            if card:
                # Titel
                title_el = card.find(['h2', 'h3', 'h4']) or link
                listing.title = title_el.get_text(strip=True)[:200] if title_el else ''

                # Preis
                price_patterns = [
                    r"CHF\s*([\d']+)",
                    r"([\d']+)\s*CHF",
                    r"Fr\.\s*([\d']+)",
                ]
                card_text = card.get_text()
                for pattern in price_patterns:
                    price_match = re.search(pattern, card_text)
                    if price_match:
                        price_str = price_match.group(1).replace("'", "").replace(" ", "")
                        try:
                            listing.price = int(price_str)
                        except ValueError:
                            pass
                        break

                # Zimmer
                rooms_match = re.search(r'(\d+(?:[.,]\d)?)\s*(?:Zimmer|Zi\.?|rooms?)', card_text, re.I)
                if rooms_match:
                    listing.rooms = float(rooms_match.group(1).replace(',', '.'))

                # Fläche
                area_match = re.search(r'(\d+)\s*m[²2]', card_text)
                if area_match:
                    listing.area_sqm = int(area_match.group(1))

                # Ort
                location_patterns = [
                    r'(\d{4})\s+([A-Za-zäöüÄÖÜ\-\s]+?)(?:\s*,|\s*$)',
                    r'in\s+([A-Za-zäöüÄÖÜ\-\s]+)',
                ]
                for pattern in location_patterns:
                    loc_match = re.search(pattern, card_text)
                    if loc_match:
                        if loc_match.lastindex >= 2:
                            listing.city = loc_match.group(2).strip()
                        else:
                            listing.city = loc_match.group(1).strip()
                        break

            if listing.title:
                listings.append(listing)

        if listings:
            logger.info(f"[immoscout24] {len(listings)} Listings via HTML-Cards")

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
