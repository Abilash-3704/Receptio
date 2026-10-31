from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Clinic location is fictional, in Bengaluru. All time interpretation uses this timezone.
CLINIC_TZ = ZoneInfo("Asia/Kolkata")

# The clock seam: anything returning an aware "now". Tests inject a fake so Hold expiry
# never needs a sleep.
Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)
