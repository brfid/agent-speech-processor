"""Identify opaque Markdown regions without imposing link-relocation policy."""
import re


def opaque_regions(text):
    regions, cursor = [], 0
    while cursor < len(text):
        # Consume comments before looking for Markdown inside their contents.
        if text.startswith("<!--", cursor):
            end = text.find("-->", cursor + 4)
            if end < 0:
                raise ValueError("Unclosed HTML annotation; preserve it for inspection")
            end += 3
            regions.append((cursor, end, "comment"))
            cursor = end
            continue
        if cursor == 0 or text[cursor - 1] == "\n":
            end = text.find("\n", cursor)
            end = len(text) if end < 0 else end + 1
            line = text[cursor:end]
            fence = re.match(r" {0,3}(`{3,}|~{3,})", line)
            if fence:
                marker = fence[1]
                close = re.search(r"(?m)^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker))
                                  + r",}[ \t]*\r?$", text[end:])
                if close is None:
                    raise ValueError("Unclosed Markdown code fence; preserve it for inspection")
                stop = end + close.end()
                if stop < len(text) and text[stop] == "\n":
                    stop += 1
                regions.append((cursor, stop, "fenced_code"))
                cursor = stop
                continue
            if line.startswith(("    ", "\t")):
                regions.append((cursor, end, "indented_code"))
                cursor = end
                continue
        if text[cursor] == "`":
            end = cursor
            while end < len(text) and text[end] == "`":
                end += 1
            close = next((end + m.start() for m in re.finditer(r"`+", text[end:])
                          if len(m[0]) == end - cursor), None)
            if close is None:
                raise ValueError("Unclosed inline code; preserve it for inspection")
            close += end - cursor
            regions.append((cursor, close, "inline_code"))
            cursor = close
            continue
        cursor += 1
    return regions


def protected_mask(text, regions=None):
    result = bytearray(len(text))
    for start, end, _ in opaque_regions(text) if regions is None else regions:
        result[start:end] = b"\1" * (end - start)
    return result
