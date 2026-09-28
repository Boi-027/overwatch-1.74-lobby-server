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
    country: str      # ISO 3166 alpha-3, sent in the lobby connect record
    currency: int     # ISO 4217 numeric code
    template: str     # price display, {} is the amount
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
    """Return value with RUB prices and the RUS country swapped for the region's; RU keeps the capture."""
    region = region_of(code)
    if region.currency == RUB_CURRENCY:
        return value
    tiers = _TIERS.get(region.code, _TIERS["US"])
    if isinstance(value, dict):
        if value.get("+0x10") == RUB_CURRENCY and isinstance(value.get("+0x18"), str):
            rub = _rub_whole(value["+0x18"])
            if rub in tiers:
                amount = tiers[rub]
                shown = region.display(amount)
                out = dict(value)
                out.update({"+0x0": round(amount * 10000), "+0x8": round(amount * 10000),
                            "+0x10": region.currency, "+0x18": shown, "+0x40": shown})
                return out
        out = {k: localize(v, code) for k, v in value.items()}
        if value.get("+0x78") == RUB_CURRENCY and isinstance(value.get("+0x80"), list):
            out["+0x78"] = region.currency  # the store message's own currency code
        return out
    if isinstance(value, list):
        return [localize(v, code) for v in value]
    if value == RUB_COUNTRY:
        return region.country
    return value
