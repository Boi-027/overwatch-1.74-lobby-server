"""Account regions: which currency, price format and country the client is shown.

The captured retail store lists real-money loot box bundles priced in RUB, so every player saw
rubles. Prices here follow the profile's region. Purchases are still refused offline; this only
changes what the shop displays.
"""

from dataclasses import dataclass

RUB_CURRENCY = 643
RUB_COUNTRY = "RUS"

# The five captured bundle prices (whole RUB) and the price of the same bundle per region.
_TIERS = {
    "US": {119: 1.99, 299: 4.99, 599: 9.99, 1199: 19.99, 2399: 39.99},
    "EU": {119: 1.99, 299: 4.99, 599: 9.99, 1199: 19.99, 2399: 39.99},
    "GB": {119: 1.79, 299: 4.49, 599: 8.99, 1199: 17.99, 2399: 35.99},
}


@dataclass(frozen=True)
class Region:
    code: str
    label: str
    country: str  # ISO 3166 alpha-3, sent in the lobby connect record
    currency: int  # ISO 4217 numeric code
    template: str  # price display, {} is the amount
    decimal_comma: bool = False

    def display(self, amount: float) -> str:
        text = f"{amount:.2f}"
        return self.template.format(text.replace(".", ",") if self.decimal_comma else text)


REGIONS = {
    "US": Region("US", "United States (USD)", "USA", 840, "${}"),
    "EU": Region("EU", "Europe (EUR)", "DEU", 978, "{} €", decimal_comma=True),
    "GB": Region("GB", "United Kingdom (GBP)", "GBR", 826, "£{}"),
    "RU": Region("RU", "Russia (RUB)", RUB_COUNTRY, RUB_CURRENCY, "{} RUB", decimal_comma=True),
}
DEFAULT_REGION = "US"


def region_of(code: str) -> Region:
    return REGIONS.get(code, REGIONS[DEFAULT_REGION])


def _rub_whole(text: str) -> int | None:
    """'1199,00 RUB' -> 1199."""
    head = text.split(" ")[0].split(",")[0]
    return int(head) if head.isdigit() else None


def localize(value, code: str):
    """Return value with RUB prices and the RUS country swapped for the region's. RU keeps the capture."""
    region = region_of(code)
    if region.currency == RUB_CURRENCY:
        return value
    return _localize(value, region, _TIERS.get(region.code, _TIERS["US"]))


def _localize(value, region: Region, tiers: dict[int, float]):
    if isinstance(value, dict):
        price = _localized_price(value, region, tiers)
        if price is not None:
            return price
        out = {}
        for key, item in value.items():
            out[key] = _localize(item, region, tiers)
        # The store message has its own currency code next to its product list.
        if value.get("+0x78") == RUB_CURRENCY and isinstance(value.get("+0x80"), list):
            out["+0x78"] = region.currency
        return out
    if isinstance(value, list):
        return [_localize(item, region, tiers) for item in value]
    if value == RUB_COUNTRY:
        return region.country
    return value


def _localized_price(value: dict, region: Region, tiers: dict[int, float]) -> dict | None:
    """A copy of a known RUB price record in the region's currency, or None for anything else.

    A price record holds the amount twice in 1/10000 units (+0x0, +0x8), the currency (+0x10) and
    the display text twice (+0x18, +0x40).
    """
    if value.get("+0x10") != RUB_CURRENCY or not isinstance(value.get("+0x18"), str):
        return None
    rub = _rub_whole(value["+0x18"])
    if rub not in tiers:
        return None
    amount = tiers[rub]
    shown = region.display(amount)
    price = dict(value)
    price["+0x0"] = round(amount * 10000)
    price["+0x8"] = round(amount * 10000)
    price["+0x10"] = region.currency
    price["+0x18"] = shown
    price["+0x40"] = shown
    return price
