"""Errors and small input parsers of the dashboard API."""


class ApiError(ValueError):
    """A request the dashboard rejects; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def parse_int(value, label: str, low: int = 0, high: int = 2**31 - 1) -> int:
    # int() would quietly turn True into 1 and 2.5 into 2, so both are refused.
    if isinstance(value, (bool, float)):
        raise ApiError(f"{label}: enter a whole number")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(f"{label}: enter a whole number") from None
    if not low <= number <= high:
        raise ApiError(f"{label}: allowed range is {low} to {high}")
    return number


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("true", "false"):
        return value == "true"
    raise ApiError("Use true or false.")


def parse_guid(value) -> int:
    """An item GUID given as a number or as text such as "0x0250000000001149"."""
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except (TypeError, ValueError):
        raise ApiError("Invalid item") from None
