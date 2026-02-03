"""Scoring-Engine: Bewertet Immobilien-Listings auf einer 120-Punkte-Skala."""

import re
import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Prioritäts-Gemeinden für Lage-Score
PRIORITY_TIER1 = {
    'greifensee', 'uster', 'volketswil', 'schwerzenbach',
}
PRIORITY_TIER2 = {
    'wädenswil', 'wadenswil', 'richterswil', 'horgen',
}
ZURICH_CITY_VARIANTS = {
    'zürich', 'zurich', 'zuerich',
}


class ListingScorer:
    """Bewertet Listings nach dem 120-Punkte-System."""

    def __init__(self, config):
        self.config = config
        self.criteria = config.get('search_criteria', {})
        self._load_maturitaetsquoten()
        self._load_steuerfuss()

    def _load_maturitaetsquoten(self):
        """Lädt Maturitätsquoten aus JSON-Datei."""
        data_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'maturitaetsquoten.json'
        )
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                self.maturitaetsquoten = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Maturitätsquoten-Datei nicht gefunden, verwende leere Daten")
            self.maturitaetsquoten = {}

    def _load_steuerfuss(self):
        """Lädt Steuerfuss-Daten aus JSON-Datei."""
        data_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'steuerfuss.json'
        )
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                self.steuerfuss_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Steuerfuss-Datei nicht gefunden, verwende leere Daten")
            self.steuerfuss_data = {}

    def score_listing(self, listing) -> dict:
        """Berechnet den Gesamtscore eines Listings.

        Returns:
            dict mit 'total', 'location', 'price', 'features',
            'transport', 'education', 'steuerfuss', 'grade' und 'breakdown'.
        """
        location = self._score_location(listing)
        price = self._score_price(listing)
        features = self._score_features(listing)
        transport = self._score_transport(listing)
        education = self._score_education(listing)
        steuerfuss = self._score_steuerfuss(listing)

        total = location + price + features + transport + education + steuerfuss
        grade = self._get_grade(total)

        return {
            'total': round(total, 1),
            'location': round(location, 1),
            'price': round(price, 1),
            'features': round(features, 1),
            'transport': round(transport, 1),
            'education': round(education, 1),
            'steuerfuss': round(steuerfuss, 1),
            'grade': grade,
            'breakdown': {
                'location': round(location, 1),
                'price': round(price, 1),
                'features': round(features, 1),
                'transport': round(transport, 1),
                'education': round(education, 1),
                'steuerfuss': round(steuerfuss, 1),
            },
        }

    def _score_location(self, listing) -> float:
        """Lage-Score (0-20 Punkte)."""
        city = (listing.city or '').lower().strip()

        if not city:
            return 8  # Standard wenn Stadt unbekannt

        if city in PRIORITY_TIER1:
            return 20
        if city in PRIORITY_TIER2:
            return 16
        if city in ZURICH_CITY_VARIANTS:
            return 12
        # Andere Gemeinden im Kanton Zürich
        return 8

    def _score_price(self, listing) -> float:
        """Preis-Score (0-20 Punkte) basierend auf CHF/m²."""
        if not listing.price or not listing.area_sqm or listing.area_sqm == 0:
            return 10  # Neutral wenn keine Daten

        price_per_sqm = listing.price / listing.area_sqm

        if price_per_sqm < 15_000:
            return 20
        elif price_per_sqm < 17_000:
            return 16
        elif price_per_sqm < 18_500:
            return 12
        elif price_per_sqm < 20_000:
            return 8
        else:
            return 4

    def _score_features(self, listing) -> float:
        """Ausstattungs-Score (0-20 Punkte)."""
        score = 0.0

        # Aussenbereich (max 8)
        if listing.outdoor_space_sqm:
            if listing.outdoor_space_sqm >= 20:
                score += 8
            elif listing.outdoor_space_sqm >= 10:
                score += 6

        # Parkplätze (max 4)
        if listing.parking_spots and listing.parking_spots >= 2:
            score += 4

        # Bonus-Features aus Beschreibung (max 8)
        desc = (listing.description or '').lower() + ' ' + (listing.title or '').lower()
        bonus_keywords = {
            'minergie': 2,
            'neubau': 2,
            'erstbezug': 2,
            'seesicht': 2,
            'see sicht': 2,
            'seeblick': 2,
            'garten': 2,
            'eigener garten': 2,
            'dachterrasse': 2,
            'attika': 2,
            'lift': 1,
            'aufzug': 1,
            'einbauküche': 1,
        }

        bonus = 0
        for keyword, points in bonus_keywords.items():
            if keyword in desc:
                bonus += points

        score += min(bonus, 8)  # Max 8 Bonus-Punkte
        return min(score, 20)

    def _score_transport(self, listing) -> float:
        """ÖV-Score (0-20 Punkte)."""
        travel_time = listing.travel_time_to_hb

        if travel_time is None or travel_time < 0:
            return 10  # Neutral wenn unbekannt

        if travel_time <= 20:
            return 20
        elif travel_time <= 25:
            return 16
        elif travel_time <= 30:
            return 12
        elif travel_time <= 35:
            return 8
        else:
            return 0  # Disqualifiziert

    def _score_education(self, listing) -> float:
        """Bildungs-Score (0-25 Punkte) - HÖCHSTE GEWICHTUNG.

        A) Schulnähe: 0-15 Punkte
        B) Maturitätsquote: 0-10 Punkte
        """
        score = 0.0

        # A) Schulnähe (aus listing.nearby_schools oder nearest_school_distance)
        if listing.nearest_school_distance is not None:
            dist = listing.nearest_school_distance
            # Vereinfachte Bewertung basierend auf nächster Schule
            if dist <= 1.0:
                score += 10
            elif dist <= 2.0:
                score += 7
            elif dist <= 3.0:
                score += 4
            elif dist <= 5.0:
                score += 2

        # Detaillierte Schulnähe wenn verfügbar
        if listing.nearby_schools:
            school_score = self._score_school_proximity(listing.nearby_schools)
            score = max(score, school_score)  # Nehme besseren Wert

        # Gymnasium-Bonus
        if listing.gymnasium_nearby:
            score = max(score, 6)

        score = min(score, 15)

        # B) Maturitätsquote
        maturitaetsquote = listing.maturitaetsquote
        if not maturitaetsquote and listing.city:
            maturitaetsquote = self._get_maturitaetsquote(listing.city)
            listing.maturitaetsquote = maturitaetsquote

        if maturitaetsquote:
            if maturitaetsquote >= 32:
                score += 10
            elif maturitaetsquote >= 28:
                score += 8
            elif maturitaetsquote >= 24:
                score += 6
            elif maturitaetsquote >= 21:
                score += 4
            elif maturitaetsquote >= 18:
                score += 2

        return min(score, 25)

    def _score_school_proximity(self, schools: list) -> float:
        """Bewertet Schulnähe detailliert."""
        score = 0.0
        has_primar = False
        has_sekundar = False
        has_gymnasium = False

        for school in schools:
            dist = school.get('distance_km', 999)
            school_type = school.get('type', '').lower()

            if 'primar' in school_type and not has_primar:
                has_primar = True
                if dist <= 1.0:
                    score += 5
                elif dist <= 1.5:
                    score += 3

            elif 'sekundar' in school_type and not has_sekundar:
                has_sekundar = True
                if dist <= 2.0:
                    score += 4
                elif dist <= 3.0:
                    score += 2

            elif ('gymnasium' in school_type or 'kanti' in school_type) and not has_gymnasium:
                has_gymnasium = True
                if dist <= 3.0:
                    score += 6
                elif dist <= 5.0:
                    score += 3

        return min(score, 15)

    def _get_maturitaetsquote(self, city: str) -> Optional[float]:
        """Holt Maturitätsquote für eine Gemeinde."""
        city_lower = city.lower().strip()
        for key, value in self.maturitaetsquoten.items():
            if key.lower() == city_lower:
                return value
        return None

    def _score_steuerfuss(self, listing) -> float:
        """Steuerfuss-Score (0-15 Punkte). Tiefer = besser."""
        steuerfuss = self._get_steuerfuss(listing.city)

        # Steuerfuss am Listing speichern für Anzeige
        listing.steuerfuss = steuerfuss

        if steuerfuss is None:
            return 7  # Neutral wenn unbekannt

        # Tiefere Steuerfüsse = mehr Punkte
        # Zürich Kanton: ca. 69% (Rüschlikon) bis 130% (Fischenthal)
        if steuerfuss <= 80:
            return 15
        elif steuerfuss <= 90:
            return 12
        elif steuerfuss <= 100:
            return 10
        elif steuerfuss <= 110:
            return 7
        elif steuerfuss <= 120:
            return 4
        else:
            return 0

    def _get_steuerfuss(self, city: str) -> Optional[int]:
        """Holt Steuerfuss für eine Gemeinde."""
        if not city:
            return None
        city_clean = city.strip()

        # Direkter Match
        if city_clean in self.steuerfuss_data:
            return self.steuerfuss_data[city_clean]

        # Case-insensitive Match
        city_lower = city_clean.lower()
        for key, value in self.steuerfuss_data.items():
            if key.lower() == city_lower:
                return value

        # Postleitzahl entfernen (z.B. "8001 Zürich" -> "Zürich")
        city_without_plz = re.sub(r'^\d{4}\s*', '', city_clean)
        if city_without_plz != city_clean:
            for key, value in self.steuerfuss_data.items():
                if key.lower() == city_without_plz.lower():
                    return value

        # Umlaut-Varianten normalisieren
        city_normalized = self._normalize_umlauts(city_lower)
        for key, value in self.steuerfuss_data.items():
            if self._normalize_umlauts(key.lower()) == city_normalized:
                return value

        # Teilmatch: Prüfen ob bekannte Gemeinde im String enthalten ist
        for key, value in self.steuerfuss_data.items():
            key_lower = key.lower()
            key_normalized = self._normalize_umlauts(key_lower)
            if key_lower in city_lower or key_normalized in city_normalized:
                return value

        return None

    @staticmethod
    def _normalize_umlauts(text: str) -> str:
        """Normalisiert Umlaute für Vergleiche."""
        replacements = {
            'ä': 'ae', 'ö': 'oe', 'ü': 'ue',
            'ae': 'ä', 'oe': 'ö', 'ue': 'ü',
        }
        result = text
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result

    @staticmethod
    def _get_grade(total_score: float) -> str:
        """Bestimmt das Grade basierend auf dem Gesamtscore (max 120)."""
        if total_score >= 102:  # 85%
            return 'A+'
        elif total_score >= 90:  # 75%
            return 'A'
        elif total_score >= 78:  # 65%
            return 'B'
        elif total_score >= 66:  # 55%
            return 'C'
        else:
            return 'D'
