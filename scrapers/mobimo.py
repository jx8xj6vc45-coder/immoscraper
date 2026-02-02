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
        """Parsed Mobimo Listings mit generischem HTML-Parsing."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')

        # Finde alle Links die nach Immobilien-Detail-Seiten aussehen
        seen_urls = set()
        for link in soup.find_all('a', href=True):
            href = link['href']

            # Filtere auf Links die nach Objekten aussehen
            if not re.search(
                r'/(?:immobilien|objekt|property|wohnung|haus|kaufen|mieten)/.+',
                href
            ):
                continue

            # Keine Übersichtsseiten
            if href.rstrip('/') in (
                '/de/immobilien/miete-und-kauf',
                '/de/immobilien',
            ):
                continue

            full_url = f"{self.BASE_URL}{href}" if href.startswith('/') else href
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            container = link.find_parent(['article', 'div', 'li', 'section'])
            if not container:
                container = link

            text = container.get_text(' ', strip=True)

            listing = Listing()
            listing.url = full_url

            slug = href.rstrip('/').split('/')[-1]
            listing.external_id = f"mobimo-{slug}"

            title_el = container.find(['h2', 'h3', 'h4'])
            if title_el:
                listing.title = title_el.get_text(strip=True)
            else:
                listing.title = link.get_text(strip=True)[:100]

            price_match = re.search(r"(?:CHF|Fr\.?)\s*([\d'.,]+)", text)
            if price_match:
                listing.price = self._parse_price(price_match.group(1))

            rooms_match = re.search(r'(\d+\.?\d*)\s*(?:Zimmer|Zi\.|rooms)', text, re.IGNORECASE)
            if rooms_match:
                listing.rooms = float(rooms_match.group(1))

            area_match = re.search(r'(\d+)\s*m[²2]', text)
            if area_match:
                listing.area_sqm = int(area_match.group(1))

            plz_match = re.search(r'(\d{4})\s+([A-ZÄÖÜ][a-zäöüéèê]+)', text)
            if plz_match:
                listing.city = plz_match.group(2)
                listing.address = f"{plz_match.group(1)} {plz_match.group(2)}"

            img = container.find('img')
            if img:
                listing.image_url = img.get('src', '') or img.get('data-src', '')

            listing.property_type = 'wohnung'
            if listing.title:
                title_lower = listing.title.lower()
                if any(w in title_lower for w in ['haus', 'villa', 'reihenhaus', 'doppelhaus', 'house']):
                    listing.property_type = 'einfamilienhaus'

            listings.append(listing)

        return listings
