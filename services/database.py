"""SQLite-Datenbank für Immobilien-Listings."""

import sqlite3
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'listings.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL,
    listing_hash TEXT,
    platform TEXT NOT NULL,

    -- Basis-Informationen
    title TEXT,
    description TEXT,
    price INTEGER,
    rooms REAL,
    area_sqm INTEGER,
    address TEXT,
    city TEXT,

    -- Geo-Daten
    latitude REAL,
    longitude REAL,

    -- Kriterien
    parking_spots INTEGER,
    outdoor_space_sqm INTEGER,
    outdoor_type TEXT,
    property_type TEXT,
    ownership_type TEXT,

    -- URLs
    url TEXT,
    image_url TEXT,

    -- Scores (Total: 120 Punkte)
    total_score REAL,
    location_score REAL,
    price_score REAL,
    features_score REAL,
    transport_score REAL,
    education_score REAL,
    steuerfuss_score REAL,
    steuerfuss INTEGER,
    grade TEXT,

    -- Bildungs-Details
    maturitaetsquote REAL,
    nearest_school_distance REAL,
    gymnasium_nearby BOOLEAN,
    nearby_schools_json TEXT,

    -- Meta
    travel_time_to_hb INTEGER,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
    notified BOOLEAN DEFAULT 0,

    -- Favoriten & Duplikate
    is_favorite BOOLEAN DEFAULT 0,
    duplicate_group_id TEXT,

    UNIQUE(external_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_listing_hash ON listings(listing_hash);
CREATE INDEX IF NOT EXISTS idx_external_id_platform ON listings(external_id, platform);
CREATE INDEX IF NOT EXISTS idx_total_score ON listings(total_score DESC);
CREATE INDEX IF NOT EXISTS idx_notified ON listings(notified);
CREATE INDEX IF NOT EXISTS idx_first_seen ON listings(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_favorite ON listings(is_favorite);
CREATE INDEX IF NOT EXISTS idx_duplicate_group ON listings(duplicate_group_id);

-- Preis-Historie
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);

CREATE TABLE IF NOT EXISTS search_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    results_found INTEGER,
    new_results INTEGER,
    errors TEXT
);
"""


class Database:
    """SQLite-Datenbank-Manager für Immobilien-Listings."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(SCHEMA)
            # Migration: Add steuerfuss columns if missing
            self._migrate_add_steuerfuss(conn)
            # Migration: Add favorites and duplicates columns
            self._migrate_add_favorites_duplicates(conn)

    def _migrate_add_steuerfuss(self, conn):
        """Fügt steuerfuss Spalten hinzu falls nicht vorhanden."""
        try:
            cursor = conn.execute("PRAGMA table_info(listings)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'steuerfuss_score' not in columns:
                conn.execute("ALTER TABLE listings ADD COLUMN steuerfuss_score REAL")
            if 'steuerfuss' not in columns:
                conn.execute("ALTER TABLE listings ADD COLUMN steuerfuss INTEGER")
        except Exception as e:
            logger.warning(f"Steuerfuss Migration Fehler: {e}")

    def _migrate_add_favorites_duplicates(self, conn):
        """Fügt Favoriten und Duplikate Spalten hinzu falls nicht vorhanden."""
        try:
            cursor = conn.execute("PRAGMA table_info(listings)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'is_favorite' not in columns:
                conn.execute("ALTER TABLE listings ADD COLUMN is_favorite BOOLEAN DEFAULT 0")
            if 'duplicate_group_id' not in columns:
                conn.execute("ALTER TABLE listings ADD COLUMN duplicate_group_id TEXT")
        except Exception as e:
            logger.warning(f"Favorites/Duplicates Migration Fehler: {e}")

    def listing_exists(self, listing_hash):
        """Prüft ob ein Listing (per Hash) bereits existiert."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM listings WHERE listing_hash = ?",
                (listing_hash,)
            ).fetchone()
            return row is not None

    def listing_exists_by_external_id(self, external_id, platform):
        """Prüft ob ein Listing per external_id + platform existiert."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM listings WHERE external_id = ? AND platform = ?",
                (external_id, platform)
            ).fetchone()
            return row is not None

    def save_listing(self, listing):
        """Speichert ein neues Listing in der Datenbank."""
        with self._get_conn() as conn:
            # Duplikat-Gruppe finden wenn gleicher Hash existiert
            duplicate_group = None
            if listing.listing_hash:
                existing = conn.execute(
                    "SELECT duplicate_group_id FROM listings WHERE listing_hash = ? LIMIT 1",
                    (listing.listing_hash,)
                ).fetchone()
                if existing:
                    duplicate_group = existing['duplicate_group_id']
                    if not duplicate_group:
                        import uuid
                        duplicate_group = str(uuid.uuid4())[:8]
                        # Update existing listing with group ID
                        conn.execute(
                            "UPDATE listings SET duplicate_group_id = ? WHERE listing_hash = ?",
                            (duplicate_group, listing.listing_hash)
                        )

            cursor = conn.execute("""
                INSERT OR IGNORE INTO listings (
                    external_id, listing_hash, platform,
                    title, description, price, rooms, area_sqm, address, city,
                    latitude, longitude,
                    parking_spots, outdoor_space_sqm, outdoor_type,
                    property_type, ownership_type,
                    url, image_url,
                    total_score, location_score, price_score, features_score,
                    transport_score, education_score, steuerfuss_score, steuerfuss, grade,
                    maturitaetsquote, nearest_school_distance,
                    gymnasium_nearby, nearby_schools_json,
                    travel_time_to_hb, duplicate_group_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                listing.external_id, listing.listing_hash, listing.platform,
                listing.title, listing.description, listing.price,
                listing.rooms, listing.area_sqm, listing.address, listing.city,
                listing.latitude, listing.longitude,
                listing.parking_spots, listing.outdoor_space_sqm,
                listing.outdoor_type, listing.property_type,
                listing.ownership_type,
                listing.url, listing.image_url,
                listing.total_score, listing.location_score,
                listing.price_score, listing.features_score,
                listing.transport_score, listing.education_score,
                listing.steuerfuss_score, listing.steuerfuss,
                listing.grade,
                listing.maturitaetsquote, listing.nearest_school_distance,
                listing.gymnasium_nearby,
                json.dumps(listing.nearby_schools) if listing.nearby_schools else None,
                listing.travel_time_to_hb, duplicate_group,
            ))

            # Preis in Historie speichern wenn neues Listing
            if cursor.rowcount > 0 and listing.price:
                listing_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO price_history (listing_id, price) VALUES (?, ?)",
                    (listing_id, listing.price)
                )

    def update_scores(self, listing_id, scores):
        """Aktualisiert Scores eines Listings."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE listings SET
                    total_score = ?, location_score = ?, price_score = ?,
                    features_score = ?, transport_score = ?, education_score = ?,
                    grade = ?
                WHERE id = ?
            """, (
                scores['total'], scores['location'], scores['price'],
                scores['features'], scores['transport'], scores['education'],
                scores['grade'], listing_id,
            ))

    def get_unnotified_listings(self, min_score=60):
        """Gibt alle noch nicht gemeldeten Listings über min_score zurück."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM listings
                WHERE notified = 0 AND total_score >= ? AND is_active = 1
                ORDER BY total_score DESC
            """, (min_score,)).fetchall()
            return [dict(r) for r in rows]

    def mark_as_notified(self, external_ids):
        """Markiert Listings als benachrichtigt."""
        if not external_ids:
            return
        with self._get_conn() as conn:
            placeholders = ','.join('?' for _ in external_ids)
            conn.execute(
                f"UPDATE listings SET notified = 1 WHERE external_id IN ({placeholders})",
                external_ids,
            )

    def deactivate_missing(self, platform, active_external_ids):
        """Deaktiviert Listings die nicht mehr auf der Plattform sind."""
        if not active_external_ids:
            return
        with self._get_conn() as conn:
            placeholders = ','.join('?' for _ in active_external_ids)
            conn.execute(
                f"""UPDATE listings SET is_active = 0
                    WHERE platform = ? AND external_id NOT IN ({placeholders})
                    AND is_active = 1""",
                [platform] + list(active_external_ids),
            )

    def log_search(self, platform, results_found, new_results, errors=None):
        """Loggt einen Suchlauf."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO search_logs (platform, results_found, new_results, errors)
                VALUES (?, ?, ?, ?)
            """, (platform, results_found, new_results, errors))

    def get_stats(self):
        """Gibt Statistiken zurück."""
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE is_active = 1"
            ).fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(total_score) FROM listings WHERE is_active = 1 AND total_score IS NOT NULL"
            ).fetchone()[0]
            by_grade = conn.execute("""
                SELECT grade, COUNT(*) as cnt FROM listings
                WHERE is_active = 1 AND grade IS NOT NULL
                GROUP BY grade ORDER BY grade
            """).fetchall()
            return {
                'total_active': total,
                'avg_score': round(avg_score, 1) if avg_score else 0,
                'by_grade': {r['grade']: r['cnt'] for r in by_grade},
            }

    # === Favoriten ===

    def toggle_favorite(self, listing_id):
        """Toggle Favoriten-Status eines Listings."""
        with self._get_conn() as conn:
            current = conn.execute(
                "SELECT is_favorite FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            if current:
                new_status = 0 if current['is_favorite'] else 1
                conn.execute(
                    "UPDATE listings SET is_favorite = ? WHERE id = ?",
                    (new_status, listing_id)
                )
                return new_status == 1
        return False

    def get_favorites(self):
        """Gibt alle Favoriten zurück."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE is_favorite = 1 ORDER BY first_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # === Preis-Historie ===

    def record_price(self, listing_id, price):
        """Speichert Preis in Historie wenn geändert."""
        if not price:
            return
        with self._get_conn() as conn:
            # Letzten Preis prüfen
            last = conn.execute("""
                SELECT price FROM price_history
                WHERE listing_id = ? ORDER BY recorded_at DESC LIMIT 1
            """, (listing_id,)).fetchone()

            # Nur speichern wenn Preis anders oder erster Eintrag
            if not last or last['price'] != price:
                conn.execute(
                    "INSERT INTO price_history (listing_id, price) VALUES (?, ?)",
                    (listing_id, price)
                )

    def get_price_history(self, listing_id):
        """Gibt Preishistorie eines Listings zurück."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT price, recorded_at FROM price_history
                WHERE listing_id = ? ORDER BY recorded_at ASC
            """, (listing_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_listings_with_price_changes(self):
        """Gibt Listings mit Preisänderungen zurück."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT l.*,
                    (SELECT MIN(price) FROM price_history WHERE listing_id = l.id) as min_price,
                    (SELECT MAX(price) FROM price_history WHERE listing_id = l.id) as max_price,
                    (SELECT COUNT(*) FROM price_history WHERE listing_id = l.id) as price_change_count
                FROM listings l
                WHERE l.id IN (
                    SELECT listing_id FROM price_history
                    GROUP BY listing_id HAVING COUNT(*) > 1
                )
                ORDER BY l.first_seen DESC
            """).fetchall()
            return [dict(r) for r in rows]

    # === Duplikate ===

    def find_duplicates_by_hash(self, listing_hash):
        """Findet alle Listings mit gleichem Hash (Duplikate)."""
        if not listing_hash:
            return []
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE listing_hash = ? ORDER BY first_seen ASC",
                (listing_hash,)
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_duplicate_group(self, listing_ids, group_id=None):
        """Markiert Listings als Duplikat-Gruppe."""
        if not listing_ids:
            return
        import uuid
        group_id = group_id or str(uuid.uuid4())[:8]
        with self._get_conn() as conn:
            placeholders = ','.join('?' for _ in listing_ids)
            conn.execute(
                f"UPDATE listings SET duplicate_group_id = ? WHERE id IN ({placeholders})",
                [group_id] + list(listing_ids)
            )
        return group_id

    def get_duplicates(self):
        """Gibt alle Duplikat-Gruppen zurück."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT l.*,
                    (SELECT COUNT(*) FROM listings WHERE listing_hash = l.listing_hash) as duplicate_count
                FROM listings l
                WHERE l.listing_hash IN (
                    SELECT listing_hash FROM listings
                    WHERE listing_hash IS NOT NULL
                    GROUP BY listing_hash HAVING COUNT(*) > 1
                )
                ORDER BY l.listing_hash, l.first_seen ASC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_listing_by_id(self, listing_id):
        """Gibt ein einzelnes Listing zurück."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_listing_price(self, listing_id, new_price):
        """Aktualisiert Preis und speichert in Historie."""
        with self._get_conn() as conn:
            # Aktuellen Preis holen
            current = conn.execute(
                "SELECT price FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()

            if current and current['price'] != new_price:
                # Preis in Historie speichern
                self.record_price(listing_id, new_price)
                # Listing aktualisieren
                conn.execute(
                    "UPDATE listings SET price = ?, last_checked = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_price, listing_id)
                )
