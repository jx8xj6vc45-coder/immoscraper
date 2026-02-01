"""Scraper für Homegate.ch."""

import re
import json
import logging
from typing import List
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class HomegateScraper(BaseScraper):
    """Scraper für homegate.ch."""

    BASE_URL = 'https://www.homegate.ch'

    def get_name(self) -> str:
        return 'homegate'

    def build_search_url(self) -> str:
        max_price = self.criteria.get('max_price', 2200000)
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_area = self.criteria.get('min_area_sqm', 120)

        # Homegate URL-Struktur
        return (
            f"{self.BASE_URL}/kaufen/immobilien/kanton-zuerich/trefferliste"
            f"?ac={max_price}"
            f"&ah={min_rooms}"
            f"&ag={min_area}"
            f"&tab=list"
            f"&o=dateCreated-desc"
        )

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []

        # Versuche JSON aus Next.js Daten
        listings.extend(self._parse_nextjs_data(content))

        # Fallback HTML
        if not listings:
            listings.extend(self._parse_html(content))

        return listings

    def _parse_nextjs_data(self, content: str) -> List[Listing]:
        """Extrahiert Listings aus __NEXT_DATA__."""
        listings = []
        try:
            match = re.search(
                r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                content, re.DOTALL
            )
            if not match:
                return listings

            data = json.loads(match.group(1))
            props = data.get('props', {}).get('pageProps', {})
            result_list = props.get('resultList', [])

            if isinstance(result_list, dict):
                result_list = result_list.get('items', [])

            for item in result_list:
                listing = self._item_to_listing(item)
                if listing:
                    listings.append(listing)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Homegate JSON parsing fehlgeschlagen: {e}")

        return listings

    def _item_to_listing(self, item: dict) -> Listing:
        """Konvertiert Homegate JSON-Item."""
        try:
            listing = Listing()

            listing.external_id = str(item.get('id', item.get('listingId', '')))
            listing.title = item.get('title', '')
            listing.description = item.get('description', '')

            # Preis
            listing.price = item.get('sellingPrice') or item.get('price')

            # Eigenschaften
            listing.rooms = item.get('numberOfRooms')
            listing.area_sqm = item.get('surfaceLiving')

            # Adresse
            listing.address = item.get('street', '')
            listing.city = item.get('city', '') or item.get('locality', '')
            geo = item.get('geoLocation', {})
            if geo:
                listing.latitude = geo.get('latitude')
                listing.longitude = geo.get('longitude')

            # URL
            listing.url = f"{self.BASE_URL}/kaufen/{listing.external_id}"

            # Bilder
            images = item.get('pictures', []) or item.get('images', [])
            if images:
                first = images[0]
                if isinstance(first, dict):
                    listing.image_url = first.get('url', '')
                elif isinstance(first, str):
                    listing.image_url = first

            # Typ
            obj_type = str(item.get('objectType', '')).lower()
            if 'house' in obj_type or 'haus' in obj_type:
                listing.property_type = 'einfamilienhaus'
            else:
                listing.property_type = 'wohnung'

            return listing
        except Exception as e:
            logger.debug(f"Homegate item parsing Fehler: {e}")
            return None

    def _parse_html(self, content: str) -> List[Listing]:
        """Fallback HTML-Parsing."""
        from bs4 import BeautifulSoup
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        cards = soup.select('[data-test="result-list-item"], .ResultList article, .ListItem')

        for card in cards:
            try:
                listing = Listing()

                link = card.select_one('a[href*="/kaufen/"]')
                if link:
                    href = link.get('href', '')
                    id_match = re.search(r'/(\d+)$', href)
                    if id_match:
                        listing.external_id = id_match.group(1)
                    listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href

                title_el = card.select_one('h3, [data-test="title"], .ListItemTitle')
                if title_el:
                    listing.title = title_el.get_text(strip=True)

                price_el = card.select_one('[data-test="price"], .ListItemPrice')
                if price_el:
                    listing.price = self._parse_price(price_el.get_text())

                # Details
                for el in card.select('.ListItemRoomNumber, [data-test="rooms"]'):
                    listing.rooms = self._parse_rooms(el.get_text())
                for el in card.select('.ListItemLivingSpace, [data-test="area"]'):
                    listing.area_sqm = self._parse_area(el.get_text())

                addr_el = card.select_one('[data-test="address"], .ListItemAddress')
                if addr_el:
                    addr_text = addr_el.get_text(strip=True)
                    listing.address = addr_text
                    parts = addr_text.split(',')
                    if parts:
                        listing.city = re.sub(r'^\d{4}\s*', '', parts[-1].strip())

                if listing.external_id:
                    listings.append(listing)

            except Exception as e:
                logger.debug(f"Homegate HTML Fehler: {e}")
                continue

        return listings
