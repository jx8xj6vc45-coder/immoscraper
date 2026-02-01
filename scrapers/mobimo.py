"""Scraper für Mobimo.ch."""

import re
import logging
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class MobimoScraper(BaseScraper):
    """Scraper für mobimo.ch - Schweizer Immobiliengesellschaft."""

    BASE_URL = 'https://www.mobimo.ch'

    def get_name(self) -> str:
        return 'mobimo'

    def build_search_url(self) -> str:
        # mobimo.ch: Aktuelle Angebote Miete und Kauf
        return f"{self.BASE_URL}/de/immobilien/miete-und-kauf"

    def parse_listings(self, content: str) -> List[Listing]:
        """Parsed Mobimo Listings aus HTML."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        cards = soup.select(
            '.property-item, .immobilie-item, '
            'article[class*="property"], .object-list-item, '
            '.property-card, [class*="listing"]'
        )

        for card in cards:
            try:
                listing = Listing()

                # Link und ID
                link = card.select_one('a[href]')
                if link:
                    href = link.get('href', '')
                    listing.url = f"{self.BASE_URL}{href}" if href.startswith('/') else href
                    id_match = re.search(r'/(\d+)', href)
                    if id_match:
                        listing.external_id = f"mobimo-{id_match.group(1)}"
                    else:
                        slug = href.rstrip('/').split('/')[-1]
                        listing.external_id = f"mobimo-{slug}"

                # Titel
                title_el = card.select_one('h2, h3, .property-title, .title')
                if title_el:
                    listing.title = title_el.get_text(strip=True)

                # Preis
                price_el = card.select_one('.price, .property-price, [class*="price"]')
                if price_el:
                    listing.price = self._parse_price(price_el.get_text())

                # Details
                text = card.get_text(' ', strip=True)

                rooms_match = re.search(r'(\d+\.?\d*)\s*(?:Zimmer|Zi\.)', text, re.IGNORECASE)
                if rooms_match:
                    listing.rooms = float(rooms_match.group(1))

                area_match = re.search(r'(\d+)\s*m²', text)
                if area_match:
                    listing.area_sqm = int(area_match.group(1))

                # Adresse
                addr_el = card.select_one('.address, .location, [class*="address"]')
                if addr_el:
                    listing.address = addr_el.get_text(strip=True)
                    parts = listing.address.split(',')
                    if parts:
                        listing.city = re.sub(r'^\d{4}\s*', '', parts[-1].strip())

                if not listing.city:
                    location_match = re.search(r'\d{4}\s+(\w+)', text)
                    if location_match:
                        listing.city = location_match.group(1)

                # Bild
                img = card.select_one('img')
                if img:
                    listing.image_url = img.get('src', '') or img.get('data-src', '')

                listing.property_type = 'wohnung'
                if listing.title:
                    title_lower = listing.title.lower()
                    if any(w in title_lower for w in ['haus', 'villa', 'reihenhaus', 'doppelhaus']):
                        listing.property_type = 'einfamilienhaus'

                if listing.external_id:
                    listings.append(listing)

            except Exception as e:
                logger.debug(f"Mobimo Parsing Fehler: {e}")
                continue

        return listings
