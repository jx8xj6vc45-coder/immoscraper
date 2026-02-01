"""Scraper für Newhome.ch."""

import re
import json
import logging
from typing import List
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class NewhomeScraper(BaseScraper):
    """Scraper für newhome.ch - Fokus auf Neubauprojekte."""

    BASE_URL = 'https://www.newhome.ch'

    def get_name(self) -> str:
        return 'newhome'

    def build_search_url(self) -> str:
        # Newhome.ch: Suche über die Karte/Liste
        return f"{self.BASE_URL}/de/kaufen/immobilien/kanton-zuerich/"

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []

        # JSON-Daten versuchen
        try:
            match = re.search(
                r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                content, re.DOTALL
            )
            if match:
                data = json.loads(match.group(1))
                props = data.get('props', {}).get('pageProps', {})
                results = props.get('searchResults', {}).get('results', [])
                for item in results:
                    listing = self._item_to_listing(item)
                    if listing:
                        listings.append(listing)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Newhome JSON fehler: {e}")

        # Fallback HTML
        if not listings:
            listings.extend(self._parse_html(content))

        return listings

    def _item_to_listing(self, item: dict) -> Listing:
        """Konvertiert Newhome JSON-Item."""
        try:
            listing = Listing()
            listing.external_id = str(item.get('id', ''))
            listing.title = item.get('title', '')
            listing.description = item.get('description', '')

            listing.price = item.get('price') or item.get('sellingPrice')
            listing.rooms = item.get('rooms') or item.get('numberOfRooms')
            listing.area_sqm = item.get('livingArea') or item.get('surfaceLiving')

            listing.address = item.get('street', '')
            listing.city = item.get('city', '') or item.get('municipality', '')

            geo = item.get('coordinates', {}) or item.get('geoLocation', {})
            if geo:
                listing.latitude = geo.get('lat') or geo.get('latitude')
                listing.longitude = geo.get('lng') or geo.get('longitude')

            listing.url = f"{self.BASE_URL}/de/kaufen/immobilien/{listing.external_id}"

            images = item.get('images', [])
            if images:
                first = images[0]
                listing.image_url = first.get('url', '') if isinstance(first, dict) else str(first)

            obj_type = str(item.get('objectType', '')).lower()
            if 'house' in obj_type or 'haus' in obj_type:
                listing.property_type = 'einfamilienhaus'
            else:
                listing.property_type = 'wohnung'

            return listing
        except Exception as e:
            logger.debug(f"Newhome item Fehler: {e}")
            return None

    def _parse_html(self, content: str) -> List[Listing]:
        """Fallback HTML-Parsing für Newhome."""
        from bs4 import BeautifulSoup
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        cards = soup.select('.search-result-item, .property-card, article[class*="listing"]')

        for card in cards:
            try:
                listing = Listing()

                link = card.select_one('a[href*="/kaufen/"], a[href*="/immobilien/"]')
                if link:
                    href = link.get('href', '')
                    id_match = re.search(r'/(\d+)', href)
                    if id_match:
                        listing.external_id = id_match.group(1)
                    listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

                title_el = card.select_one('h2, h3, .property-title')
                if title_el:
                    listing.title = title_el.get_text(strip=True)

                price_el = card.select_one('.property-price, .price')
                if price_el:
                    listing.price = self._parse_price(price_el.get_text())

                for el in card.select('.property-rooms, .rooms'):
                    listing.rooms = self._parse_rooms(el.get_text())
                for el in card.select('.property-area, .living-area'):
                    listing.area_sqm = self._parse_area(el.get_text())

                addr_el = card.select_one('.property-address, .address')
                if addr_el:
                    listing.address = addr_el.get_text(strip=True)
                    parts = listing.address.split(',')
                    if parts:
                        listing.city = re.sub(r'^\d{4}\s*', '', parts[-1].strip())

                if listing.external_id:
                    listings.append(listing)

            except Exception as e:
                logger.debug(f"Newhome HTML Fehler: {e}")
                continue

        return listings
