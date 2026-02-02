"""Lokale Web-Oberfläche für Immobilien-Listings."""

import sqlite3
import os
from flask import Flask, render_template, request

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'listings.db')


def get_db():
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

    query = "SELECT * FROM listings WHERE is_active = 1"
    params = []

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

    listings = [dict(r) for r in db.execute(query, params).fetchall()]

    # Preise formatieren
    for l in listings:
        l['price_fmt'] = format_price(l.get('price'))

    # Statistiken
    stats = {
        'total': len(listings),
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

    db.close()

    return render_template(
        'index.html',
        listings=listings,
        stats=stats,
        cities=cities_sorted,
        sort=sort,
        grade_filter=grade_filter,
        city_filter=city_filter,
        platform_filter=platform_filter,
        min_price=min_price,
        max_price=max_price,
        min_rooms=min_rooms,
    )


if __name__ == '__main__':
    print("\n  Öffne im Browser: http://localhost:5001\n")
    app.run(debug=True, port=5001)
