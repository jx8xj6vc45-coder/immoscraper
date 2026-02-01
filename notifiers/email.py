"""E-Mail-Benachrichtigungen für neue Immobilien-Listings."""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from utils.helpers import format_price

logger = logging.getLogger(__name__)

GRADE_EMOJIS = {
    'A+': '🌟 TOP MATCH',
    'A': '⭐ Sehr gut',
    'B': '✓ Gut',
    'C': '~ OK',
    'D': '- Weniger relevant',
}


class EmailNotifier:
    """Versendet E-Mail-Benachrichtigungen über neue Listings."""

    def __init__(self, config):
        self.config = config
        email_cfg = config.get('notifications', {}).get('email', {})
        self.enabled = email_cfg.get('enabled', True)
        self.smtp_host = email_cfg.get('smtp_host', 'smtp.gmail.com')
        self.smtp_port = email_cfg.get('smtp_port', 587)
        self.from_email = os.getenv('EMAIL_FROM', '')
        self.to_email = os.getenv('EMAIL_TO', '')
        self.password = os.getenv('EMAIL_PASSWORD', '')

    def send_new_listings(self, listings: list):
        """Versendet E-Mail mit neuen Listings, sortiert nach Score."""
        if not self.enabled or not listings:
            return

        if not all([self.from_email, self.to_email, self.password]):
            logger.warning("E-Mail nicht konfiguriert (EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD fehlen)")
            return

        # Nach Score sortieren
        listings.sort(key=lambda l: l.total_score or 0, reverse=True)

        # Grade-Gruppen
        top_matches = [l for l in listings if l.grade in ('A+', 'A')]
        good_matches = [l for l in listings if l.grade == 'B']
        ok_matches = [l for l in listings if l.grade in ('C', 'D')]

        subject = self._build_subject(listings, top_matches)
        html_body = self._build_html(listings, top_matches, good_matches, ok_matches)

        self._send_email(subject, html_body)

    def _build_subject(self, all_listings, top_matches) -> str:
        """Erstellt den E-Mail-Betreff."""
        count = len(all_listings)
        if top_matches:
            grades = set(l.grade for l in top_matches)
            grade_str = '/'.join(sorted(grades))
            return f"🏠 {count} neue Immobilie(n) gefunden! ({grade_str})"
        return f"🏠 {count} neue Immobilie(n) gefunden"

    def _build_html(self, all_listings, top_matches, good_matches, ok_matches) -> str:
        """Erstellt den HTML-Body der E-Mail."""
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
                .listing {{ border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 12px 0; }}
                .listing-top {{ border-left: 4px solid #FFD700; background: #FFFEF0; }}
                .listing-good {{ border-left: 4px solid #4CAF50; background: #F0FFF0; }}
                .listing-ok {{ border-left: 4px solid #999; }}
                .score {{ font-size: 24px; font-weight: bold; float: right; }}
                .grade {{ font-size: 14px; color: #666; }}
                .details {{ margin-top: 8px; color: #555; }}
                .score-breakdown {{ font-size: 12px; color: #888; margin-top: 4px; }}
                h2 {{ color: #2c3e50; }}
                h3 {{ color: #34495e; margin-top: 24px; }}
                a {{ color: #3498db; }}
            </style>
        </head>
        <body>
            <h2>🏠 {len(all_listings)} neue Immobilie(n) gefunden!</h2>
        """

        if top_matches:
            html += '<h3>🌟 TOP MATCHES</h3>'
            for listing in top_matches:
                html += self._listing_to_html(listing, 'listing-top')

        if good_matches:
            html += '<h3>⭐ Gute Matches</h3>'
            for listing in good_matches:
                html += self._listing_to_html(listing, 'listing-good')

        if ok_matches:
            html += '<h3>Weitere Matches</h3>'
            for listing in ok_matches:
                html += self._listing_to_html(listing, 'listing-ok')

        html += """
            <hr>
            <p style="color: #999; font-size: 12px;">
                Immobilien-Suchagent | Automatische Benachrichtigung
            </p>
        </body>
        </html>
        """
        return html

    def _listing_to_html(self, listing, css_class: str) -> str:
        """Konvertiert ein Listing in HTML."""
        score = listing.total_score or 0
        grade = listing.grade or 'D'
        grade_label = GRADE_EMOJIS.get(grade, grade)

        price_str = format_price(listing.price) if listing.price else 'Preis auf Anfrage'
        rooms_str = f"{listing.rooms} Zimmer" if listing.rooms else ''
        area_str = f"{listing.area_sqm} m²" if listing.area_sqm else ''
        city_str = listing.city or ''

        details = ' | '.join(filter(None, [rooms_str, area_str, city_str]))

        score_breakdown = ''
        if listing.location_score is not None:
            score_breakdown = (
                f"Lage: {listing.location_score}/20 | "
                f"Preis: {listing.price_score}/20 | "
                f"Features: {listing.features_score}/20 | "
                f"ÖV: {listing.transport_score}/20 | "
                f"Bildung: {listing.education_score}/25"
            )

        travel_str = ''
        if listing.travel_time_to_hb and listing.travel_time_to_hb > 0:
            travel_str = f"🚆 {listing.travel_time_to_hb} Min zum HB"

        matur_str = ''
        if listing.maturitaetsquote:
            matur_str = f"🎓 Maturitätsquote: {listing.maturitaetsquote}%"

        return f"""
        <div class="listing {css_class}">
            <span class="score">{score:.0f}/105</span>
            <strong><a href="{listing.url}">{listing.title or 'Inserat'}</a></strong>
            <span class="grade"> — {grade_label}</span>
            <div class="details">
                <strong>{price_str}</strong> | {details}
            </div>
            <div class="details">
                📍 {listing.address or city_str}
                {f' | {travel_str}' if travel_str else ''}
                {f' | {matur_str}' if matur_str else ''}
            </div>
            <div class="score-breakdown">{score_breakdown}</div>
            <div style="margin-top:4px; font-size:12px; color:#999;">
                Plattform: {listing.platform}
            </div>
        </div>
        """

    def _send_email(self, subject: str, html_body: str):
        """Versendet die E-Mail via SMTP."""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_email
            msg['To'] = self.to_email

            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.from_email, self.password)
                server.sendmail(self.from_email, self.to_email, msg.as_string())

            logger.info(f"E-Mail versendet: {subject}")

        except Exception as e:
            logger.error(f"E-Mail-Versand fehlgeschlagen: {e}")
