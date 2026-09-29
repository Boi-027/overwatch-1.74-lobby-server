"""The time the lobby tells the client."""

import calendar
import time

NOON = 12 * 3600


def server_time(profile) -> float:
    """Noon of the profile's server_date, or the real time when the date is empty or "now"."""
    date = (profile.server_date or "").strip()
    if date and date.lower() != "now":
        return calendar.timegm(time.strptime(date[:10], "%Y-%m-%d")) + NOON
    return time.time()


def stu_datetime(seconds: float) -> int:
    """Pack a Unix time into the game's date format."""
    utc = time.gmtime(seconds)
    # Bit layout: year since 2000 at bit 36, then month, day, hour, minute and second.
    return (
        ((utc.tm_year - 2000) << 36)
        | (utc.tm_mon << 32)
        | (utc.tm_mday << 27)
        | (utc.tm_hour << 22)
        | (utc.tm_min << 16)
        | (utc.tm_sec << 10)
    )
