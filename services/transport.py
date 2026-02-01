"""ÖV-Distanz-Berechnung zum HB Zürich."""

import logging
import os
import requests
import time

logger = logging.getLogger(__name__)

# Zürich HB Koordinaten
HB_ZURICH = {'lat': 47.3783, 'lon': 8.5404}

# Cache für Reisezeiten
_travel_cache = {}


class TransportService:
    """Berechnet ÖV-Reisezeiten zum HB Zürich."""

    def __init__(self, config):
        self.config = config
        self.google_api_key = os.getenv('GOOGLE_MAPS_API_KEY', '')
        api_provider = config.get('apis', {}).get('transport', 'sbb')

        if api_provider == 'google' and self.google_api_key:
            self._calc_fn = self._calc_google
        else:
            self._calc_fn = self._calc_sbb

    def get_travel_time(self, origin_lat: float, origin_lon: float) -> int:
        """Berechnet Reisezeit in Minuten zum HB Zürich via ÖV.

        Returns:
            Reisezeit in Minuten oder -1 bei Fehler.
        """
        if not origin_lat or not origin_lon:
            return -1

        cache_key = f"{round(origin_lat, 4)},{round(origin_lon, 4)}"
        if cache_key in _travel_cache:
            return _travel_cache[cache_key]

        result = self._calc_fn(origin_lat, origin_lon)
        if result >= 0:
            _travel_cache[cache_key] = result
        return result

    def get_travel_time_from_city(self, city: str) -> int:
        """Berechnet Reisezeit basierend auf Stadtname (SBB API)."""
        if not city:
            return -1

        cache_key = f"city:{city.lower()}"
        if cache_key in _travel_cache:
            return _travel_cache[cache_key]

        result = self._calc_sbb_by_name(city)
        if result >= 0:
            _travel_cache[cache_key] = result
        return result

    def _calc_sbb(self, lat: float, lon: float) -> int:
        """Berechnet Reisezeit via SBB/transport.opendata.ch API."""
        try:
            time.sleep(0.5)  # Rate limiting
            resp = requests.get(
                'https://transport.opendata.ch/v1/connections',
                params={
                    'from': f"{lat},{lon}",
                    'to': 'Zürich HB',
                    'limit': 3,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            connections = data.get('connections', [])
            if connections:
                # Kürzeste Verbindung nehmen
                durations = []
                for conn in connections:
                    duration_str = conn.get('duration', '')
                    minutes = self._parse_duration(duration_str)
                    if minutes > 0:
                        durations.append(minutes)

                if durations:
                    return min(durations)

        except Exception as e:
            logger.warning(f"SBB API Fehler: {e}")

        return -1

    def _calc_sbb_by_name(self, city: str) -> int:
        """Berechnet Reisezeit von Stadtname via SBB API."""
        try:
            time.sleep(0.5)
            resp = requests.get(
                'https://transport.opendata.ch/v1/connections',
                params={
                    'from': city,
                    'to': 'Zürich HB',
                    'limit': 3,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            connections = data.get('connections', [])
            if connections:
                durations = []
                for conn in connections:
                    duration_str = conn.get('duration', '')
                    minutes = self._parse_duration(duration_str)
                    if minutes > 0:
                        durations.append(minutes)
                if durations:
                    return min(durations)

        except Exception as e:
            logger.warning(f"SBB API Fehler für {city}: {e}")

        return -1

    def _calc_google(self, lat: float, lon: float) -> int:
        """Berechnet Reisezeit via Google Maps Directions API."""
        try:
            resp = requests.get(
                'https://maps.googleapis.com/maps/api/directions/json',
                params={
                    'origin': f"{lat},{lon}",
                    'destination': f"{HB_ZURICH['lat']},{HB_ZURICH['lon']}",
                    'mode': 'transit',
                    'key': self.google_api_key,
                    'region': 'ch',
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get('status') == 'OK':
                routes = data.get('routes', [])
                if routes:
                    leg = routes[0].get('legs', [{}])[0]
                    duration_sec = leg.get('duration', {}).get('value', 0)
                    return duration_sec // 60

        except Exception as e:
            logger.warning(f"Google Directions Fehler: {e}")

        return -1

    @staticmethod
    def _parse_duration(duration_str: str) -> int:
        """Parsed SBB Duration-String 'd days HH:MM:SS' in Minuten."""
        if not duration_str:
            return -1

        try:
            parts = duration_str.split(':')
            if len(parts) == 3:
                # Format: "00d00:25:00"
                day_hours = parts[0]
                days = 0
                hours = 0
                if 'd' in day_hours:
                    day_part, hour_part = day_hours.split('d')
                    days = int(day_part)
                    hours = int(hour_part)
                else:
                    hours = int(day_hours)
                minutes = int(parts[1])
                return days * 24 * 60 + hours * 60 + minutes
        except (ValueError, IndexError):
            pass

        return -1
