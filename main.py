"""Immobilien-Suchagent: Hauptscript mit Scheduling."""

import logging
import yaml
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from scrapers import get_scrapers_for_tier, get_all_scrapers
from scrapers.base import close_browser
from services.database import Database
from services.geocoding import GeocodingService
from services.transport import TransportService
from services.education import EducationService
from services.scoring import ListingScorer
from notifiers.email import EmailNotifier
from utils.deduplication import generate_listing_hash
from utils.helpers import setup_logging, format_price, format_score_bar

logger = logging.getLogger(__name__)


def load_config():
    """Lädt Konfiguration aus config.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def enrich_listing(listing, geocoder, transport, education):
    """Reichert ein Listing mit Geo-, Transport- und Bildungsdaten an."""
    # Geocoding falls nötig
    if not listing.latitude or not listing.longitude:
        geo = geocoder.geocode(listing.address, listing.city)
        if geo:
            listing.latitude = geo['latitude']
            listing.longitude = geo['longitude']

    # ÖV-Distanz
    if listing.travel_time_to_hb is None:
        if listing.latitude and listing.longitude:
            listing.travel_time_to_hb = transport.get_travel_time(
                listing.latitude, listing.longitude
            )
        elif listing.city:
            listing.travel_time_to_hb = transport.get_travel_time_from_city(
                listing.city
            )

    # Bildungsdaten
    if listing.latitude and listing.longitude:
        edu_data = education.get_education_data(listing.latitude, listing.longitude)
        listing.nearby_schools = edu_data.get('nearby_schools')
        listing.nearest_school_distance = edu_data.get('nearest_school_distance')
        listing.gymnasium_nearby = edu_data.get('gymnasium_nearby', False)


def run_single_scraper(scraper, config):
    """Führt einen einzelnen Scraper aus und gibt Ergebnisse zurück."""
    platform_name = scraper.get_name()
    try:
        logger.info(f"[{platform_name}] Starte Suche...")
        listings = scraper.search()
        return {'platform': platform_name, 'listings': listings, 'error': None}
    except Exception as e:
        logger.error(f"[{platform_name}] Fehler: {e}")
        return {'platform': platform_name, 'listings': [], 'error': str(e)}


def run_search_cycle(tier='all', parallel=True, progress_callback=None):
    """Ein kompletter Such-Durchlauf für ein oder alle Tiers.

    Args:
        tier: 'all', 'tier1', 'tier2', oder 'tier3'
        parallel: Paralleles Scraping aktivieren
        progress_callback: Optional callback(current, total, platform_name) für Fortschritt

    Returns:
        dict: Platform status mit success/error pro Plattform
    """
    config = load_config()
    db = Database()
    geocoder = GeocodingService(config)
    transport = TransportService(config)
    education = EducationService(config)
    scorer = ListingScorer(config)
    notifier = EmailNotifier(config)

    if tier == 'all':
        scrapers = get_all_scrapers(config)
    else:
        scrapers = get_scrapers_for_tier(config, tier)

    # Initialer Progress-Report
    platform_names = [s.get_name() for s in scrapers]
    if progress_callback:
        progress_callback(0, len(scrapers), None, platform_names)

    all_new_listings = []
    platform_results = {}  # Track results per platform
    scraping_config = config.get('scraping', {})
    max_workers = scraping_config.get('max_parallel_scrapers', 3)
    parallel_enabled = scraping_config.get('parallel_enabled', True)

    # Paralleles Scraping (nur wenn aktiviert in config)
    if parallel and parallel_enabled and len(scrapers) > 1:
        logger.info(f"Starte paralleles Scraping mit {min(len(scrapers), max_workers)} Threads...")
        scraper_results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_single_scraper, scraper, config): scraper
                for scraper in scrapers
            }

            for future in as_completed(futures):
                result = future.result()
                scraper_results.append(result)

        # Ergebnisse sequentiell verarbeiten (DB-Zugriffe nicht parallel)
        for idx, result in enumerate(scraper_results):
            platform_name = result['platform']
            listings = result['listings']
            error = result['error']
            if progress_callback:
                progress_callback(idx, len(scraper_results), platform_name, platform_names)

            if error:
                db.log_search(platform_name, 0, 0, error)
                platform_results[platform_name] = {
                    'success': False,
                    'count': 0,
                    'error': error,
                }
                continue

            new_count = 0
            for listing in listings:
                # Duplikat-Check
                listing.listing_hash = generate_listing_hash(listing)

                if db.listing_exists(listing.listing_hash):
                    logger.info(f"  ⏭ Übersprungen (Duplikat): {listing.title[:50]}...")
                    continue

                if db.listing_exists_by_external_id(listing.external_id, platform_name):
                    logger.info(f"  ⏭ Übersprungen (bereits bekannt): {listing.external_id}")
                    continue

                # Anreicherung mit Geo/Transport/Bildung
                try:
                    enrich_listing(listing, geocoder, transport, education)
                except Exception as e:
                    logger.warning(f"[{platform_name}] Anreicherung fehlgeschlagen: {e}")

                # ÖV-Filter: >35 Min ausschliessen
                max_travel = config.get('search_criteria', {}).get('max_travel_time_minutes', 35)
                if listing.travel_time_to_hb and listing.travel_time_to_hb > max_travel:
                    logger.info(f"  ⏭ Übersprungen (ÖV {listing.travel_time_to_hb} Min > {max_travel} Min): {listing.title[:40]}...")
                    continue

                # Scoring
                score_data = scorer.score_listing(listing)
                listing.total_score = score_data['total']
                listing.location_score = score_data['location']
                listing.price_score = score_data['price']
                listing.features_score = score_data['features']
                listing.transport_score = score_data['transport']
                listing.education_score = score_data['education']
                listing.steuerfuss_score = score_data['steuerfuss']
                listing.grade = score_data['grade']

                # Speichern
                db.save_listing(listing)
                all_new_listings.append(listing)
                new_count += 1

                logger.info(
                    f"  [{listing.grade}] {listing.title} | "
                    f"{format_price(listing.price)} | "
                    f"Score: {listing.total_score}/120"
                )

            db.log_search(platform_name, len(listings), new_count)
            platform_results[platform_name] = {
                'success': True,
                'count': len(listings),
                'new': new_count,
                'error': None,
            }

            # Nur bei erfolgreichem Scraping: Alte Inserate deaktivieren
            # (die nicht mehr auf der Plattform sind)
            if len(listings) > 0:
                active_ids = [l.external_id for l in listings]
                db.deactivate_missing(platform_name, active_ids)

            logger.info(
                f"[{platform_name}] {len(listings)} Inserate gefunden, "
                f"{new_count} neu"
            )
    else:
        # Sequentielles Scraping (Fallback)
        for idx, scraper in enumerate(scrapers):
            platform_name = scraper.get_name()
            if progress_callback:
                progress_callback(idx, len(scrapers), platform_name, platform_names)
            try:
                logger.info(f"[{platform_name}] Starte Suche...")
                listings = scraper.search()
                new_count = 0

                for listing in listings:
                    # Duplikat-Check
                    listing.listing_hash = generate_listing_hash(listing)

                    if db.listing_exists(listing.listing_hash):
                        logger.info(f"  ⏭ Übersprungen (Duplikat): {listing.title[:50]}...")
                        continue

                    if db.listing_exists_by_external_id(listing.external_id, platform_name):
                        logger.info(f"  ⏭ Übersprungen (bereits bekannt): {listing.external_id}")
                        continue

                    # Anreicherung mit Geo/Transport/Bildung
                    try:
                        enrich_listing(listing, geocoder, transport, education)
                    except Exception as e:
                        logger.warning(f"[{platform_name}] Anreicherung fehlgeschlagen: {e}")

                    # ÖV-Filter: >35 Min ausschliessen
                    max_travel = config.get('search_criteria', {}).get('max_travel_time_minutes', 35)
                    if listing.travel_time_to_hb and listing.travel_time_to_hb > max_travel:
                        logger.info(f"  ⏭ Übersprungen (ÖV {listing.travel_time_to_hb} Min > {max_travel} Min): {listing.title[:40]}...")
                        continue

                    # Scoring
                    score_data = scorer.score_listing(listing)
                    listing.total_score = score_data['total']
                    listing.location_score = score_data['location']
                    listing.price_score = score_data['price']
                    listing.features_score = score_data['features']
                    listing.transport_score = score_data['transport']
                    listing.education_score = score_data['education']
                    listing.steuerfuss_score = score_data['steuerfuss']
                    listing.grade = score_data['grade']

                    # Speichern
                    db.save_listing(listing)
                    all_new_listings.append(listing)
                    new_count += 1

                    logger.info(
                        f"  [{listing.grade}] {listing.title} | "
                        f"{format_price(listing.price)} | "
                        f"Score: {listing.total_score}/120"
                    )

                db.log_search(platform_name, len(listings), new_count)
                platform_results[platform_name] = {
                    'success': True,
                    'count': len(listings),
                    'new': new_count,
                    'error': None,
                }

                # Nur bei erfolgreichem Scraping: Alte Inserate deaktivieren
                if len(listings) > 0:
                    active_ids = [l.external_id for l in listings]
                    db.deactivate_missing(platform_name, active_ids)

                logger.info(
                    f"[{platform_name}] {len(listings)} Inserate gefunden, "
                    f"{new_count} neu"
                )

            except Exception as e:
                # Bei Fehler: Nichts deaktivieren, alte Inserate behalten
                logger.error(f"[{platform_name}] Fehler: {e}")
                db.log_search(platform_name, 0, 0, str(e))
                platform_results[platform_name] = {
                    'success': False,
                    'count': 0,
                    'error': str(e),
                }

    # Benachrichtigungen
    all_new_listings.sort(key=lambda x: x.total_score or 0, reverse=True)
    min_score = config.get('notifications', {}).get('min_score_to_notify', 60)
    high_quality = [l for l in all_new_listings if (l.total_score or 0) >= min_score]

    if high_quality:
        logger.info(f"\n🎉 {len(high_quality)} neue hochwertige Inserate gefunden!")
        notifier.send_new_listings(high_quality)
        db.mark_as_notified([l.external_id for l in high_quality])
    else:
        logger.info("Keine neuen hochwertigen Inserate gefunden.")

    # Statistik
    stats = db.get_stats()
    logger.info(
        f"Statistik: {stats['total_active']} aktive Inserate, "
        f"Ø Score: {stats['avg_score']}"
    )

    return platform_results


def main():
    """Startet den Immobilien-Suchagent."""
    load_dotenv()
    setup_logging()

    logger.info("🏠 Immobilien-Suchagent gestartet")

    config = load_config()
    scheduling = config.get('scheduling', {})
    tier1_interval = scheduling.get('tier1_interval_minutes', 15)
    tier2_interval = scheduling.get('tier2_interval_minutes', 60)
    tier3_interval = scheduling.get('tier3_interval_minutes', 360)

    # Initiale Suche über alle Tiers
    logger.info("Starte initiale Suche über alle Plattformen...")
    run_search_cycle('all')

    # Scheduler einrichten
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_search_cycle,
        trigger=IntervalTrigger(minutes=tier1_interval),
        args=['tier1'],
        id='tier1_search',
        name=f'Tier 1 Suche (alle {tier1_interval} Min)',
    )

    scheduler.add_job(
        run_search_cycle,
        trigger=IntervalTrigger(minutes=tier2_interval),
        args=['tier2'],
        id='tier2_search',
        name=f'Tier 2 Suche (alle {tier2_interval} Min)',
    )

    scheduler.add_job(
        run_search_cycle,
        trigger=IntervalTrigger(minutes=tier3_interval),
        args=['tier3'],
        id='tier3_search',
        name=f'Tier 3 Suche (alle {tier3_interval} Min)',
    )

    logger.info(
        f"✅ Scheduler gestartet:\n"
        f"  Tier 1: alle {tier1_interval} Min\n"
        f"  Tier 2: alle {tier2_interval} Min\n"
        f"  Tier 3: alle {tier3_interval} Min"
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Agent gestoppt.")
    finally:
        close_browser()


if __name__ == '__main__':
    main()
