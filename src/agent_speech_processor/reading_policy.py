"""Shared interpretation of declared transcript reading dispositions."""
import re
from .publication import regular

BEGIN = "<!-- agent-speech-processor reading guide v1 -->"
END = "<!-- end agent-speech-processor reading guide -->"


def validate_markers(text):
    for marker in re.findall(r"<!-- (?:end )?[a-z0-9-]+ reading guide(?: v[0-9]+)? -->", text):
        if marker not in (BEGIN, END):
            raise ValueError("Unsupported reading-guide marker; preserve the guide for inspection")


def disposition(text):
    validate_markers(text)
    text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), "", text, flags=re.S)
    values = re.findall(r"^Reading disposition:[ \t]*(.*?)[ \t\r]*$", text, re.M)
    if len(values) > 1 or (values and values[0] not in ("unusable", "unspecified")):
        raise ValueError("Use one Reading disposition field: unusable or unspecified")
    return values[0] if values else "unspecified"


def file_disposition(path):
    """Read the current policy; an absent recording note has no exclusion."""
    text = regular(path).read_bytes().decode("utf-8") if path.exists() or path.is_symlink() else ""
    return disposition(text)
