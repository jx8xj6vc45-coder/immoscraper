"""Scraper für Newhome.ch."""

import re
import json
import logging
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
        return f"{self.BASE_URL}/de/kaufen/immobilien/kanton-zuerich/"

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []
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

    def _parse_html_generic(self, soup: BeautifulSoup) -> List[Listing]:
        """Generisches HTML-Parsing: findet Listing-Cards über Heuristiken."""
        listings = []

        # Finde alle Links die nach Immobilien-Detail-Seiten aussehen
        seen_urls = set()
        for link in soup.find_all('a', href=True):
            href = link['href']
            if not re.search(r'/(?:kaufen|buy|objekt|property|detail|immobilien)/.*\d', href):
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
