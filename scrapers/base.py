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
        Nutzt playwright-stealth um Bot-Erkennung zu umgehen.
        """
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
                logger.debug("playwright-stealth nicht installiert, nutze Standard-Modus")

            pw = sync_playwright().start()

            # Browser mit zusätzlichen Argumenten für bessere Tarnung
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
                # Zusätzliche Browser-Eigenschaften
                extra_http_headers={
                    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'sec-ch-ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                },
            )
            page = context.new_page()

            # Stealth-Modus aktivieren (versteckt Automatisierungs-Merkmale)
            if has_stealth:
                stealth_sync(page)

            try:
                # Zusätzliche JS-Injection für noch bessere Tarnung
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['de-CH', 'de', 'en']});
                    window.chrome = { runtime: {} };
                """)

                # Seite laden und auf Netzwerk-Idle warten
                page.goto(url, wait_until='networkidle', timeout=45000)

                # Längere Wartezeit für Anti-Bot-Checks
                page.wait_for_timeout(5000)

                # Simuliere menschliches Verhalten (Mausbewegung, Scrollen)
                try:
                    page.mouse.move(random.randint(100, 500), random.randint(100, 400))
                    page.wait_for_timeout(random.randint(200, 500))
                    page.mouse.wheel(0, random.randint(100, 300))
                    page.wait_for_timeout(random.randint(300, 700))
                except Exception:
                    pass

                # Cookie-Banner wegklicken falls vorhanden
                for selector in [
                    'button:has-text("Akzeptieren")',
                    'button:has-text("Accept")',
                    'button:has-text("Alle akzeptieren")',
                    'button:has-text("OK")',
                    '[id*="cookie"] button',
                    '[class*="cookie"] button',
                ]:
                    try:
                        btn = page.locator(selector).first
                        if btn.is_visible(timeout=500):
                            btn.click()
                            page.wait_for_timeout(500)
                            break
                    except Exception:
                        continue

                # Prüfe ob CAPTCHA/Bot-Schutz angezeigt wird
                content_check = page.content()
                if 'captcha' in content_check.lower() or 'datadome' in content_check.lower():
                    logger.warning(f"[{self.get_name()}] CAPTCHA/Bot-Schutz erkannt - warte und versuche erneut...")
                    page.wait_for_timeout(10000)  # 10 Sekunden warten
                    # Nochmal scrollen und warten
                    page.mouse.wheel(0, 200)
                    page.wait_for_timeout(5000)

                # Versuche verschiedene JS-Variablen zu extrahieren
                self._js_initial_state = None
                self._js_next_data = None
                self._js_preloaded_state = None

                # Liste von möglichen State-Variablen
                js_vars = [
                    ('__INITIAL_STATE__', '_js_initial_state'),
                    ('__NEXT_DATA__', '_js_next_data'),
                    ('__PRELOADED_STATE__', '_js_preloaded_state'),
                    ('__NUXT__', '_js_preloaded_state'),
                    ('__APP_INITIAL_STATE__', '_js_initial_state'),
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

                # Suche nach window-Properties die 'state', 'data' oder 'props' enthalten
                try:
                    found_vars = page.evaluate('''() => {
                        const results = {};
                        for (const key of Object.keys(window)) {
                            if (key.includes('STATE') || key.includes('DATA') || key.includes('PROPS')) {
                                try {
                                    const val = window[key];
                                    if (val && typeof val === 'object') {
                                        results[key] = JSON.stringify(val).substring(0, 50000);
                                    }
                                } catch(e) {}
                            }
                        }
                        return results;
                    }''')
                    if found_vars:
                        self._js_window_vars = found_vars
                        for k in found_vars.keys():
                            logger.debug(f"[{self.get_name()}] Window-Var gefunden: {k}")
                except Exception:
                    self._js_window_vars = {}

                content = page.content()
                listings = self.parse_listings(content)

                filtered = []
                for listing in listings:
                    listing.platform = self.get_name()
                    if self.meets_criteria(listing):
                        filtered.append(listing)

                logger.info(
                    f"[{self.get_name()}] {len(listings)} geparst, "
                    f"{len(filtered)} nach Filter"
                )

                # Rate limiting
                time.sleep(random.uniform(1.0, 2.5))

                return filtered

            finally:
                context.close()

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

    def meets_criteria(self, listing: Listing) -> bool:
        """Prüft ob ein Inserat die Mindestkriterien erfüllt.

        Bei fehlenden Daten wird das Listing durchgelassen,
        damit es manuell geprüft werden kann.
        """
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
            # Plausibilität: Immobilienpreise in der Schweiz
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
