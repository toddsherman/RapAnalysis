"""Words never shown on the public site (slurs). Data-driven word lists are filtered
through this before export; the curated lexicon simply leaves them out."""
import re

BLOCK_RE = re.compile(
    r"^(?:n+[i1]+g+(?:a+|ah|uh|as|az|ers?|ga|gaz|gas|gah|guh)?s?z?|niggaz|nigg\w*|"
    r"fag+(?:ot|it|s)?s?|faggot\w*|dykes?|tranny|trannies|retards?|retarded|"
    r"chinks?|spics?|wetbacks?|kikes?|gooks?|beaners?|coons?)$"
)


def blocked(word: str) -> bool:
    return bool(BLOCK_RE.match(word))
