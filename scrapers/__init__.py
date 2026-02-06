from scrapers.immoscout24 import ImmoScout24Scraper
from scrapers.homegate import HomegateScraper
from scrapers.newhome import NewhomeScraper
from scrapers.allreal import AllrealScraper
from scrapers.mobimo import MobimoScraper
from scrapers.comparis import ComparisScraper
from scrapers.flatfox import FlatfoxScraper
from scrapers.neubauprojekte import NeubauprojekteScraper
from scrapers.immostreet import ImmoStreetScraper


SCRAPER_REGISTRY = {
    'immoscout24': ImmoScout24Scraper,
    'homegate': HomegateScraper,
    'newhome': NewhomeScraper,
    'allreal': AllrealScraper,
    'mobimo': MobimoScraper,
    'comparis': ComparisScraper,
    'flatfox': FlatfoxScraper,
    'neubauprojekte': NeubauprojekteScraper,
    'immostreet': ImmoStreetScraper,
}


def get_scrapers_for_tier(config, tier):
    """Gibt alle Scraper für ein bestimmtes Tier zurück."""
    platform_names = config.get('platforms', {}).get(tier, [])
    scrapers = []
    for name in platform_names:
        scraper_class = SCRAPER_REGISTRY.get(name)
        if scraper_class:
            scrapers.append(scraper_class(config))
    return scrapers


def get_all_scrapers(config):
    """Gibt alle verfügbaren Scraper zurück."""
    scrapers = []
    for tier in ['tier1', 'tier2', 'tier3']:
        scrapers.extend(get_scrapers_for_tier(config, tier))
    return scrapers
