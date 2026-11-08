"""Bounded exact token-passage analysis, never source/intent attribution.

Unicode word runs are case-folded; punctuation and whitespace are ignored.
Offsets address Python Unicode code points in the original string (end exclusive).
No fuzzy matching, stemming, language inference or embedded instructions execute.
"""
import re
from .contracts import LearningError, digest

TOKEN = re.compile(r"\w+(?:['’]\w+)*", re.UNICODE)
MAX_TOKENS = 2048
MAX_CHARACTERS = 65536
METHOD = 'unicode-word-casefold-passage/1'


def token_windows(text, minimum=6):
    if type(minimum) is not int or not 3 <= minimum <= 16:
        raise LearningError('invalid_passage_minimum')
    if not isinstance(text, str):
        return None
    if len(text) > MAX_CHARACTERS:
        raise LearningError('passage_input_limit')
    tokens = []
    for match in TOKEN.finditer(text):
        tokens.append((match.group().casefold(), match.start(), match.end()))
        if len(tokens) > MAX_TOKENS:
            raise LearningError('passage_input_limit')
    windows = {}
    for i in range(len(tokens)-minimum+1):
        words = tuple(t[0] for t in tokens[i:i+minimum])
        windows.setdefault(words, [tokens[i][1], tokens[i+minimum-1][2]])
    return windows


def overlap(left, right):
    if left is None or right is None:
        return None
    # Deterministic lexicographic witness, not a similarity/confidence score.
    witness = min(left.keys() & right.keys(), default=None)
    return None if witness is None else {
        'subjectSpan': left[witness], 'anchorSpan': right[witness],
        'normalizedPassageDigest': digest(list(witness)), 'tokenCount': len(witness),
        'offsetUnit': 'unicode-code-point', 'endExclusive': True, 'method': METHOD}
