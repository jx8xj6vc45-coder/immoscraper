"""Telegram Notification Service für neue Inserate."""

import logging
import requests
from typing import List

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sendet Benachrichtigungen über neue Inserate via Telegram."""

    def __init__(self, config: dict):
        telegram_config = config.get('notifications', {}).get('telegram', {})
        self.enabled = telegram_config.get('enabled', False)
        self.bot_token = telegram_config.get('bot_token', '')
        self.chat_id = telegram_config.get('chat_id', '')
        self.min_score = config.get('notifications', {}).get('min_score_to_notify', 60)

    def send_message(self, text: str, parse_mode: str = 'HTML') -> bool:
        """Sendet eine Nachricht an den konfigurierten Chat."""
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': parse_mode,
                'disable_web_page_preview': False
            }
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[telegram] Fehler beim Senden: {e}")
            return False

    def notify_new_listings(self, listings: List) -> int:
        """Benachrichtigt über neue Inserate.

        Returns:
            Anzahl gesendeter Nachrichten
        """
        if not self.enabled:
            return 0

        sent = 0
        for listing in listings:
            # Nur Inserate mit ausreichendem Score
            if listing.total_score and listing.total_score < self.min_score:
                continue

            message = self._format_listing(listing)
            if self.send_message(message):
                sent += 1

        if sent > 0:
            logger.info(f"[telegram] {sent} Benachrichtigungen gesendet")

        return sent

    def notify_summary(self, new_count: int, total_count: int, top_listings: List = None) -> bool:
        """Sendet eine Zusammenfassung nach dem Scraping."""
        if not self.enabled or new_count == 0:
            return False

        message = f"🏠 <b>Immobilien-Update</b>\n\n"
        message += f"📊 <b>{new_count}</b> neue Inserate gefunden\n"
        message += f"📁 Total: {total_count} aktive Inserate\n"

        if top_listings:
            message += f"\n<b>Top {len(top_listings)} neue Inserate:</b>\n"
            for listing in top_listings[:5]:
                message += f"\n{self._format_listing_short(listing)}"

        return self.send_message(message)

    def _format_listing(self, listing) -> str:
        """Formatiert ein Inserat als Telegram-Nachricht."""
        # Emoji basierend auf Grade
        grade_emoji = {
            'A+': '🌟',
            'A': '⭐',
            'B': '✅',
            'C': '📍',
            'D': '📌'
        }.get(listing.grade, '🏠')

        message = f"{grade_emoji} <b>Neues Inserat</b>\n\n"

        if listing.title:
            message += f"<b>{listing.title[:60]}</b>\n"

        if listing.price:
            message += f"💰 CHF {listing.price:,}\n".replace(',', "'")
        else:
            message += "💰 Preis auf Anfrage\n"

        details = []
        if listing.rooms:
            details.append(f"{listing.rooms} Zi")
        if listing.area_sqm:
            details.append(f"{listing.area_sqm} m²")
        if listing.travel_time_to_hb:
            details.append(f"{listing.travel_time_to_hb} Min HB")

        if details:
            message += f"📐 {' | '.join(details)}\n"

        if listing.city or listing.address:
            message += f"📍 {listing.address or listing.city}\n"

        # Score Details
        if listing.total_score:
            message += f"\n📊 <b>Score: {listing.total_score}/100 (Grade {listing.grade})</b>\n"

            score_details = []
            if hasattr(listing, 'steuerfuss') and listing.steuerfuss:
                message += f"💸 Steuerfuss: {listing.steuerfuss}%\n"
            if hasattr(listing, 'maturitaets_quote') and listing.maturitaets_quote:
                message += f"🎓 Maturitätsquote: {listing.maturitaets_quote}%\n"
            if listing.travel_time_to_hb:
                message += f"🚆 ÖV zum HB: {listing.travel_time_to_hb} Min\n"

        message += f"\n🔗 <a href=\"{listing.url}\">Inserat öffnen</a>"
        message += f"\n<i>via {listing.platform}</i>"

        return message

    def _format_listing_short(self, listing) -> str:
        """Kurze Formatierung für Zusammenfassungen."""
        price = f"CHF {listing.price:,}".replace(',', "'") if listing.price else "Preis a.A."
        rooms = f"{listing.rooms} Zi" if listing.rooms else ""
        city = listing.city or ""
        score = f"({listing.total_score})" if listing.total_score else ""

        return f"• {price} | {rooms} | {city} {score}\n  <a href=\"{listing.url}\">→ Link</a>\n"

    def test_connection(self) -> bool:
        """Testet die Telegram-Verbindung."""
        if not self.bot_token or not self.chat_id:
            return False

        return self.send_message("✅ Immobilien-Suchagent verbunden!")
