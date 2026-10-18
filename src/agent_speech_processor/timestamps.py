"""Parse and format positions on an original recording's timeline."""
import math
import re


def parse_time(value):
    """Accept nonnegative seconds or a source HH:MM:SS[.mmm] locator."""
    value = str(value)
    if re.fullmatch(r"\d+(?:\.\d{1,3})?", value):
        seconds = float(value)
    elif re.fullmatch(r"\d{2,}:[0-5]\d:[0-5]\d(?:\.\d{1,3})?", value):
        hours, minutes, seconds = value.split(":")
        try:
            seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except OverflowError as exc:
            raise ValueError("Time must be finite") from exc
    else:
        raise ValueError("Time must be nonnegative seconds or HH:MM:SS.mmm (up to millisecond precision)")
    if not math.isfinite(seconds):
        raise ValueError("Time must be finite")
    return seconds


def timecode(seconds):
    """Format a nonnegative recording position, rounding before clock rollover."""
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or seconds < 0):
        raise ValueError("Time must be a finite nonnegative number of seconds")
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{ms:03}"
