"""Scraper für Homegate.ch."""

import re
import json
import logging
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class HomegateScraper(BaseScraper):
    """Scraper für homegate.ch.

    Extrahiert Listing-Daten aus dem window.__INITIAL_STATE__ JSON-Blob.
    Gleiche Datenstruktur wie ImmoScout24.ch.
    """

    BASE_URL = 'https://www.homegate.ch'

    def get_name(self) -> str:
        return 'homegate'

    def build_search_url(self) -> str:
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_rooms_int = int(min_rooms)

        return (
            f"{self.BASE_URL}/buy/real-estate/canton-zurich/matching-list"
            f"?ac={min_rooms_int}"
        )

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []
        listings.extend(self._parse_initial_state(content))
        return listings

    def _parse_initial_state(self, content: str) -> List[Listing]:
        """Extrahiert Listings aus window.__INITIAL_STATE__ JSON."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Finde Script-Tag mit __INITIAL_STATE__
        script_tag = None
        for tag in soup.find_all('script'):
            if tag.string and 'window.__INITIAL_STATE__=' in tag.string:
                script_tag = tag
                break

        if not script_tag:
            logger.warning("[homegate] Kein __INITIAL_STATE__ gefunden")
            return listings

        try:
            json_text = script_tag.string.strip()
            # Entferne den Prefix
            prefix = 'window.__INITIAL_STATE__='
            idx = json_text.index(prefix)
            json_text = json_text[idx + len(prefix):]
            data = json.loads(json_text)
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            logger.warning(f"[homegate] JSON-Parsing fehlgeschlagen: {e}")
            return listings

        # Pfad: resultList.search.fullSearch.result.listings
        try:
            result_listings = (
                data['resultList']['search']['fullSearch']['result']['listings']
            )
        except (KeyError, TypeError) as e:
            logger.warning(f"[homegate] JSON-Pfad geändert: {e}")
            return listings

        for item in result_listings:
            listing = self._item_to_listing(item)
            if listing:
                listings.append(listing)

        return listings

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
