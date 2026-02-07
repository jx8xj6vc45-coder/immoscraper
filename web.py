"""Lokale Web-Oberfläche für Immobilien-Listings."""

import sqlite3
import os
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'listings.db')
ITEMS_PER_PAGE = 20
MAX_LISTINGS = 200  # Maximum number of listings to keep in view

# Status für Scraper-Trigger
scraper_status = {'running': False, 'last_run': None, 'message': '', 'completed': False}

# Status pro Plattform: {'platform': {'success': bool, 'count': int, 'error': str, 'timestamp': str}}
platform_status = {}

# Scraper-Fortschritt: {'total': int, 'current': int, 'current_platform': str, 'platforms': []}
scraper_progress = {'total': 0, 'current': 0, 'current_platform': '', 'platforms': []}

# Migration flag
_db_migrated = False


def migrate_db():
    """Führt Datenbank-Migrationen aus."""
    global _db_migrated
    if _db_migrated:
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute("PRAGMA table_info(listings)")
        columns = [row[1] for row in cursor.fetchall()]

        # Migration: is_favorite
        if 'is_favorite' not in columns:
            conn.execute("ALTER TABLE listings ADD COLUMN is_favorite BOOLEAN DEFAULT 0")
            print("Migration: is_favorite Spalte hinzugefügt")

        # Migration: duplicate_group_id
        if 'duplicate_group_id' not in columns:
            conn.execute("ALTER TABLE listings ADD COLUMN duplicate_group_id TEXT")
            print("Migration: duplicate_group_id Spalte hinzugefügt")

        # Migration: price_history Tabelle
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            )
        """)

        conn.commit()
        _db_migrated = True
    except Exception as e:
        print(f"Migration Fehler: {e}")
    finally:
        conn.close()


def get_db():
    migrate_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def format_price(price):
    if not price:
        return 'Preis auf Anfrage'
    s = str(int(price))
    groups = []
    while s:
        groups.append(s[-3:])
        s = s[:-3]
    return "CHF " + "'".join(reversed(groups))


@app.route('/')
def index():
    db = get_db()

    # Filter aus Query-Parametern
    sort = request.args.get('sort', 'newest')
    grade_filter = request.args.get('grade', '')
    city_filter = request.args.get('city', '')
    platform_filter = request.args.get('platform', '')
    min_price = request.args.get('min_price', '', type=str)
    max_price = request.args.get('max_price', '', type=str)
    min_rooms = request.args.get('min_rooms', '', type=str)
    only_new = request.args.get('only_new', '')
    newest_20 = request.args.get('newest_20', '')
    only_favorites = request.args.get('only_favorites', '')
    only_duplicates = request.args.get('only_duplicates', '')
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1

    # Show all listings (active and inactive) - no is_active filter
    query = "SELECT * FROM listings WHERE 1=1"
    params = []

    # "Neu" = letzte 24 Stunden
    new_threshold = datetime.now() - timedelta(hours=24)
    new_threshold_str = new_threshold.strftime('%Y-%m-%d %H:%M:%S')

    if only_new:
        query += " AND first_seen >= ?"
        params.append(new_threshold_str)

    # "Newest 20" filter - only show most recent 20 entries
    limit_newest_20 = bool(newest_20)

    # Favorites filter
    if only_favorites:
        query += " AND is_favorite = 1"

    # Duplicates filter - show only listings with duplicates
    if only_duplicates:
        query += " AND listing_hash IN (SELECT listing_hash FROM listings WHERE listing_hash IS NOT NULL GROUP BY listing_hash HAVING COUNT(*) > 1)"

    if grade_filter:
        query += " AND grade = ?"
        params.append(grade_filter)
    if city_filter:
        query += " AND city LIKE ?"
        params.append(f"%{city_filter}%")
    if platform_filter:
        query += " AND platform = ?"
        params.append(platform_filter)
    if min_price:
        query += " AND price >= ?"
        params.append(int(min_price))
    if max_price:
        query += " AND price <= ?"
        params.append(int(max_price))
    if min_rooms:
        query += " AND rooms >= ?"
        params.append(float(min_rooms))

    sort_map = {
        'newest': 'first_seen DESC',
        'score': 'total_score DESC',
        'price_asc': 'price ASC',
        'price_desc': 'price DESC',
        'rooms': 'rooms DESC',
    }
    query += f" ORDER BY {sort_map.get(sort, 'first_seen DESC')}"

    # Apply limits: newest_20 filter or MAX_LISTINGS
    if limit_newest_20:
        query += f" LIMIT 20"
    else:
        query += f" LIMIT {MAX_LISTINGS}"

    listings = [dict(r) for r in db.execute(query, params).fetchall()]

    # Duplikat-Zählung für jedes Listing
    duplicate_counts = {}
    dup_rows = db.execute("""
        SELECT listing_hash, COUNT(*) as cnt FROM listings
        WHERE listing_hash IS NOT NULL
        GROUP BY listing_hash HAVING COUNT(*) > 1
    """).fetchall()
    for row in dup_rows:
        duplicate_counts[row['listing_hash']] = row['cnt']

    # Favoriten-Zählung
    fav_count = db.execute("SELECT COUNT(*) FROM listings WHERE is_favorite = 1").fetchone()[0]

    # Preise formatieren und "Neu"-Flag setzen
    for l in listings:
        # Duplikat-Info hinzufügen
        l['duplicate_count'] = duplicate_counts.get(l.get('listing_hash'), 0)
        l['price_fmt'] = format_price(l.get('price'))
        # Prüfen ob Listing "neu" ist (letzte 24h)
        first_seen = l.get('first_seen')
        if first_seen:
            try:
                seen_dt = datetime.strptime(first_seen, '%Y-%m-%d %H:%M:%S')
                l['is_new'] = seen_dt >= new_threshold
            except (ValueError, TypeError):
                l['is_new'] = False
        else:
            l['is_new'] = False

    # Statistiken (vor Pagination berechnen)
    new_count = sum(1 for l in listings if l.get('is_new'))
    dup_count = sum(1 for l in listings if l.get('duplicate_count', 0) > 1)
    stats = {
        'total': len(listings),
        'new_count': new_count,
        'fav_count': fav_count,
        'dup_count': dup_count,
        'avg_score': 0,
        'platforms': {},
        'grades': {},
        'cities': {},
    }
    if listings:
        scores = [l['total_score'] for l in listings if l.get('total_score')]
        stats['avg_score'] = round(sum(scores) / len(scores), 1) if scores else 0

    for l in listings:
        p = l.get('platform', 'unknown')
        stats['platforms'][p] = stats['platforms'].get(p, 0) + 1
        g = l.get('grade', '-')
        stats['grades'][g] = stats['grades'].get(g, 0) + 1
        c = l.get('city', 'Unbekannt')
        if c:
            stats['cities'][c] = stats['cities'].get(c, 0) + 1

    # Sortierte Städte für Filter
    cities_sorted = sorted(stats['cities'].items(), key=lambda x: -x[1])

    # Pagination
    total_items = len(listings)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if page > total_pages and total_pages > 0:
        page = total_pages
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    listings_page = listings[start_idx:end_idx]

    db.close()

    return render_template(
        'index.html',
        listings=listings_page,
        stats=stats,
        cities=cities_sorted,
        sort=sort,
        grade_filter=grade_filter,
        city_filter=city_filter,
        platform_filter=platform_filter,
        min_price=min_price,
        max_price=max_price,
        min_rooms=min_rooms,
        only_new=only_new,
        newest_20=newest_20,
        only_favorites=only_favorites,
        only_duplicates=only_duplicates,
        scraper_status=scraper_status,
        platform_status=platform_status,
        page=page,
        total_pages=total_pages,
        total_items=total_items,
    )


def update_progress(current, total, current_platform, all_platforms):
    """Callback für Scraper-Fortschritt."""
    global scraper_progress
    scraper_progress['current'] = current
    scraper_progress['total'] = total
    scraper_progress['current_platform'] = current_platform or ''
    scraper_progress['platforms'] = all_platforms or []


def run_scraper_background():
    """Führt den Scraper im Hintergrund aus."""
    global scraper_status, platform_status, scraper_progress
    try:
        scraper_status['running'] = True
        scraper_status['completed'] = False
        scraper_status['message'] = 'Scraper läuft...'
        scraper_progress = {'total': 0, 'current': 0, 'current_platform': '', 'platforms': []}

        # Importiere und starte den Scraper mit Progress-Callback
        from main import run_search_cycle
        results = run_search_cycle('all', progress_callback=update_progress)

        # Platform-Status aktualisieren
        if results:
            timestamp = datetime.now().strftime('%H:%M:%S')
            for platform, data in results.items():
                platform_status[platform] = {
                    **data,
                    'timestamp': timestamp,
                }

        scraper_status['message'] = 'Scraper abgeschlossen!'
        scraper_status['last_run'] = datetime.now().strftime('%H:%M:%S')
        scraper_status['completed'] = True
    except Exception as e:
        scraper_status['message'] = f'Fehler: {str(e)}'
    finally:
        scraper_status['running'] = False


@app.route('/trigger-scraper', methods=['POST'])
def trigger_scraper():
    """Startet den Scraper manuell."""
    global scraper_status

    if scraper_status['running']:
        return jsonify({'success': False, 'message': 'Scraper läuft bereits'})

    # Starte Scraper in eigenem Thread
    thread = threading.Thread(target=run_scraper_background, daemon=True)
    thread.start()

    return jsonify({'success': True, 'message': 'Scraper gestartet'})


@app.route('/scraper-status')
def get_scraper_status():
    """Gibt den aktuellen Scraper-Status zurück."""
    return jsonify(scraper_status)


@app.route('/scraper-progress')
def get_scraper_progress():
    """Gibt den aktuellen Scraper-Fortschritt zurück."""
    return jsonify(scraper_progress)


@app.route('/platform-status')
def get_platform_status():
    """Gibt Status aller Plattformen zurück."""
    return jsonify(platform_status)


@app.route('/toggle-favorite/<int:listing_id>', methods=['POST'])
def toggle_favorite(listing_id):
    """Toggle Favoriten-Status eines Listings."""
    db = get_db()
    try:
        current = db.execute(
            "SELECT is_favorite FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if current:
            new_status = 0 if current['is_favorite'] else 1
            db.execute(
                "UPDATE listings SET is_favorite = ? WHERE id = ?",
                (new_status, listing_id)
            )
            db.commit()
            return jsonify({'success': True, 'is_favorite': new_status == 1})
        return jsonify({'success': False, 'message': 'Listing nicht gefunden'})
    finally:
        db.close()


@app.route('/price-history/<int:listing_id>')
def price_history(listing_id):
    """Gibt Preishistorie eines Listings zurück."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT price, recorded_at FROM price_history
            WHERE listing_id = ? ORDER BY recorded_at ASC
        """, (listing_id,)).fetchall()
        history = [{'price': format_price(r['price']), 'price_raw': r['price'],
                   'date': r['recorded_at']} for r in rows]
        return jsonify({'success': True, 'history': history})
    finally:
        db.close()


@app.route('/duplicates/<int:listing_id>')
def get_duplicates(listing_id):
    """Gibt Duplikate eines Listings zurück."""
    db = get_db()
    try:
        listing = db.execute(
            "SELECT listing_hash FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if not listing or not listing['listing_hash']:
            return jsonify({'success': False, 'message': 'Kein Hash'})

        rows = db.execute("""
            SELECT id, platform, url, price, first_seen FROM listings
            WHERE listing_hash = ? AND id != ?
            ORDER BY first_seen ASC
        """, (listing['listing_hash'], listing_id)).fetchall()

        duplicates = [{
            'id': r['id'],
            'platform': r['platform'],
            'url': r['url'],
            'price': format_price(r['price']),
            'first_seen': r['first_seen']
        } for r in rows]
        return jsonify({'success': True, 'duplicates': duplicates})
    finally:
        db.close()


if __name__ == '__main__':
    print("\n  Öffne im Browser: http://localhost:5001\n")
    app.run(debug=True, port=5001)
