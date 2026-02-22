"""Scraper für Homegate.ch."""

import re
import json
import logging
import time
import random
from typing import List
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing

logger = logging.getLogger(__name__)


class HomegateScraper(BaseScraper):
    """Scraper für homegate.ch.

    Extrahiert Listing-Daten aus dem window.__INITIAL_STATE__ JSON-Blob.
    Gleiche Datenstruktur wie ImmoScout24.ch.
    Unterstützt Pagination (mehrere Seiten).
    """

    BASE_URL = 'https://www.homegate.ch'
    MAX_PAGES = 5  # Maximal 5 Seiten laden (ca. 100 Listings)

    def get_name(self) -> str:
        return 'homegate'

    def build_search_url(self, page: int = 1, room_param: str = 'ac') -> str:
        """Baut die Such-URL.

        Args:
            page: Seitennummer
            room_param: 'ac' oder 'nrf' - welcher Parameter für Zimmerfilter
        """
        min_rooms = self.criteria.get('min_rooms', 4.5)
        min_rooms_int = int(min_rooms)

        url = (
            f"{self.BASE_URL}/buy/real-estate/canton-zurich/matching-list"
            f"?{room_param}={min_rooms_int}"
        )
        if page > 1:
            url += f"&ep={page}"

        return url

    def search(self) -> List[Listing]:
        """Überschreibt die Basis-Suche um mehrere Seiten zu laden.

        Testet beide Zimmer-Parameter (ac und nrf) und verwendet den besseren.
        """
        # TEST: Vergleiche beide Parameter
        min_rooms = self.criteria.get('min_rooms', 4.5)

        # Test mit 'ac' Parameter
        logger.info(f"[homegate] TEST: Vergleiche 'ac' vs 'nrf' Parameter für Zimmerfilter")

        ac_url = self.build_search_url(page=1, room_param='ac')
        logger.info(f"[homegate] Test URL mit ac: {ac_url}")
        ac_listings = self._search_page(1, room_param='ac')
        ac_with_rooms = [l for l in ac_listings if l.rooms and l.rooms >= min_rooms]
        logger.info(f"[homegate] ac={int(min_rooms)}: {len(ac_listings)} total, {len(ac_with_rooms)} mit >= {min_rooms} Zimmer")

        # Test mit 'nrf' Parameter
        nrf_url = self.build_search_url(page=1, room_param='nrf')
        logger.info(f"[homegate] Test URL mit nrf: {nrf_url}")
        nrf_listings = self._search_page(1, room_param='nrf')
        nrf_with_rooms = [l for l in nrf_listings if l.rooms and l.rooms >= min_rooms]
        logger.info(f"[homegate] nrf={int(min_rooms)}: {len(nrf_listings)} total, {len(nrf_with_rooms)} mit >= {min_rooms} Zimmer")

        # Entscheide welcher Parameter besser ist
        if len(nrf_with_rooms) > len(ac_with_rooms):
            best_param = 'nrf'
            logger.info(f"[homegate] ERGEBNIS: 'nrf' ist besser ({len(nrf_with_rooms)} vs {len(ac_with_rooms)} passende Listings)")
        elif len(ac_with_rooms) > len(nrf_with_rooms):
            best_param = 'ac'
            logger.info(f"[homegate] ERGEBNIS: 'ac' ist besser ({len(ac_with_rooms)} vs {len(nrf_with_rooms)} passende Listings)")
        else:
            best_param = 'ac'  # Default
            logger.info(f"[homegate] ERGEBNIS: Beide gleich ({len(ac_with_rooms)} passende Listings), verwende 'ac'")

        # Jetzt normale Suche mit bestem Parameter
        all_listings = []
        seen_ids = set()

        for page in range(1, self.MAX_PAGES + 1):
            logger.info(f"[homegate] Lade Seite {page} (param={best_param})...")

            # Führe die Basis-Suche für diese Seite aus
            page_listings = self._search_page(page, room_param=best_param)

            if not page_listings:
                logger.info(f"[homegate] Seite {page}: keine weiteren Listings")
                break

            # Deduplizierung
            new_count = 0
            for listing in page_listings:
                if listing.external_id not in seen_ids:
                    seen_ids.add(listing.external_id)
                    all_listings.append(listing)
                    new_count += 1

            logger.info(f"[homegate] Seite {page}: {new_count} neue Listings")

            # Wenn weniger als 20 Listings, sind wir am Ende
            if len(page_listings) < 20:
                break

            # Rate limiting zwischen Seiten
            time.sleep(random.uniform(2.0, 4.0))

        logger.info(f"[homegate] Total: {len(all_listings)} Listings von {page} Seiten")
        return all_listings

    def _search_page(self, page: int, room_param: str = 'ac') -> List[Listing]:
        """Lädt eine einzelne Seite und gibt die Listings zurück."""
        pw = None
        browser = None
        context = None
        try:
            url = self.build_search_url(page, room_param=room_param)
            logger.debug(f"[homegate] Fetching {url}")

            from playwright.sync_api import sync_playwright
            import platform

            try:
                from playwright_stealth import stealth_sync
                has_stealth = True
            except ImportError:
                has_stealth = False

            pw = sync_playwright().start()

            # Auf macOS: Firefox nutzen (Chromium hat oft Probleme)
            use_firefox = platform.system() == 'Darwin'

            if use_firefox:
                browser = pw.firefox.launch(headless=True)
            else:
                # Linux: Chromium mit Optimierungen
                executable_path = self._find_chromium_executable()

                launch_args = [
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--window-size=1920,1080',
                    '--disable-features=AsyncDns',
                ]

                launch_kwargs = {
                    'headless': True,
                    'args': launch_args,
                }
                if executable_path:
                    launch_kwargs['executable_path'] = executable_path

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
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                },
            )
            page_obj = context.new_page()

            if has_stealth:
                stealth_sync(page_obj)

            # Einfache JS-Injection
            page_obj.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
            """)

            # Seite laden (domcontentloaded ist zuverlässiger als networkidle)
            page_obj.goto(url, wait_until='domcontentloaded', timeout=60000)
            # Warten bis dynamischer Content geladen ist
            page_obj.wait_for_timeout(5000)

            # JS-State extrahieren
            try:
                js_state = page_obj.evaluate(
                    '() => { try { return JSON.stringify(window.__INITIAL_STATE__); } catch(e) { return null; } }'
                )
                if js_state and js_state != 'null':
                    self._js_initial_state = js_state
            except Exception:
                self._js_initial_state = None

            content = page_obj.content()

            # Context schliessen bevor wir parsen
            try:
                context.close()
                context = None
            except Exception:
                pass

            listings = self.parse_listings(content)

            # Filter anwenden
            filtered = []
            for listing in listings:
                listing.platform = self.get_name()
                if self.meets_criteria(listing):
                    filtered.append(listing)
                else:
                    # Debug: Warum gefiltert?
                    max_price = self.criteria.get('max_price', 2_500_000)
                    min_rooms = self.criteria.get('min_rooms', 4.5)
                    min_area = self.criteria.get('min_area_sqm', 120)
                    reasons = []
                    if listing.price and listing.price > max_price:
                        reasons.append(f"Preis {listing.price} > {max_price}")
                    if listing.rooms and listing.rooms < min_rooms:
                        reasons.append(f"Zimmer {listing.rooms} < {min_rooms}")
                    if listing.area_sqm and listing.area_sqm < min_area:
                        reasons.append(f"Fläche {listing.area_sqm} < {min_area}")
                    if listing.outdoor_type == 'none':
                        reasons.append("Kein Outdoor")
                    if not reasons:
                        reasons.append(f"price={listing.price}, rooms={listing.rooms}, area={listing.area_sqm}")
                    logger.info(f"[homegate] Gefiltert: {listing.external_id} - {', '.join(reasons)}")

            return filtered

        except Exception as e:
            logger.error(f"[homegate] Seite {page} Fehler: {e}")
            return []
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
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

    def parse_listings(self, content: str) -> List[Listing]:
        listings = []

        # Strategie 0: JS-evaluierter __INITIAL_STATE__ (direkt aus Browser)
        js_state = getattr(self, '_js_initial_state', None)
        if js_state:
            try:
                data = json.loads(js_state)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[homegate] {len(result_listings)} Listings via JS evaluate")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[homegate] JS evaluate fehlgeschlagen: {e}")

        # Strategie 0b: JS-evaluierter __NEXT_DATA__
        js_next = getattr(self, '_js_next_data', None)
        if js_next:
            try:
                data = json.loads(js_next)
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    logger.info(f"[homegate] {len(result_listings)} Listings via JS __NEXT_DATA__")
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[homegate] JS __NEXT_DATA__ fehlgeschlagen: {e}")

        # Strategie 1: __INITIAL_STATE__ via Regex auf Raw-HTML
        listings.extend(self._parse_initial_state(content))

        # Strategie 2: __NEXT_DATA__ (Next.js) als Fallback
        if not listings:
            listings.extend(self._parse_next_data(content))

        # Strategie 3: JSON-Blob Suche
        if not listings:
            listings.extend(self._parse_via_evaluate(content))

        return listings

    def _extract_json_from_html(self, content: str, var_name: str) -> dict:
        """Extrahiert JSON aus einem window.VAR_NAME=... Pattern im HTML."""
        pattern = re.escape(var_name) + r'\s*=\s*(\{.+?\})\s*;?\s*</script>'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        pattern2 = re.escape(var_name) + r'\s*=\s*(\{.*?\})\s*;\s*$'
        match2 = re.search(pattern2, content, re.DOTALL | re.MULTILINE)
        if match2:
            return json.loads(match2.group(1))

        return None

    def _parse_initial_state(self, content: str) -> List[Listing]:
        """Extrahiert Listings aus window.__INITIAL_STATE__ JSON."""
        listings = []

        data = self._extract_json_from_html(content, 'window.__INITIAL_STATE__')
        if not data:
            soup = BeautifulSoup(content, 'lxml')
            for tag in soup.find_all('script'):
                text = tag.string or tag.get_text()
                if text and '__INITIAL_STATE__' in text:
                    try:
                        idx = text.index('__INITIAL_STATE__')
                        eq_idx = text.index('=', idx)
                        json_text = text[eq_idx + 1:].strip().rstrip(';')
                        data = json.loads(json_text)
                        break
                    except (ValueError, json.JSONDecodeError):
                        continue

        if not data:
            logger.warning("[homegate] Kein __INITIAL_STATE__ gefunden")
            return listings

        result_listings = self._find_listings_in_json(data)
        if not result_listings:
            logger.warning("[homegate] Keine Listings im JSON gefunden")
            return listings

        for item in result_listings:
            listing = self._item_to_listing(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_next_data(self, content: str) -> List[Listing]:
        """Fallback: Extrahiert aus __NEXT_DATA__ (Next.js)."""
        listings = []
        soup = BeautifulSoup(content, 'lxml')
        tag = soup.find('script', id='__NEXT_DATA__')
        if not tag:
            return listings

        try:
            text = tag.string or tag.get_text()
            data = json.loads(text)
        except (json.JSONDecodeError, AttributeError):
            return listings

        result_listings = self._find_listings_in_json(data)
        if result_listings:
            logger.info(f"[homegate] {len(result_listings)} Listings via __NEXT_DATA__")
            for item in result_listings:
                listing = self._item_to_listing(item)
                if listing:
                    listings.append(listing)

        return listings

    def _parse_via_evaluate(self, content: str) -> List[Listing]:
        """Fallback: Suche nach JSON-Blobs die Listing-Daten enthalten."""
        listings = []

        for match in re.finditer(r'(\{"resultList":\{.*?\})\s*;?\s*</script>', content, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                result_listings = self._find_listings_in_json(data)
                if result_listings:
                    for item in result_listings:
                        listing = self._item_to_listing(item)
                        if listing:
                            listings.append(listing)
                    return listings
            except json.JSONDecodeError:
                continue

        return listings

    def _find_listings_in_json(self, data: dict) -> list:
        """Findet Listings-Array in verschachteltem JSON."""
        try:
            result = data['resultList']['search']['fullSearch']['result']['listings']
            logger.info(f"[homegate] Listings gefunden via direkt: resultList.search.fullSearch.result.listings")
            return result
        except (KeyError, TypeError):
            pass

        for path in [
            ['props', 'pageProps', 'resultList', 'search', 'fullSearch', 'result', 'listings'],
            ['props', 'pageProps', 'listings'],
            ['props', 'pageProps', 'searchResult', 'listings'],
            ['searchResult', 'listings'],
            ['data', 'searchResult', 'listings'],
        ]:
            obj = data
            try:
                for key in path:
                    obj = obj[key]
                if isinstance(obj, list) and len(obj) > 0:
                    logger.info(f"[homegate] Listings gefunden via Pfad: {'.'.join(path)}")
                    # Log first item structure
                    if obj and isinstance(obj[0], dict):
                        logger.info(f"[homegate] Erstes Item Keys: {list(obj[0].keys())[:8]}")
                    return obj
            except (KeyError, TypeError):
                continue

        result = self._find_key_recursive(data, 'listings', max_depth=6)
        if result:
            logger.info(f"[homegate] Listings gefunden via rekursive Suche")
            if result and isinstance(result[0], dict):
                logger.info(f"[homegate] Erstes Item Keys: {list(result[0].keys())[:8]}")
        return result

    def _find_key_recursive(self, obj, target_key, max_depth=6, depth=0):
        """Sucht rekursiv nach einem Key der eine Liste enthält."""
        if depth > max_depth:
            return None
        if isinstance(obj, dict):
            if target_key in obj and isinstance(obj[target_key], list) and len(obj[target_key]) > 3:
                return obj[target_key]
            for v in obj.values():
                result = self._find_key_recursive(v, target_key, max_depth, depth + 1)
                if result:
                    return result
        return None

    def _item_to_listing(self, item: dict) -> Listing:
        """Konvertiert ein JSON-Item in ein Listing-Objekt."""
        try:
            inner = item.get('listing', item)

            # Debug: Log structure
            item_id = inner.get('id', 'unknown')
            has_listing_key = 'listing' in item
            logger.info(f"[homegate] Item: id={item_id}, has_listing_key={has_listing_key}, inner_keys={list(inner.keys())[:6]}")

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
                try:
                    listing.area_sqm = int(float(living_space))
                except (ValueError, TypeError):
                    pass

            # Debug: Log raw room data from JSON
            logger.info(f"[homegate] Raw JSON für {listing.external_id}: numberOfRooms={chars.get('numberOfRooms')}, "
                       f"all_char_keys={list(chars.keys())}")

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

            logger.info(f"[homegate] Listing erstellt: {listing.external_id} - {listing.title[:40] if listing.title else 'no title'}")
            return listing

        except Exception as e:
            logger.warning(f"[homegate] Konvertierung fehlgeschlagen: {e} - item keys: {list(item.keys())[:5] if isinstance(item, dict) else type(item)}")
            return None
