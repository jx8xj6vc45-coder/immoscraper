"""Scraper für Newhome.ch."""

import re
import json
import logging
import os
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class NewhomeScraper(BaseScraper):
    """Scraper für newhome.ch - Fokus auf Neubauprojekte.

    Verwendet generisches HTML-Parsing, da Newhome die Inhalte
    serverseitig rendert.
    """

    BASE_URL = 'https://www.newhome.ch'

    def get_name(self) -> str:
        return 'newhome'

    def build_search_url(self) -> str:
        """Baut Such-URL mit Filtern für Kanton Zürich."""
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_rooms_int = int(min_rooms)
        max_price = self.criteria.get('max_price', 2200000)

        # Newhome verwendet andere URL-Struktur mit Query-Parametern
        return (
            f"{self.BASE_URL}/de/kaufen/immobilien/kanton-zuerich/"
            f"?rooms={min_rooms_int}&price_to={max_price}"
        )

    def get_alternative_urls(self) -> list:
        """Alternative URLs für verschiedene Immobilientypen."""
        return [
            f"{self.BASE_URL}/de/kaufen/wohnung/kanton-zuerich/",
            f"{self.BASE_URL}/de/kaufen/einfamilienhaus/kanton-zuerich/",
            f"{self.BASE_URL}/de/kaufen/mehrfamilienhaus/kanton-zuerich/",
        ]

    def _save_debug_html(self, content: str):
        """Speichert HTML zur Analyse."""
        try:
            debug_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            debug_file = os.path.join(debug_dir, 'newhome_last.html')
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception:
            pass

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []

        # Debug speichern
        self._save_debug_html(content)

        # Versuche zuerst JS-evaluierte Daten
        for attr in ['_js_initial_state', '_js_next_data', '_js_preloaded_state']:
            js_data = getattr(self, attr, None)
            if js_data:
                try:
                    data = json.loads(js_data)
                    found = self._find_listings_recursive(data)
                    if found:
                        logger.info(f"[newhome] {len(found)} Listings via {attr}")
                        for item in found:
                            listing = self._json_to_listing(item)
                            if listing:
                                listings.append(listing)
                        if listings:
                            return listings
                except (json.JSONDecodeError, Exception):
                    continue

        # Window-Variablen durchsuchen
        js_window_vars = getattr(self, '_js_window_vars', {})
        for var_name, var_content in js_window_vars.items():
            try:
                data = json.loads(var_content)
                found = self._find_listings_recursive(data)
                if found:
                    logger.info(f"[newhome] {len(found)} Listings via {var_name}")
                    for item in found:
                        listing = self._json_to_listing(item)
                        if listing:
                            listings.append(listing)
                    if listings:
                        return listings
            except (json.JSONDecodeError, Exception):
                continue

        soup = BeautifulSoup(content, 'lxml')

        # Versuche JSON-LD Schema.org Daten
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data:
                        if item.get('@type') in ('Product', 'Residence', 'Apartment', 'House'):
                            listing = self._schema_to_listing(item)
                            if listing:
                                listings.append(listing)
                elif isinstance(data, dict):
                    if data.get('@type') in ('ItemList', 'SearchResultsPage'):
                        for item in data.get('itemListElement', []):
                            listing = self._schema_to_listing(item.get('item', item))
                            if listing:
                                listings.append(listing)
            except (json.JSONDecodeError, TypeError):
                continue

        if listings:
            return listings

        # Versuche __NEXT_DATA__
        script_tag = soup.find('script', id='__NEXT_DATA__')
        if script_tag and script_tag.string:
            try:
                data = json.loads(script_tag.string)
                props = data.get('props', {}).get('pageProps', {})
                # Verschiedene Pfade probieren
                for key in ['searchResults', 'results', 'listings', 'properties']:
                    results = props.get(key, {})
                    if isinstance(results, dict):
                        results = results.get('results', results.get('items', []))
                    if isinstance(results, list):
                        for item in results:
                            listing = self._json_to_listing(item)
                            if listing:
                                listings.append(listing)
                        if listings:
                            return listings
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"[newhome] __NEXT_DATA__ Fehler: {e}")

        # Generisches HTML-Parsing: Finde alle Links die nach Listings aussehen
        listings.extend(self._parse_html_generic(soup))

        return listings

    def _schema_to_listing(self, item: dict) -> Listing:
        """Konvertiert Schema.org JSON-LD zu Listing."""
        try:
            listing = Listing()
            listing.title = item.get('name', '')
            listing.external_id = f"nh-{item.get('sku', item.get('productID', ''))}"
            listing.url = item.get('url', '')
            listing.description = item.get('description', '')

            offers = item.get('offers', {})
            if isinstance(offers, dict):
                listing.price = self._parse_price(str(offers.get('price', '')))

            addr = item.get('address', {})
            if isinstance(addr, dict):
                listing.city = addr.get('addressLocality', '')
                listing.address = addr.get('streetAddress', '')

            return listing if listing.external_id and listing.external_id != 'nh-' else None
        except Exception:
            return None

    def _json_to_listing(self, item: dict) -> Listing:
        """Konvertiert generisches JSON-Item."""
        try:
            listing = Listing()
            listing.external_id = f"nh-{item.get('id', item.get('objectId', ''))}"
            listing.title = item.get('title', item.get('name', ''))
            listing.price = item.get('price') or item.get('sellingPrice')
            listing.rooms = item.get('rooms') or item.get('numberOfRooms')
            listing.area_sqm = item.get('livingArea') or item.get('surfaceLiving')
            listing.city = item.get('city', '') or item.get('municipality', '')
            listing.address = item.get('street', '')
            listing.url = f"{self.BASE_URL}/de/kaufen/immobilien/{item.get('id', '')}"
            return listing if listing.external_id and listing.external_id != 'nh-' else None
        except Exception:
            return None

    def _find_listings_recursive(self, obj, depth=0, max_depth=8):
        """Sucht rekursiv nach Arrays die Listing-Objekte enthalten könnten."""
        if depth > max_depth:
            return None

        if isinstance(obj, list) and len(obj) >= 3:
            # Prüfe ob die Liste Objekte mit listing-typischen Keys enthält
            sample = obj[0] if obj else {}
            if isinstance(sample, dict):
                listing_keys = ['id', 'price', 'title', 'rooms', 'address', 'street', 'city']
                matches = sum(1 for k in listing_keys if k.lower() in [x.lower() for x in sample.keys()])
                if matches >= 2:
                    return obj

        if isinstance(obj, dict):
            # Suche nach bekannten Keys
            for key in ['listings', 'results', 'items', 'properties', 'searchResults', 'objects']:
                if key in obj:
                    val = obj[key]
                    if isinstance(val, list) and len(val) >= 1:
                        return val
                    elif isinstance(val, dict):
                        result = self._find_listings_recursive(val, depth + 1, max_depth)
                        if result:
                            return result

            # Rekursiv weitersuchen
            for v in obj.values():
                result = self._find_listings_recursive(v, depth + 1, max_depth)
                if result:
                    return result

        return None

    def _parse_html_generic(self, soup: BeautifulSoup) -> List[Listing]:
        """Generisches HTML-Parsing: findet Listing-Cards über Heuristiken."""
        listings = []

        # Finde alle Links die nach Immobilien-Detail-Seiten aussehen
        # Newhome-spezifische Patterns hinzugefügt
        seen_urls = set()
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Mehrere URL-Patterns für Newhome
            if not re.search(r'/(?:kaufen|buy|objekt|object|property|detail|immobilien|expose|inserat)/.*\d', href):
                # Auch Links die direkt auf IDs enden
                if not re.search(r'/\d{5,}(?:/|$)', href):
                    continue
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Hole den umgebenden Container
            container = link.find_parent(['article', 'div', 'li', 'section'])
            if not container:
                container = link

            text = container.get_text(' ', strip=True)

            # Muss mindestens einen Preis ODER Zimmerzahl enthalten
            has_price = bool(re.search(r"(?:CHF|Fr\.?)\s*[\d'.,]+|[\d'.,]+\s*(?:CHF|Fr)", text))
            has_rooms = bool(re.search(r'\d+\.?\d*\s*(?:Zimmer|Zi\.|rooms)', text, re.IGNORECASE))

            if not (has_price or has_rooms):
                continue

            listing = Listing()
            listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

            id_match = re.search(r'/(\d+)', href)
            if id_match:
                listing.external_id = f"nh-{id_match.group(1)}"
            else:
                listing.external_id = f"nh-{href.rstrip('/').split('/')[-1]}"

            # Titel: erstes h2/h3 im Container
            title_el = container.find(['h2', 'h3', 'h4'])
            if title_el:
                listing.title = title_el.get_text(strip=True)
            else:
                listing.title = link.get_text(strip=True)[:100]

            # Preis extrahieren
            price_match = re.search(r"[\d']{3,}[\d']*", text)
            if price_match:
                listing.price = self._parse_price(price_match.group())

            # Zimmer
            rooms_match = re.search(r'(\d+\.?\d*)\s*(?:Zimmer|Zi\.)', text, re.IGNORECASE)
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

            listings.append(listing)

        return listings
