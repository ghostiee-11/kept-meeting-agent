"""Turning spoken deadlines into dates.

"Friday" is not a date. It is a date relative to when the meeting happened, in
the timezone the speakers were in, and it means a different day in Gurugram
than it does in San Francisco.

Most of it is arithmetic, so most of it is done here rather than by a model:
weekday names, "tomorrow", "end of next week", "in two weeks", "EOD". What is
left over genuinely needs either language understanding or the web ("before the
Diwali break", "after the client demo"), and that is what Chronos is for.

Returning `None` is a real answer. A deadline the system cannot resolve becomes
a clarification for a human, which is better than a confident guess that
quietly moves someone's due date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mon": 0,
    "tue": 1,
    "tues": 1,
    "wed": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

# How sure we are, by route. A weekday name is unambiguous once you know the
# meeting date; "end of the month" is a convention we have chosen.
_CONFIDENCE = {
    "explicit": 1.0,
    "weekday": 0.95,
    "next_weekday": 0.8,
    "offset": 0.95,
    "period": 0.8,
    "vague": 0.5,
}


@dataclass(frozen=True)
class Resolved:
    due: date | None
    confidence: float
    method: str
    raw: str

    @property
    def needs_help(self) -> bool:
        """Whether this should go to Chronos, and possibly to a human."""
        return self.due is None or self.confidence < 0.6


def resolve(phrase: str | None, *, meeting_date: date, timezone: str = "UTC") -> Resolved:
    """Resolve a spoken deadline against the meeting's own date.

    `timezone` is accepted so a caller can pass the meeting's zone and get the
    same answer the people in the room would have given.
    """
    if not phrase or not phrase.strip():
        return Resolved(None, 0.0, "absent", phrase or "")

    text = re.sub(r"\s+", " ", phrase.strip().lower())
    text = re.sub(r"^(by|on|before|due|until|no later than)\s+", "", text)

    for rule in (_explicit_date, _named_day, _offset, _period, _weekday):
        found = rule(text, meeting_date)
        if found is not None:
            return Resolved(found[0], _CONFIDENCE[found[1]], found[1], phrase)

    return Resolved(None, 0.0, "unresolved", phrase)


def _explicit_date(text: str, _: date) -> tuple[date, str] | None:
    """An ISO date, or a day and month written out."""
    if iso := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            return date(*(int(part) for part in iso.groups())), "explicit"
        except ValueError:
            return None
    return None


def _named_day(text: str, meeting_date: date) -> tuple[date, str] | None:
    if "today" in text or "end of day" in text or re.fullmatch(r"eod", text):
        return meeting_date, "offset"
    if "tomorrow" in text:
        return meeting_date + timedelta(days=1), "offset"
    return None


def _offset(text: str, meeting_date: date) -> tuple[date, str] | None:
    """`in three days`, `in 2 weeks`, `next month`."""
    words = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

    match = re.search(r"\bin (\d+|a|one|two|three|four|five|six) (day|week|month)s?\b", text)
    if match:
        count = int(match.group(1)) if match.group(1).isdigit() else words[match.group(1)]
        unit = match.group(2)
        days = {"day": 1, "week": 7, "month": 30}[unit] * count
        return meeting_date + timedelta(days=days), "offset"

    if "next month" in text:
        return meeting_date + timedelta(days=30), "period"
    return None


def _period(text: str, meeting_date: date) -> tuple[date, str] | None:
    """`end of the week`, `end of next week`, `end of the month`, `this sprint`.

    "End of week" means Friday, not Sunday. Teams mean the working week, and
    resolving to Sunday would put every deadline two days late.
    """
    end_of_this_week = meeting_date + timedelta(days=(4 - meeting_date.weekday()) % 7)

    if re.search(r"\b(eow|end of (the )?week)\b", text):
        return end_of_this_week, "period"
    if re.search(r"\bend of next week\b", text) or re.search(r"\bnext week\b", text):
        return end_of_this_week + timedelta(days=7), "period"
    if re.search(r"\b(eom|end of (the )?month)\b", text):
        first_of_next = (meeting_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        return first_of_next - timedelta(days=1), "period"
    if re.search(r"\b(this|next) sprint\b", text):
        # A convention, not a fact. Two weeks is the common cadence, and the
        # low confidence is what sends it to a human when it matters.
        return meeting_date + timedelta(days=14), "vague"
    if re.search(r"\b(end of (the )?quarter|eoq)\b", text):
        quarter_end_month = 3 * ((meeting_date.month - 1) // 3) + 3
        first_of_next = date(
            meeting_date.year + (quarter_end_month == 12),
            (quarter_end_month % 12) + 1,
            1,
        )
        return first_of_next - timedelta(days=1), "period"
    return None


def _weekday(text: str, meeting_date: date) -> tuple[date, str] | None:
    """`Friday`, `next Tuesday`.

    A bare weekday is the next one strictly after the meeting: someone saying
    "Friday" in Friday's standup means the Friday after, not the one they are
    standing in.

    "next X" is the X in the following calendar week. English genuinely
    disagrees with itself here, some people mean the very next occurrence, so
    it is scored slightly lower and the spoken phrase is kept alongside the
    date for a human to check.
    """
    match = re.search(r"\b(next|this|coming)?\s*(" + "|".join(WEEKDAYS) + r")\b", text)
    if not match:
        return None

    target = WEEKDAYS[match.group(2)]

    if (match.group(1) or "").strip() == "next":
        monday_next_week = meeting_date + timedelta(days=7 - meeting_date.weekday())
        return monday_next_week + timedelta(days=target), "next_weekday"

    return meeting_date + timedelta(days=(target - meeting_date.weekday()) % 7 or 7), "weekday"


def today_in(timezone: str) -> date:
    """Current date where the meeting happened, not where the server runs."""
    from datetime import datetime

    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except Exception:  # noqa: BLE001 - an unknown zone must not break a run
        return datetime.now(ZoneInfo("UTC")).date()
