#!/usr/bin/env python3
"""Berechnet Scores für alle bestehenden Listings neu."""

import sqlite3
import yaml
import os

# Projektverzeichnis
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'listings.db')

def load_config():
    with open(os.path.join(BASE_DIR, 'config.yaml'), 'r') as f:
        return yaml.safe_load(f)

def main():
    from services.scoring import ListingScorer
    from scrapers.base import Listing

    config = load_config()
    scorer = ListingScorer(config)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Alle aktiven Listings holen
    rows = conn.execute("SELECT * FROM listings WHERE is_active = 1").fetchall()
    print(f"Berechne Scores für {len(rows)} Listings neu...")

    updated = 0
    for row in rows:
        # Listing-Objekt erstellen
        listing = Listing(
            external_id=row['external_id'],
            platform=row['platform'],
            title=row['title'],
            description=row['description'],
            price=row['price'],
            rooms=row['rooms'],
            area_sqm=row['area_sqm'],
            address=row['address'],
            city=row['city'],
            latitude=row['latitude'],
            longitude=row['longitude'],
            parking_spots=row['parking_spots'],
            outdoor_space_sqm=row['outdoor_space_sqm'],
            travel_time_to_hb=row['travel_time_to_hb'],
            maturitaetsquote=row['maturitaetsquote'],
            nearest_school_distance=row['nearest_school_distance'],
            gymnasium_nearby=row['gymnasium_nearby'],
        )

        # Neu berechnen
        scores = scorer.score_listing(listing)

        # Update
        conn.execute("""
            UPDATE listings SET
                total_score = ?,
                location_score = ?,
                price_score = ?,
                features_score = ?,
                transport_score = ?,
                education_score = ?,
                steuerfuss_score = ?,
                steuerfuss = ?,
                grade = ?
            WHERE id = ?
        """, (
            scores['total'],
            scores['location'],
            scores['price'],
            scores['features'],
            scores['transport'],
            scores['education'],
            scores['steuerfuss'],
            listing.steuerfuss,
            scores['grade'],
            row['id']
        ))
        updated += 1

        print(f"  [{scores['grade']}] {row['city'] or 'Unbekannt'}: {scores['total']}/120 (Steuer: {listing.steuerfuss}% -> {scores['steuerfuss']}/15)")

    conn.commit()
    conn.close()

    print(f"\n{updated} Listings aktualisiert!")

if __name__ == '__main__':
    main()
