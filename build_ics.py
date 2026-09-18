from datetime import datetime, date
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
UTC = ZoneInfo("UTC")


def _fmt_utc(dt_str):
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=PARIS)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fmt_date(d_str):
    return datetime.strptime(d_str, "%Y-%m-%d").strftime("%Y%m%d")


def _escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(events, calendar_name="Planning Effiplan"):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//effiplan-sync//FR",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        "X-WR-TIMEZONE:Europe/Paris",
    ]

    # Fixed (not "now") so re-generating an unchanged schedule produces byte-identical
    # output - otherwise every run would look like a diff and get committed/pushed.
    stable_stamp = "20260101T000000Z"

    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev['id']}@effiplan-sync")
        lines.append(f"DTSTAMP:{stable_stamp}")
        if ev["all_day"]:
            lines.append(f"DTSTART;VALUE=DATE:{_fmt_date(ev['start'])}")
            lines.append(f"DTEND;VALUE=DATE:{_fmt_date(ev['end'])}")
        else:
            lines.append(f"DTSTART:{_fmt_utc(ev['start'])}")
            lines.append(f"DTEND:{_fmt_utc(ev['end'])}")
        lines.append(f"SUMMARY:{_escape(ev['title'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
