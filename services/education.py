"""Bildungs-Service: Schulen-Proximity und Maturitätsquoten."""

import logging
import time
import requests
from geopy.distance import geodesic

logger = logging.getLogger(__name__)

# Cache für Schulen-Abfragen
_school_cache = {}


class EducationService:
    """Service für Bildungsdaten: Schulen in der Nähe und Maturitätsquoten."""

    def __init__(self, config):
        self.config = config

    def find_nearby_schools(self, lat: float, lon: float, radius_km: float = 5.0) -> list:
        """Findet Schulen im Umkreis via OpenStreetMap Overpass API.

        Args:
            lat: Breitengrad
            lon: Längengrad
            radius_km: Suchradius in km

        Returns:
            Liste von dicts mit 'name', 'type', 'distance_km', 'lat', 'lon'
        """
        if not lat or not lon:
            return []

        cache_key = f"{round(lat, 3)},{round(lon, 3)},{radius_km}"
        if cache_key in _school_cache:
            return _school_cache[cache_key]

        schools = self._query_overpass(lat, lon, radius_km)
        _school_cache[cache_key] = schools
        return schools

    def _query_overpass(self, lat: float, lon: float, radius_km: float) -> list:
        """Fragt Overpass API nach Schulen ab."""
        radius_m = int(radius_km * 1000)

        query = f"""
        [out:json][timeout:25];
        (
          node["amenity"="school"](around:{radius_m},{lat},{lon});
          way["amenity"="school"](around:{radius_m},{lat},{lon});
          node["amenity"="college"](around:{radius_m},{lat},{lon});
          way["amenity"="college"](around:{radius_m},{lat},{lon});
        );
        out center tags;
        """

        try:
            time.sleep(1)  # Rate limiting
            resp = requests.post(
                'https://overpass-api.de/api/interpreter',
                data={'data': query},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            schools = []
            for element in data.get('elements', []):
                school = self._parse_school_element(element, lat, lon)
                if school:
                    schools.append(school)

            # Sortiere nach Distanz
            schools.sort(key=lambda s: s['distance_km'])
            return schools

        except Exception as e:
            logger.warning(f"Overpass API Fehler: {e}")
            return []

    def _parse_school_element(self, element: dict, ref_lat: float, ref_lon: float) -> dict:
        """Parsed ein Overpass-Element zu einem Schulen-Dict."""
        tags = element.get('tags', {})
        name = tags.get('name', 'Unbekannte Schule')

        # Position bestimmen (center für ways)
        school_lat = element.get('lat') or element.get('center', {}).get('lat')
        school_lon = element.get('lon') or element.get('center', {}).get('lon')

        if not school_lat or not school_lon:
            return None

        # Distanz berechnen
        distance = geodesic(
            (ref_lat, ref_lon),
            (school_lat, school_lon)
        ).kilometers

        # Schultyp bestimmen
        school_type = self._classify_school(name, tags)

        return {
            'name': name,
            'type': school_type,
            'distance_km': round(distance, 2),
            'lat': school_lat,
            'lon': school_lon,
        }

    @staticmethod
    def _classify_school(name: str, tags: dict) -> str:
        """Klassifiziert den Schultyp."""
        name_lower = name.lower()
        isced = tags.get('isced:level', '')

        # Gymnasium / Kantonsschule
        if any(kw in name_lower for kw in ['gymnasium', 'kantonsschule', 'kanti', 'maturität']):
            return 'gymnasium'

        # Sekundarschule
        if any(kw in name_lower for kw in ['sekundar', 'oberstufe', 'sekundärschule']):
            return 'sekundarschule'

        # Primarschule
        if any(kw in name_lower for kw in ['primar', 'primarschule', 'grundschule', 'volksschule']):
            return 'primarschule'

        # ISCED-basierte Klassifikation
        if '3' in isced:
            return 'gymnasium'
        if '2' in isced:
            return 'sekundarschule'
        if '1' in isced:
            return 'primarschule'

        return 'schule'  # Allgemein

    def get_education_data(self, lat: float, lon: float) -> dict:
        """Holt alle Bildungsdaten für eine Position.

        Returns:
            dict mit 'nearby_schools', 'nearest_school_distance',
            'gymnasium_nearby'.
        """
        schools = self.find_nearby_schools(lat, lon)

        nearest_distance = None
        gymnasium_nearby = False

        if schools:
            nearest_distance = schools[0]['distance_km']
            gymnasium_nearby = any(
                s['type'] == 'gymnasium' and s['distance_km'] <= 5.0
                for s in schools
            )

        return {
            'nearby_schools': schools,
            'nearest_school_distance': nearest_distance,
            'gymnasium_nearby': gymnasium_nearby,
        }
