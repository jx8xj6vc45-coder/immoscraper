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

    def _find_chromium_executable(self) -> Optional[str]:
        """Findet den Chromium-Executable-Pfad für verschiedene Playwright-Versionen."""
        import os
        import glob
        import platform

        # Plattform-spezifische Cache-Verzeichnisse
        if platform.system() == 'Darwin':  # macOS
            cache_dirs = [
                os.path.expanduser('~/Library/Caches/ms-playwright'),
                os.path.expanduser('~/.cache/ms-playwright'),
            ]
            patterns_suffix = [
                'chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium',
                'chromium-*/chrome-mac-*/Chromium.app/Contents/MacOS/Chromium',
            ]
        else:  # Linux
            cache_dirs = [
                os.path.expanduser('~/.cache/ms-playwright'),
            ]
            patterns_suffix = [
                'chromium-*/chrome-linux/chrome',
                'chromium-*/chrome-*/chrome',
            ]

        for cache_dir in cache_dirs:
            if not os.path.exists(cache_dir):
                continue

            for suffix in patterns_suffix:
                pattern = f'{cache_dir}/{suffix}'
                matches = glob.glob(pattern)
                if matches:
                    # Neueste Version nehmen
                    matches.sort(reverse=True)
                    if os.path.isfile(matches[0]) and os.access(matches[0], os.X_OK):
                        return matches[0]

        return None

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

            # Versuche expliziten Pfad zu finden für Kompatibilität
            executable_path = self._find_chromium_executable()

            launch_args = [
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--start-maximized',
                '--disable-features=AsyncDns',  # System-DNS verwenden
            ]

            launch_kwargs = {
                'headless': True,
                'args': launch_args,
            }
            if executable_path:
                launch_kwargs['executable_path'] = executable_path
                logger.debug(f"[{self.get_name()}] Nutze Chromium: {executable_path}")

            browser = pw.chromium.launch(**launch_kwargs)

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

        Multi-Layer Ansatz für maximale Erfolgsrate:
        1. Patchright/Chromium (umgeht CDP-Erkennung)
        2. Firefox als Fallback bei DNS-Problemen
        3. Proxy-Unterstützung (Residential/Mobile)
        4. Bezier-Kurven Mausbewegungen
        5. Realistische Delays und Verhalten
        6. Fallback zu curl_cffi bei Fehlschlag
        """
        url = self.build_search_url()

        # Zuerst Chromium-basierte Methode versuchen
        result = self._try_browser_stealth(url, use_firefox=False)
        if result is not None:
            return result

        # Bei DNS-Fehler: Firefox versuchen (anderer DNS-Resolver)
        logger.info(f"[{self.get_name()}] Chromium fehlgeschlagen, versuche Firefox...")
        result = self._try_browser_stealth(url, use_firefox=True)
        if result is not None:
            return result

        # Letzter Fallback: curl_cffi mit TLS-Fingerprinting
        logger.info(f"[{self.get_name()}] Browser fehlgeschlagen, versuche curl_cffi...")
        return self._try_curl_cffi(url)

    def _try_browser_stealth(self, url: str, use_firefox: bool = False) -> Optional[List[Listing]]:
        """Versucht Browser-basiertes Scraping mit Stealth.

        Args:
            url: Die zu ladende URL
            use_firefox: Wenn True, Firefox statt Chromium verwenden (für DNS-Probleme)
        """
        pw = None
        browser = None
        try:
            # Versuche Patchright zuerst (umgeht CDP-Erkennung) - nur für Chromium
            use_patchright = False
            if not use_firefox:
                try:
                    from patchright.sync_api import sync_playwright
                    logger.info(f"[{self.get_name()}] Fetching mit Patchright: {url}")
                    use_patchright = True
                except ImportError:
                    from playwright.sync_api import sync_playwright
                    logger.info(f"[{self.get_name()}] Patchright nicht installiert, nutze Playwright Chromium: {url}")
            else:
                from playwright.sync_api import sync_playwright
                logger.info(f"[{self.get_name()}] Nutze Firefox (DNS-Fallback): {url}")

            try:
                from playwright_stealth import stealth_sync
                has_stealth = True
            except ImportError:
                has_stealth = False

            pw = sync_playwright().start()

            # Proxy aus Config laden (falls vorhanden)
            proxy_config = self.config.get('proxy', None)
            proxy = None
            if proxy_config and proxy_config.get('enabled'):
                proxy = {
                    'server': proxy_config.get('server'),
                    'username': proxy_config.get('username'),
                    'password': proxy_config.get('password'),
                }
                logger.info(f"[{self.get_name()}] Nutze Proxy: {proxy_config.get('server')}")

            # Realistische Browser-Konfigurationen
            if use_firefox:
                # Firefox User-Agents
                configs = [
                    {
                        'viewport': {'width': 1920, 'height': 1080},
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
                        'platform': 'Windows',
                    },
                    {
                        'viewport': {'width': 1440, 'height': 900},
                        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0',
                        'platform': 'macOS',
                    },
                ]
            else:
                # Chrome User-Agents
                configs = [
                    {
                        'viewport': {'width': 1920, 'height': 1080},
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                        'platform': 'Windows',
                    },
                    {
                        'viewport': {'width': 1440, 'height': 900},
                        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                        'platform': 'macOS',
                    },
                    {
                        'viewport': {'width': 1536, 'height': 864},
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                        'platform': 'Windows',
                    },
                ]
            config = random.choice(configs)

            # Browser starten
            launch_kwargs = {
                'headless': True,
                'proxy': proxy,
            }

            if use_firefox:
                # Firefox braucht weniger Args
                browser = pw.firefox.launch(**launch_kwargs)
            else:
                # Chromium-spezifische Args
                launch_args = [
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    f'--window-size={config["viewport"]["width"]},{config["viewport"]["height"]}',
                    '--disable-extensions',
                    '--disable-plugins-discovery',
                    '--disable-default-apps',
                    # DNS über System statt Chromium's eigenen Resolver
                    '--disable-features=AsyncDns',
                ]
                launch_kwargs['args'] = launch_args

                # Versuche expliziten Pfad zu finden für Kompatibilität
                executable_path = self._find_chromium_executable()
                if executable_path:
                    launch_kwargs['executable_path'] = executable_path
                    logger.debug(f"[{self.get_name()}] Nutze Chromium: {executable_path}")

                browser = pw.chromium.launch(**launch_kwargs)

            # Browser-spezifische Headers
            if use_firefox:
                extra_headers = {
                    'Accept-Language': 'de-CH,de;q=0.8,en-US;q=0.5,en;q=0.3',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                }
            else:
                extra_headers = {
                    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'sec-ch-ua': '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': f'"{config["platform"]}"',
                    'sec-fetch-dest': 'document',
                    'sec-fetch-mode': 'navigate',
                    'sec-fetch-site': 'none',
                    'sec-fetch-user': '?1',
                    'upgrade-insecure-requests': '1',
                }

            context = browser.new_context(
                locale='de-CH',
                timezone_id='Europe/Zurich',
                viewport=config['viewport'],
                user_agent=config['user_agent'],
                extra_http_headers=extra_headers,
            )
            page = context.new_page()

            if has_stealth and not use_patchright and not use_firefox:
                stealth_sync(page)

            # Umfassende JS-Injection für Fingerprint-Maskierung (nur für Chromium)
            if not use_firefox:
                page.add_init_script("""
                // Webdriver verstecken
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                delete navigator.__proto__.webdriver;

                // Plugins realistisch
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const arr = [
                            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
                            {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''},
                        ];
                        arr.item = (i) => arr[i];
                        arr.namedItem = (n) => arr.find(p => p.name === n);
                        arr.refresh = () => {};
                        return arr;
                    }
                });

                // Languages
                Object.defineProperty(navigator, 'languages', {get: () => ['de-CH', 'de', 'en-US', 'en']});
                Object.defineProperty(navigator, 'language', {get: () => 'de-CH'});

                // Chrome Objekt
                window.chrome = {
                    runtime: {
                        connect: () => {},
                        sendMessage: () => {},
                        onMessage: {addListener: () => {}},
                    },
                    loadTimes: () => ({
                        commitLoadTime: Date.now() / 1000 - Math.random() * 2,
                        connectionInfo: 'http/1.1',
                        finishDocumentLoadTime: Date.now() / 1000 - Math.random(),
                        finishLoadTime: Date.now() / 1000 - Math.random() * 0.5,
                        firstPaintAfterLoadTime: 0,
                        firstPaintTime: Date.now() / 1000 - Math.random() * 1.5,
                        navigationType: 'Other',
                        npnNegotiatedProtocol: 'unknown',
                        requestTime: Date.now() / 1000 - Math.random() * 3,
                        startLoadTime: Date.now() / 1000 - Math.random() * 2.5,
                        wasAlternateProtocolAvailable: false,
                        wasFetchedViaSpdy: false,
                        wasNpnNegotiated: false,
                    }),
                    csi: () => ({
                        startE: Date.now() - Math.floor(Math.random() * 3000),
                        onloadT: Date.now() - Math.floor(Math.random() * 1000),
                        pageT: Math.random() * 5000,
                    }),
                    app: {isInstalled: false, InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'}, RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'}},
                };

                // WebGL Fingerprint
                const getParameterOrig = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {
                    if (param === 37445) return 'Intel Inc.';
                    if (param === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameterOrig.call(this, param);
                };

                // Permissions
                const origQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (params) => (
                    params.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : origQuery(params)
                );

                // Hardware Concurrency
                Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});

                // Device Memory
                Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});

                // Connection
                Object.defineProperty(navigator, 'connection', {
                    get: () => ({
                        effectiveType: '4g',
                        rtt: 50,
                        downlink: 10,
                        saveData: false,
                    })
                });
            """)

            # Seite laden
            page.goto(url, wait_until='domcontentloaded', timeout=60000)

            # Realistische initiale Wartezeit
            time.sleep(random.uniform(2.0, 4.0))

            # Menschliche Mausbewegungen mit Bezier-Kurven
            self._human_mouse_movement(page)

            # Cookie-Banner behandeln
            self._handle_cookie_banner(page)

            # Warten auf vollständiges Laden
            page.wait_for_timeout(random.randint(2000, 4000))

            # Mehr menschliches Verhalten
            self._human_scroll_behavior(page)

            # Bot-Schutz prüfen
            content = page.content()
            if self._is_blocked(content):
                logger.warning(f"[{self.get_name()}] Bot-Schutz erkannt nach erstem Versuch")
                # Längere Wartezeit und mehr Interaktion
                time.sleep(random.uniform(5.0, 10.0))
                self._human_mouse_movement(page)
                self._human_scroll_behavior(page)
                time.sleep(random.uniform(3.0, 5.0))
                content = page.content()

                if self._is_blocked(content):
                    logger.error(f"[{self.get_name()}] Bot-Schutz konnte nicht umgangen werden")
                    context.close()
                    return None

            # JS-Variablen extrahieren
            self._extract_js_state(page)

            listings = self.parse_listings(content)
            filtered = self._filter_listings(listings)

            logger.info(f"[{self.get_name()}] {len(listings)} geparst, {len(filtered)} nach Filter")

            time.sleep(random.uniform(1.0, 2.0))
            context.close()
            return filtered

        except Exception as e:
            logger.error(f"[{self.get_name()}] Browser-Fehler: {e}")
            return None
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

    def _try_curl_cffi(self, url: str) -> List[Listing]:
        """Fallback mit curl_cffi für TLS-Fingerprinting."""
        try:
            from curl_cffi import requests as curl_requests
            logger.info(f"[{self.get_name()}] Versuche curl_cffi mit Chrome TLS-Fingerprint")

            # Chrome-ähnlicher TLS-Fingerprint
            response = curl_requests.get(
                url,
                impersonate='chrome120',
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'sec-ch-ua': '"Chromium";v="120", "Not:A-Brand";v="8", "Google Chrome";v="120"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    'sec-fetch-dest': 'document',
                    'sec-fetch-mode': 'navigate',
                    'sec-fetch-site': 'none',
                    'upgrade-insecure-requests': '1',
                },
                timeout=30,
            )

            if response.status_code == 200:
                content = response.text
                if not self._is_blocked(content):
                    listings = self.parse_listings(content)
                    filtered = self._filter_listings(listings)
                    logger.info(f"[{self.get_name()}] curl_cffi erfolgreich: {len(filtered)} Listings")
                    return filtered

            logger.warning(f"[{self.get_name()}] curl_cffi fehlgeschlagen: Status {response.status_code}")
            return []

        except ImportError:
            logger.warning(f"[{self.get_name()}] curl_cffi nicht installiert (pip install curl_cffi)")
            return []
        except Exception as e:
            logger.error(f"[{self.get_name()}] curl_cffi Fehler: {e}")
            return []

    def _is_blocked(self, content: str) -> bool:
        """Prüft ob Bot-Schutz aktiv ist."""
        content_lower = content.lower()
        block_indicators = [
            'captcha', 'datadome', 'blocked', 'robot', 'unusual traffic',
            'access denied', 'please verify', 'security check',
            'are you a robot', 'prove you are human',
        ]
        return any(indicator in content_lower for indicator in block_indicators)

    def _human_mouse_movement(self, page):
        """Simuliert menschliche Mausbewegungen mit Bezier-Kurven."""
        try:
            import math

            def bezier_curve(t, p0, p1, p2, p3):
                """Kubische Bezier-Kurve für natürliche Bewegung."""
                return (
                    (1-t)**3 * p0 +
                    3 * (1-t)**2 * t * p1 +
                    3 * (1-t) * t**2 * p2 +
                    t**3 * p3
                )

            # Startposition
            start_x, start_y = random.randint(100, 300), random.randint(100, 200)
            page.mouse.move(start_x, start_y)
            time.sleep(random.uniform(0.1, 0.3))

            # 2-3 Bewegungen mit Bezier-Kurven
            for _ in range(random.randint(2, 3)):
                end_x = random.randint(200, 800)
                end_y = random.randint(150, 500)

                # Kontrollpunkte für natürliche Kurve
                cp1_x = start_x + random.randint(-100, 100)
                cp1_y = start_y + random.randint(-50, 50)
                cp2_x = end_x + random.randint(-100, 100)
                cp2_y = end_y + random.randint(-50, 50)

                # Bewegung in Schritten
                steps = random.randint(15, 30)
                for i in range(steps + 1):
                    t = i / steps
                    # Leichte Variation für natürlichere Bewegung
                    t_varied = t + random.uniform(-0.02, 0.02)
                    t_varied = max(0, min(1, t_varied))

                    x = bezier_curve(t_varied, start_x, cp1_x, cp2_x, end_x)
                    y = bezier_curve(t_varied, start_y, cp1_y, cp2_y, end_y)

                    page.mouse.move(int(x), int(y))
                    time.sleep(random.uniform(0.01, 0.03))

                start_x, start_y = end_x, end_y
                time.sleep(random.uniform(0.2, 0.5))

        except Exception:
            # Fallback zu einfacher Bewegung
            try:
                page.mouse.move(random.randint(100, 500), random.randint(100, 400))
            except Exception:
                pass

    def _human_scroll_behavior(self, page):
        """Simuliert menschliches Scroll-Verhalten."""
        try:
            # Langsames Scrollen nach unten
            total_scroll = random.randint(300, 600)
            scroll_steps = random.randint(3, 6)
            step_size = total_scroll // scroll_steps

            for _ in range(scroll_steps):
                # Variable Scroll-Distanz
                scroll = step_size + random.randint(-30, 30)
                page.mouse.wheel(0, scroll)
                time.sleep(random.uniform(0.3, 0.8))

            # Kurze Pause zum "Lesen"
            time.sleep(random.uniform(1.0, 2.0))

            # Etwas nach oben scrollen (wie ein Mensch der zurückschaut)
            page.mouse.wheel(0, -random.randint(50, 150))
            time.sleep(random.uniform(0.5, 1.0))

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
