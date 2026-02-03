"""Base Scraper und Listing-Datenklasse."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
import time
import random
import logging

logger = logging.getLogger(__name__)


@dataclass
class Listing:
    """Datenklasse für ein Immobilien-Inserat."""
    external_id: str = ''
    listing_hash: str = ''
    platform: str = ''

    # Basis
    title: str = ''
    description: str = ''
    price: Optional[int] = None
    rooms: Optional[float] = None
    area_sqm: Optional[int] = None
    address: str = ''
    city: str = ''

    # Geo
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Kriterien
    parking_spots: Optional[int] = None
    outdoor_space_sqm: Optional[int] = None
    outdoor_type: str = ''
    property_type: str = ''
    ownership_type: str = ''

    # URLs
    url: str = ''
    image_url: str = ''

    # Scores
    total_score: Optional[float] = None
    location_score: Optional[float] = None
    price_score: Optional[float] = None
    features_score: Optional[float] = None
    transport_score: Optional[float] = None
    education_score: Optional[float] = None
    steuerfuss_score: Optional[float] = None
    steuerfuss: Optional[int] = None
    grade: str = ''

    # Bildung
    maturitaetsquote: Optional[float] = None
    nearest_school_distance: Optional[float] = None
    gymnasium_nearby: bool = False
    nearby_schools: Optional[list] = None

    # Meta
    travel_time_to_hb: Optional[int] = None


def close_browser():
    """Kompatibilitätsstub - Browser wird jetzt pro Suche erstellt/geschlossen."""
    pass


class BaseScraper(ABC):
    """Abstrakte Basisklasse für alle Immobilien-Scraper."""

    # Scraper die erweiterte Anti-Bot-Massnahmen benötigen
    NEEDS_ENHANCED_STEALTH = ['immoscout24', 'newhome']

    def __init__(self, config):
        self.config = config
        self.criteria = config.get('search_criteria', {})

    @abstractmethod
    def get_name(self) -> str:
        """Name der Plattform."""

    @abstractmethod
    def build_search_url(self) -> str:
        """Baut Such-URL mit Filtern."""

    @abstractmethod
    def parse_listings(self, content: str) -> List[Listing]:
        """Parsed alle Listings aus dem Response-Content."""

    def search(self) -> List[Listing]:
        """Führt Suche mit Playwright (echtem Browser) durch.

        Erstellt pro Aufruf eine eigene Browser-Instanz, damit es
        threadsicher mit APScheduler funktioniert.
        """
        # Für problematische Seiten erweiterte Methode nutzen
        if self.get_name() in self.NEEDS_ENHANCED_STEALTH:
            return self._search_with_enhanced_stealth()

        return self._search_standard()

    def _search_standard(self) -> List[Listing]:
        """Standard-Suche für normale Seiten (Homegate, Allreal, etc.)."""
        pw = None
        browser = None
        try:
            url = self.build_search_url()
            logger.info(f"[{self.get_name()}] Fetching {url}")

            from playwright.sync_api import sync_playwright
            try:
                from playwright_stealth import stealth_sync
                has_stealth = True
            except ImportError:
                has_stealth = False

            pw = sync_playwright().start()

            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--window-size=1920,1080',
                    '--start-maximized',
                ]
            )

            context = browser.new_context(
                locale='de-CH',
                timezone_id='Europe/Zurich',
                viewport={'width': 1920, 'height': 1080},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                ),
                extra_http_headers={
                    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                },
            )
            page = context.new_page()

            if has_stealth:
                stealth_sync(page)

            # Einfache JS-Injection
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['de-CH', 'de', 'en']});
                window.chrome = { runtime: {} };
            """)

            # Seite laden
            page.goto(url, wait_until='networkidle', timeout=45000)
            page.wait_for_timeout(3000)

            # Cookie-Banner wegklicken
            self._handle_cookie_banner(page)

            # JS-Variablen extrahieren
            self._extract_js_state(page)

            content = page.content()
            listings = self.parse_listings(content)

            filtered = self._filter_listings(listings)

            logger.info(
                f"[{self.get_name()}] {len(listings)} geparst, "
                f"{len(filtered)} nach Filter"
            )

            time.sleep(random.uniform(1.0, 2.0))

            context.close()
            return filtered

        except Exception as e:
            logger.error(f"[{self.get_name()}] Fehler: {e}")
            return []
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass

    def _search_with_enhanced_stealth(self) -> List[Listing]:
        """Erweiterte Suche für Seiten mit starkem Bot-Schutz (ImmoScout24, Newhome).

        Versucht zuerst Patchright (undetected Playwright-Fork), dann normales Playwright.
        """
        pw = None
        browser = None
        try:
            url = self.build_search_url()

            # Versuche Patchright zuerst (umgeht CDP-Erkennung)
            try:
                from patchright.sync_api import sync_playwright
                logger.info(f"[{self.get_name()}] Fetching mit Patchright (undetected): {url}")
                use_patchright = True
            except ImportError:
                from playwright.sync_api import sync_playwright
                logger.info(f"[{self.get_name()}] Patchright nicht installiert, nutze Playwright: {url}")
                use_patchright = False

            try:
                from playwright_stealth import stealth_sync
                has_stealth = True
            except ImportError:
                has_stealth = False
                if not use_patchright:
                    logger.warning("Weder Patchright noch playwright-stealth installiert - Anti-Bot wird wahrscheinlich fehlschlagen")

            pw = sync_playwright().start()

            # Zufällige Einstellungen
            viewports = [
                {'width': 1920, 'height': 1080},
                {'width': 1536, 'height': 864},
                {'width': 1440, 'height': 900},
            ]
            viewport = random.choice(viewports)

            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            ]
            user_agent = random.choice(user_agents)

            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    f'--window-size={viewport["width"]},{viewport["height"]}',
                    '--start-maximized',
                    '--disable-extensions',
                ]
            )

            context = browser.new_context(
                locale='de-CH',
                timezone_id='Europe/Zurich',
                viewport=viewport,
                user_agent=user_agent,
                extra_http_headers={
                    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'sec-ch-ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"' if 'Windows' in user_agent else '"macOS"',
                },
            )
            page = context.new_page()

            if has_stealth:
                stealth_sync(page)

            # Erweiterte JS-Injection
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                delete navigator.__proto__.webdriver;
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const plugins = [
                            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                            {name: 'Native Client', filename: 'internal-nacl-plugin'},
                        ];
                        plugins.length = 3;
                        return plugins;
                    }
                });
                Object.defineProperty(navigator, 'languages', {get: () => ['de-CH', 'de', 'en-US', 'en']});
                window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
            """)

            # Seite laden mit längerer Timeout
            page.goto(url, wait_until='networkidle', timeout=60000)

            # Längere initiale Wartezeit
            page.wait_for_timeout(random.randint(4000, 7000))

            # Menschliches Verhalten simulieren
            try:
                page.mouse.move(random.randint(100, 400), random.randint(100, 300))
                page.wait_for_timeout(random.randint(300, 600))
                page.mouse.wheel(0, random.randint(150, 350))
                page.wait_for_timeout(random.randint(500, 1000))
            except Exception:
                pass

            # Cookie-Banner
            self._handle_cookie_banner(page)

            # Bot-Schutz prüfen
            content_check = page.content()
            if 'captcha' in content_check.lower() or 'datadome' in content_check.lower():
                logger.warning(f"[{self.get_name()}] Bot-Schutz erkannt - warte...")
                page.wait_for_timeout(random.randint(10000, 15000))
                # Nochmal scrollen
                try:
                    page.mouse.wheel(0, 200)
                except Exception:
                    pass
                page.wait_for_timeout(5000)

            # JS-Variablen extrahieren
            self._extract_js_state(page)

            content = page.content()
            listings = self.parse_listings(content)

            filtered = self._filter_listings(listings)

            logger.info(
                f"[{self.get_name()}] {len(listings)} geparst, "
                f"{len(filtered)} nach Filter"
            )

            time.sleep(random.uniform(2.0, 4.0))

            context.close()
            return filtered

        except Exception as e:
            logger.error(f"[{self.get_name()}] Fehler: {e}")
            return []
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass

    def _extract_js_state(self, page):
        """Extrahiert JavaScript State-Variablen von der Seite."""
        self._js_initial_state = None
        self._js_next_data = None
        self._js_preloaded_state = None
        self._js_window_vars = {}

        js_vars = [
            ('__INITIAL_STATE__', '_js_initial_state'),
            ('__NEXT_DATA__', '_js_next_data'),
            ('__PRELOADED_STATE__', '_js_preloaded_state'),
            ('__NUXT__', '_js_preloaded_state'),
        ]

        for var_name, attr_name in js_vars:
            try:
                result = page.evaluate(
                    f'() => {{ try {{ return JSON.stringify(window.{var_name}); }} catch(e) {{ return null; }} }}'
                )
                if result and result != 'null' and result != 'undefined':
                    setattr(self, attr_name, result)
                    logger.debug(f"[{self.get_name()}] {var_name} gefunden ({len(result)} bytes)")
            except Exception:
                pass

    def _handle_cookie_banner(self, page):
        """Klickt Cookie-Banner weg falls vorhanden."""
        cookie_selectors = [
            'button:has-text("Akzeptieren")',
            'button:has-text("Accept")',
            'button:has-text("Alle akzeptieren")',
            'button:has-text("Zustimmen")',
            'button:has-text("OK")',
            '[id*="cookie"] button',
            '[class*="cookie"] button',
            '#onetrust-accept-btn-handler',
        ]
        for selector in cookie_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=500):
                    btn.click()
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue

    def _filter_listings(self, listings: List[Listing]) -> List[Listing]:
        """Filtert Listings nach Kriterien."""
        filtered = []
        for listing in listings:
            listing.platform = self.get_name()
            if self.meets_criteria(listing):
                filtered.append(listing)
        return filtered

    def meets_criteria(self, listing: Listing) -> bool:
        """Prüft ob ein Inserat die Mindestkriterien erfüllt."""
        max_price = self.criteria.get('max_price', 2_200_000)
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_area = self.criteria.get('min_area_sqm', 120)

        if listing.price and listing.price > max_price:
            return False
        if listing.rooms and listing.rooms < min_rooms:
            return False
        if listing.area_sqm and listing.area_sqm < min_area:
            return False
        if listing.ownership_type and listing.ownership_type == 'baurecht_only':
            return False

        return True

    def _parse_price(self, text: str) -> Optional[int]:
        """Extrahiert Preis aus Text wie '1'850'000' oder 'CHF 1,850,000'."""
        if not text:
            return None
        import re
        cleaned = re.sub(r'[^\d]', '', text)
        if cleaned:
            val = int(cleaned)
            if 100_000 <= val <= 50_000_000:
                return val
        return None

    def _parse_rooms(self, text: str) -> Optional[float]:
        """Extrahiert Zimmerzahl aus Text wie '5.5 Zimmer'."""
        if not text:
            return None
        import re
        match = re.search(r'(\d+\.?\d*)', text)
        if match:
            return float(match.group(1))
        return None

    def _parse_area(self, text: str) -> Optional[int]:
        """Extrahiert Fläche in m² aus Text."""
        if not text:
            return None
        import re
        match = re.search(r'(\d+)\s*m', text)
        if match:
            return int(match.group(1))
        return None
