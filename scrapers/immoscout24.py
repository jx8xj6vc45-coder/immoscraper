"""Scraper für ImmoScout24.ch."""

import re
import json
import logging
from typing import List
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class ImmoScout24Scraper(BaseScraper):
    """Scraper für immoscout24.ch - grösste Schweizer Immobilienplattform."""

    BASE_URL = 'https://www.immoscout24.ch'

    def get_name(self) -> str:
        return 'immoscout24'

    def build_search_url(self) -> str:
        max_price = self.criteria.get('max_price', 2200000)
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_area = self.criteria.get('min_area_sqm', 120)

        # ImmoScout24 URL-Struktur für Kaufobjekte in Zürich
        params = (
            f"/immobilien/kaufen/ort-zuerich"
            f"?pf={max_price}"
            f"&nrf={min_rooms}"
            f"&slf={min_area}"
            f"&t=1,2"  # 1=Wohnung, 2=Haus
            f"&se=16"  # Sortierung: Neueste zuerst
        )
        return f"{self.BASE_URL}{params}"

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []

        # ImmoScout24 liefert Daten oft als JSON-LD oder in data-Attributen
        # Versuche zuerst JSON-LD zu finden
        listings.extend(self._parse_json_ld(content))

        # Fallback: HTML-Parsing
        if not listings:
            listings.extend(self._parse_html(content))

        return listings

    def _parse_json_ld(self, content: str) -> List[Listing]:
        """Versucht Listings aus JSON-LD Script-Tags zu extrahieren."""
        listings = []
        try:
            # Suche nach __NEXT_DATA__ oder ähnlichem JSON-Block
            match = re.search(
                r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                content, re.DOTALL
            )
            if match:
                data = json.loads(match.group(1))
                # Navigation durch die Next.js Datenstruktur
                props = data.get('props', {}).get('pageProps', {})
                result_list = props.get('resultList', {}).get('listing', [])

                for item in result_list:
                    listing = self._item_to_listing(item)
                    if listing:
                        listings.append(listing)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"JSON-LD parsing fehlgeschlagen: {e}")

        return listings

    def _item_to_listing(self, item: dict) -> Listing:
        """Konvertiert ein JSON-Item in ein Listing-Objekt."""
        try:
            listing = Listing()
            listing.external_id = str(item.get('id', ''))
            listing.title = item.get('title', '')
            listing.description = item.get('description', '')

            # Preise
            price_data = item.get('prices', {})
            if isinstance(price_data, dict):
                listing.price = price_data.get('buy', {}).get('price')
            elif isinstance(price_data, list) and price_data:
                listing.price = price_data[0].get('price')

            # Eigenschaften
            props = item.get('characteristics', {})
            listing.rooms = props.get('numberOfRooms')
            listing.area_sqm = props.get('livingSpace')

            # Adresse
            addr = item.get('address', {})
            if isinstance(addr, dict):
                listing.address = addr.get('street', '')
                listing.city = addr.get('locality', '')
                listing.latitude = addr.get('geoCoordinates', {}).get('latitude')
                listing.longitude = addr.get('geoCoordinates', {}).get('longitude')

            # URL
            listing.url = f"{self.BASE_URL}/immobilien/d-{listing.external_id}"

            # Bilder
            images = item.get('images', [])
            if images:
                listing.image_url = images[0].get('url', '')

            # Property Type
            category = item.get('categories', [])
            if 'HOUSE' in str(category).upper():
                listing.property_type = 'einfamilienhaus'
            else:
                listing.property_type = 'wohnung'

            return listing
        except Exception as e:
            logger.debug(f"Fehler beim Konvertieren: {e}")
            return None

    def _parse_html(self, content: str) -> List[Listing]:
        """Fallback: Parsed Listings aus dem HTML."""
        from bs4 import BeautifulSoup
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # ImmoScout24 nutzt verschiedene CSS-Klassen
        cards = soup.select('[data-test="result-list-item"], .ResultList_listItem__k2fBH, article.ResultListItem')

        for card in cards:
            try:
                listing = Listing()

                # ID aus Link
                link = card.select_one('a[href*="/immobilien/d-"]')
                if link:
                    href = link.get('href', '')
                    id_match = re.search(r'/d-(\d+)', href)
                    if id_match:
                        listing.external_id = id_match.group(1)
                    listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

                # Titel
                title_el = card.select_one('h3, [data-test="title"]')
                if title_el:
                    listing.title = title_el.get_text(strip=True)

                # Preis
                price_el = card.select_one('[data-test="price"], .HgListingCard_price')
                if price_el:
                    listing.price = self._parse_price(price_el.get_text())

                # Zimmer und Fläche
                details = card.select('[data-test="characteristic"], .HgListingCard_characteristic')
                for detail in details:
                    text = detail.get_text(strip=True)
                    if 'Zimmer' in text or 'room' in text.lower():
                        listing.rooms = self._parse_rooms(text)
                    elif 'm²' in text or 'm2' in text:
                        listing.area_sqm = self._parse_area(text)

                # Adresse
                addr_el = card.select_one('[data-test="address"], .HgListingCard_address')
                if addr_el:
                    addr_text = addr_el.get_text(strip=True)
                    listing.address = addr_text
                    # Stadt extrahieren (letztes Element nach Komma)
                    parts = addr_text.split(',')
                    if parts:
                        city_part = parts[-1].strip()
                        # PLZ entfernen
                        city_clean = re.sub(r'^\d{4}\s*', '', city_part)
                        listing.city = city_clean

                # Bild
                img = card.select_one('img')
                if img:
                    listing.image_url = img.get('src', '')

                if listing.external_id:
                    listings.append(listing)

            except Exception as e:
                logger.debug(f"HTML-Parsing Fehler: {e}")
                continue

        return listings
