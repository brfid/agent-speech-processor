"""Interval accounting and reproducible word/speaker error metrics."""
import itertools
import math


def union(spans):
    merged = []
    for start, end in sorted(spans):
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("Invalid coverage interval")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def subtract(spans, covered):
    result = []
    for start, end in union(spans):
        for a, b in union(covered):
            if b <= start or a >= end:
                continue
            if a > start:
                result.append([start, a])
            start = max(start, b)
            if start >= end:
                break
        if start < end:
            result.append([start, end])
    return result


def coverage(duration, results):
    accepted = union([(r["span"]["core_start"], r["span"]["core_end"])
                      for r in results if r.get("accepted")])
    missing = subtract([(0, duration)], accepted)
    return {"duration_seconds": duration, "accepted_intervals": accepted,
            "reviewed_seconds": sum(b - a for a, b in accepted),
            "unreviewed_intervals": missing, "complete": not missing}


def errors(reference, hypothesis):
    from .review import words, distance
    ref, hyp = words(reference), words(hypothesis)
    return {"errors": distance(ref, hyp), "reference_words": len(ref),
            "wer": distance(ref, hyp) / max(1, len(ref))}


def speaker_errors(reference, hypothesis):
    """Permutation-invariant concatenated speaker WER; at most four voices."""
    from .review import words, distance
    def groups(items):
        result = {}
        for item in sorted(items, key=lambda s: s["start"]):
            result.setdefault(item["speaker"], []).extend(words(item["text"]))
        return list(result.values())
    ref, hyp = groups(reference), groups(hypothesis)
    n = max(len(ref), len(hyp))
    if n > 4:
        raise ValueError("Bounded speaker evaluation supports at most four speakers")
    ref += [[] for _ in range(n - len(ref))]
    hyp += [[] for _ in range(n - len(hyp))]
    count = sum(len(x) for x in ref)
    error = min((sum(distance(a, b) for a, b in zip(ref, candidate))
                 for candidate in itertools.permutations(hyp)), default=0)
    return {"errors": error, "reference_words": count, "cpwer": error / max(1, count)}
