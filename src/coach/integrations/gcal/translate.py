"""Turn the athlete's (Hebrew) training event titles into short English descriptions.

Word-level dictionary translation for display only: "ריצה- 6 קל" -> "6 km easy",
"ריצה- איכות: 2 חימום+ 6x400 (5:00) + 2 min jog" -> "Quality: 2 km warm-up + 6x400 (5:00)
+ 2 min jog", "מכון- רגליים" -> "Legs". Unknown words are kept as written (never guessed).
The title is untrusted text: it is only ever displayed escaped, never interpreted.
"""

from __future__ import annotations

import re

# Longest phrases first so "קלה" wins over "קל".
_WORDS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        {
            "מחוץ לבית": "outdoors",
            "ריצה ארוכה": "long run",
            "חדר כושר": "gym",
            "איכות": "Quality",
            "חימום": "warm-up",
            "שחרור": "cool-down",
            "התאוששות": "recovery",
            "עליות": "hill repeats",
            "עליה": "hill",
            "ירידה": "downhill",
            "ספרינטים": "sprints",
            "ספרינט": "sprint",
            "טמפו": "tempo",
            "טסט": "test",
            "מבחן": "test",
            "מרוץ": "race",
            "קלה": "easy",
            "קל": "easy",
            "ארוכה": "long",
            "רגליים": "Legs",
            "גב": "Back",
            "חזה": "Chest",
            "כתפיים": "Shoulders",
            "ידיים": "Arms",
            "בטן": "Core",
            "כוח": "strength",
            "הליכה": "walk",
            "ג'וג": "jog",
            "זון": "zone",
            'ק"מ': "km",
            "ק''מ": "km",
            "קמ": "km",
            "דקות": "min",
            "דק'": "min",
            "שניות": "s",
            "שנ'": "s",
            "מטר": "m",
            "ו": "and",
        }.items(),
        key=lambda kv: -len(kv[0]),
    )
)
_ENGLISH = {"fb": "Full body", "upper": "Upper body", "lower": "Lower body"}
_PREFIX = re.compile(r"^\s*(ריצה|מכון|run|gym)\s*[-–:]\s*", re.I)
_KM_BEFORE_WORD = re.compile(r"\b(\d+(?:\.\d+)?)\s+(easy|long|warm-up|cool-down|test|tempo)\b")
_KM = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:km\b)?\s*(?:easy|long)\b|^\s*(\d+(?:\.\d+)?)\s*km\b", re.I
)


def translate_title(title: str | None) -> str:
    if not title:
        return ""
    text = _PREFIX.sub("", title.strip()[:200])
    for he, en in _WORDS:
        if len(he) == 1:  # single letters ("ו" = and) only as standalone words
            text = re.sub(rf"(?<!\S){re.escape(he)}(?!\S)", en, text)
        else:
            text = text.replace(he, en)
    text = re.sub(r"\b(fb|upper|lower)\b", lambda m: _ENGLISH[m.group(1).lower()], text, flags=re.I)
    text = _KM_BEFORE_WORD.sub(r"\1 km \2", text)
    text = re.sub(r"\s*\+\s*", " + ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–:")
    return text[:1].upper() + text[1:] if text else ""


def distance_km(title: str | None) -> float | None:
    """The run distance stated in a title ("ריצה- 11 קל" -> 11.0), or None."""
    text = translate_title(title)
    m = _KM.search(text)
    if not m:
        return None
    value = float(m.group(1) or m.group(2))
    return value if 0 < value <= 60 else None
