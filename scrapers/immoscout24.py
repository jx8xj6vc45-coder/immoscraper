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

        # Extrahiere __INITIAL_STATE__ JSON
        listings.extend(self._parse_initial_state(content))

        return listings

    def _parse_initial_state(self, content: str) -> List[Listing]:
        """Extrahiert Listings aus window.__INITIAL_STATE__ JSON."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Finde Script-Tag mit __INITIAL_STATE__
        script_tag = soup.find(
            lambda tag: tag.name == 'script'
            and tag.string
            and tag.string.strip().startswith('window.__INITIAL_STATE__=')
        )

        if not script_tag:
            logger.warning("[immoscout24] Kein __INITIAL_STATE__ gefunden")
            return listings

        try:
            json_text = script_tag.string.strip()
            json_text = json_text.replace('window.__INITIAL_STATE__=', '', 1)
            data = json.loads(json_text)
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"[immoscout24] JSON-Parsing fehlgeschlagen: {e}")
            return listings

        # Pfad: resultList.search.fullSearch.result.listings
        try:
            result_listings = (
                data['resultList']['search']['fullSearch']['result']['listings']
            )
        except (KeyError, TypeError) as e:
            logger.warning(f"[immoscout24] JSON-Pfad geändert: {e}")
            return listings

        for item in result_listings:
            listing = self._item_to_listing(item)
            if listing:
                listings.append(listing)

        return listings

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
