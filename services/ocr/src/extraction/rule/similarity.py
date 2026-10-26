"""Normalised Levenshtein similarity, for ``accept.mode`` SIMILARITY (IDP-03 ch. 2.5)."""


def similarity(first: str, second: str) -> float:
    """1.0 for equal strings, 0.0 for nothing in common: ``1 - edit distance / length of the longer string``."""
    if first == second:
        return 1.0
    longest = max(len(first), len(second))
    previous = list(range(len(second) + 1))
    for i, left in enumerate(first, start=1):
        current = [i]
        for j, right in enumerate(second, start=1):
            cost = 0 if left == right else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return 1.0 - previous[-1] / longest
