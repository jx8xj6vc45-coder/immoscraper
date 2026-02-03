"""Geocoding-Service: Adresse → Koordinaten."""

import logging
import time
import os
import requests
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

# Cache um API-Calls zu reduzieren
_geocode_cache = {}


class GeocodingService:
    """Konvertiert Adressen in Geo-Koordinaten."""

    def __init__(self, config):
        self.config = config
        api_provider = config.get('apis', {}).get('geocoding', 'nominatim')
        self.google_api_key = os.getenv('GOOGLE_MAPS_API_KEY', '')

        if api_provider == 'google' and self.google_api_key:
            self._geocode_fn = self._geocode_google
        else:
            self._geocode_fn = self._geocode_nominatim
            self._nominatim = Nominatim(
                user_agent='immobilien-suchagent/1.0',
                timeout=10,
            )

    def geocode(self, address: str, city: str = '') -> dict:
        """Gibt Lat/Lon für eine Adresse zurück.

        Versucht zuerst die volle Adresse, dann als Fallback das Ortszentrum.

        Returns:
            dict mit 'latitude', 'longitude' oder leeres dict.
        """
        if not address and not city:
            return {}

        full_address = f"{address}, {city}, Schweiz" if address else f"{city}, Schweiz"
        full_address = full_address.strip(', ')

        # Cache prüfen
        cache_key = full_address.lower()
        if cache_key in _geocode_cache:
            return _geocode_cache[cache_key]

        # Versuche volle Adresse
        result = self._geocode_fn(full_address)
        if result:
            _geocode_cache[cache_key] = result
            return result

        # Fallback: Nur Ortszentrum wenn city vorhanden
        if city:
            city_only = f"{city}, Schweiz"
            city_cache_key = city_only.lower()

            # Cache für Ortszentrum prüfen
            if city_cache_key in _geocode_cache:
                logger.debug(f"Fallback auf Ortszentrum (cached): {city}")
                _geocode_cache[cache_key] = _geocode_cache[city_cache_key]
                return _geocode_cache[city_cache_key]

            # Ortszentrum geocoden
            logger.info(f"Adresse nicht gefunden, verwende Ortszentrum: {city}")
            result = self._geocode_fn(city_only)
            if result:
                _geocode_cache[city_cache_key] = result
                _geocode_cache[cache_key] = result  # Original-Adresse auch cachen
                return result

        return {}

    def _geocode_nominatim(self, address: str) -> dict:
        """Geocoding via OpenStreetMap Nominatim (kostenlos)."""
        try:
            # Rate Limit: max 1 request/sec
            time.sleep(1.1)
            location = self._nominatim.geocode(address)
            if location:
                return {
                    'latitude': location.latitude,
                    'longitude': location.longitude,
                }
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            logger.warning(f"Nominatim Fehler für '{address}': {e}")
        except Exception as e:
            logger.error(f"Geocoding Fehler für '{address}': {e}")

        return {}

    def _geocode_google(self, address: str) -> dict:
        """Geocoding via Google Maps API."""
        try:
            resp = requests.get(
                'https://maps.googleapis.com/maps/api/geocode/json',
                params={
                    'address': address,
                    'key': self.google_api_key,
                    'region': 'ch',
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get('status') == 'OK' and data.get('results'):
                loc = data['results'][0]['geometry']['location']
                return {
                    'latitude': loc['lat'],
                    'longitude': loc['lng'],
                }
        except Exception as e:
            logger.error(f"Google Geocoding Fehler für '{address}': {e}")

        return {}
