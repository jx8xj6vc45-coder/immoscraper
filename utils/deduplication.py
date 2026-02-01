"""Duplikat-Erkennung über Plattformen hinweg."""

import hashlib
import re


def generate_listing_hash(listing) -> str:
    """Generiert einen Hash für plattformübergreifende Duplikaterkennung.

    Der Hash basiert auf normalisierten Schlüsselmerkmalen:
    - Preis (gerundet auf 10'000)
    - Zimmerzahl
    - Fläche (gerundet auf 5 m²)
    - Stadt (normalisiert)
    - Adresse (normalisiert, ohne Hausnummer-Varianten)

    So wird dasselbe Objekt auf verschiedenen Plattformen erkannt.
    """
    components = []

    # Preis (auf 10'000 gerundet)
    if listing.price:
        components.append(str(round(listing.price / 10_000) * 10_000))
    else:
        components.append('no_price')

    # Zimmer
    if listing.rooms:
        components.append(f"{listing.rooms:.1f}")
    else:
        components.append('no_rooms')

    # Fläche (auf 5 m² gerundet)
    if listing.area_sqm:
        components.append(str(round(listing.area_sqm / 5) * 5))
    else:
        components.append('no_area')

    # Stadt (normalisiert)
    city = _normalize_text(listing.city or '')
    components.append(city if city else 'no_city')

    # Adresse (normalisiert)
    address = _normalize_address(listing.address or '')
    components.append(address if address else 'no_addr')

    raw = '|'.join(components)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _normalize_text(text: str) -> str:
    """Normalisiert Text: Kleinbuchstaben, Umlaute, Whitespace."""
    text = text.lower().strip()
    text = text.replace('ü', 'ue').replace('ö', 'oe').replace('ä', 'ae')
    text = re.sub(r'\s+', ' ', text)
    return text


def _normalize_address(address: str) -> str:
    """Normalisiert Adresse für Vergleiche."""
    text = _normalize_text(address)
    # PLZ entfernen
    text = re.sub(r'\b\d{4}\b', '', text)
    # Strasse/Str./Str → einheitlich
    text = re.sub(r'\bstrasse\b', 'str', text)
    text = re.sub(r'\bstr\.\b', 'str', text)
    # Mehrfach-Spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text
