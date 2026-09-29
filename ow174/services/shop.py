"""The cosmetic shop shared by the game lobby and the dashboard.

Prices come from the retail catalog's +0x14 field (+0x10 is an unlock level, not a price). OWL items
cost league tokens, golden weapons cost competitive points, everything else costs credits. The
caller saves the profile and notifies the client.
"""

from threading import RLock

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB, Unlock
from ow174.content.collection import Collection

CURRENCIES = ("credits", "league_tokens", "comp_points")
MAX_PAGE_SIZE = 100
SEARCHED_FIELDS = ("name", "hero", "type", "rarity", "guid")


class ShopError(ValueError):
    """A rejected request with a stable code and an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _currency_for(unlock: Unlock) -> str:
    if "OWL" in unlock.categories:
        return "league_tokens"
    if unlock.type == "WeaponSkin":
        return "comp_points"
    return "credits"


def _sort_key(pair: tuple[int, dict]) -> tuple:
    guid, product = pair
    return (product["hero"].casefold(), product["type"].casefold(), product["name"].casefold(), guid)


def _matches_search(product: dict, search: str) -> bool:
    searchable = " ".join(product[key] for key in SEARCHED_FIELDS).casefold()
    return search in searchable


class ShopService:
    def __init__(self, collection: Collection, items: ItemDB) -> None:
        self.collection = collection
        self.items = items
        self._purchase_lock = RLock()
        self._products = {}
        for guid, entry in collection.store_entries.items():
            unlock = items.get(guid)
            price = entry.get("+0x14", 0)
            if unlock is None or not isinstance(price, int) or price <= 0:
                continue
            self._products[guid] = {
                "guid": f"0x{guid:016X}",
                "name": unlock.name,
                "hero": unlock.hero or "",
                "type": unlock.type,
                "rarity": unlock.rarity,
                "price": price,
                "currency": _currency_for(unlock),
            }
        self._ordered = sorted(self._products.items(), key=_sort_key)

    def catalog(self, profile: Profile, q="", hero="", currency="", page=1, page_size=24) -> dict:
        """List priced products. Owned products are included but marked as not purchasable."""
        for text in (q, hero, currency):
            if not isinstance(text, str):
                raise ShopError("invalid_input", "Invalid search parameters.")
        search = q.strip().casefold()
        hero = hero.strip().casefold()
        currency = currency.strip().casefold()
        if currency and currency not in CURRENCIES:
            raise ShopError("invalid_input", "Unknown currency.")
        page = self._positive_int(page)
        page_size = min(self._positive_int(page_size), MAX_PAGE_SIZE)

        found = []
        for guid, product in self._ordered:
            if hero and product["hero"].casefold() != hero:
                continue
            if currency and product["currency"] != currency:
                continue
            if search and not _matches_search(product, search):
                continue
            found.append((guid, product))

        start = (page - 1) * page_size
        page_items = []
        for guid, product in found[start : start + page_size]:
            owned = bool(profile.unlock_all or self.collection.owns(profile, guid))
            page_items.append({**product, "owned": owned, "purchasable": not owned})
        return {
            "items": page_items,
            "total": len(found),
            "page": page,
            "page_size": page_size,
            "pages": (len(found) + page_size - 1) // page_size,
        }

    def purchase(self, profile: Profile, guid) -> dict:
        """Check the product, charge its price, and add it to the profile's unlocks."""
        guid = self._guid(guid)
        product = self._products.get(guid)
        if product is None:
            raise ShopError("not_purchasable", "This item is not available for purchase.")
        with self._purchase_lock:
            if profile.unlock_all or self.collection.owns(profile, guid):
                raise ShopError("already_owned", "You already have this item.", 409)
            currency = product["currency"]
            price = product["price"]
            balance = getattr(profile, currency)
            if balance < price:
                raise ShopError("insufficient_balance", "Not enough funds.", 409)
            setattr(profile, currency, balance - price)
            profile.unlocked_items = [*profile.unlocked_items, product["guid"]]
        return {"guid": product["guid"], "price": price, "currency": currency}

    @staticmethod
    def _guid(value) -> int:
        """Parse an item GUID given as a number or as text like "0x0250..."."""
        # bool is a subclass of int, so it has to be refused by name.
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ShopError("invalid_input", "Invalid item identifier.")
        if isinstance(value, str):
            try:
                guid = int(value.strip(), 0)
            except ValueError:
                raise ShopError("invalid_input", "Invalid item identifier.") from None
        else:
            guid = value
        if not 0 < guid < 1 << 64:
            raise ShopError("invalid_input", "Invalid item identifier.")
        return guid

    @staticmethod
    def _positive_int(value) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ShopError("invalid_input", "Invalid page number or list size.")
        try:
            number = int(value)
        except ValueError:
            raise ShopError("invalid_input", "Invalid page number or list size.") from None
        if number < 1:
            raise ShopError("invalid_input", "Page number and list size must be positive.")
        return number
