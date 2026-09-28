"""A shared cosmetic shop for the game lobby and local dashboard.

Prices come from the retail catalog's +0x14 field. +0x10 is an unlock
level, not a currency ID. Currency comes from extracted unlock metadata:
OWL products use league tokens, golden weapons use competitive points,
and the remaining priced cosmetics use credits. Persistence and client
notifications belong to the caller.
"""

from threading import RLock


CURRENCIES = ("credits", "league_tokens", "comp_points")


class ShopError(ValueError):
    """A rejected request with a stable code and an HTTP-compatible status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class ShopService:
    def __init__(self, content, items):
        self.content = content
        self.items = items
        self._purchase_lock = RLock()
        self._products = {}
        for guid, entry in content.store_entries.items():
            unlock = items.get(guid)
            price = entry.get("+0x14", 0)
            if unlock is None or not isinstance(price, int) or price <= 0:
                continue
            if "OWL" in unlock.categories:
                currency = "league_tokens"
            elif unlock.type == "WeaponSkin":
                currency = "comp_points"
            else:
                currency = "credits"
            self._products[guid] = {
                "guid": f"0x{guid:016X}", "name": unlock.name,
                "hero": unlock.hero or "", "type": unlock.type,
                "rarity": unlock.rarity, "price": price, "currency": currency,
            }
        self._ordered = sorted(self._products.items(), key=lambda pair: (
            pair[1]["hero"].casefold(), pair[1]["type"].casefold(),
            pair[1]["name"].casefold(), pair[0]))

    def catalog(self, profile, q="", hero="", currency="", page=1, page_size=24) -> dict:
        """List priced products, including owned products with a disabled purchase flag."""
        if not all(isinstance(value, str) for value in (q, hero, currency)):
            raise ShopError("invalid_input", "Некорректные параметры поиска.")
        q, hero, currency = q.strip().casefold(), hero.strip().casefold(), currency.strip().casefold()
        if currency and currency not in CURRENCIES:
            raise ShopError("invalid_input", "Неизвестная валюта.")
        page = self._positive_int(page)
        page_size = min(self._positive_int(page_size), 100)
        filtered = []
        for guid, product in self._ordered:
            if hero and product["hero"].casefold() != hero:
                continue
            if currency and product["currency"] != currency:
                continue
            if q and q not in " ".join(product[key] for key in ("name", "hero", "type", "rarity", "guid")).casefold():
                continue
            filtered.append((guid, product))
        total = len(filtered)
        start = (page - 1) * page_size
        result = []
        for guid, product in filtered[start:start + page_size]:
            owned = bool(profile.unlock_all or self.content.owns(profile, guid))
            result.append({**product, "owned": owned, "purchasable": not owned})
        return {"items": result, "total": total, "page": page, "page_size": page_size,
                "pages": (total + page_size - 1) // page_size}

    def purchase(self, profile, guid) -> dict:
        """Validate a product, debit its currency, and record ownership once."""
        guid = self._guid(guid)
        product = self._products.get(guid)
        if product is None:
            raise ShopError("not_purchasable", "Этот предмет недоступен для покупки.")
        with self._purchase_lock:
            if profile.unlock_all or self.content.owns(profile, guid):
                raise ShopError("already_owned", "Этот предмет уже получен.", 409)
            currency, price = product["currency"], product["price"]
            balance = getattr(profile, currency)
            if balance < price:
                raise ShopError("insufficient_balance", "Недостаточно средств для покупки.", 409)
            unlocked_items = [*profile.unlocked_items, product["guid"]]
            setattr(profile, currency, balance - price)
            profile.unlocked_items = unlocked_items
        return {"guid": product["guid"], "price": price, "currency": currency}

    @staticmethod
    def _guid(value) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ShopError("invalid_input", "Некорректный идентификатор предмета.")
        try:
            guid = int(value.strip(), 0) if isinstance(value, str) else value
        except ValueError:
            raise ShopError("invalid_input", "Некорректный идентификатор предмета.") from None
        if not 0 < guid < 1 << 64:
            raise ShopError("invalid_input", "Некорректный идентификатор предмета.")
        return guid

    @staticmethod
    def _positive_int(value) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ShopError("invalid_input", "Некорректный номер страницы или размер списка.")
        try:
            number = int(value)
        except ValueError:
            raise ShopError("invalid_input", "Некорректный номер страницы или размер списка.") from None
        if number < 1:
            raise ShopError("invalid_input", "Номер страницы и размер списка должны быть положительными.")
        return number
