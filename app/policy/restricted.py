"""Restricted-information detection.

Deterministic phrase matching over everything that would leave the building:
the topic, the core message, and every platform variant body, first comment,
and CTA.

This is a floor, not a ceiling. It catches the categories RDX has already been
burned by or is contractually exposed on. It is not a substitute for the human
approval gate, which is why a match in the ``require_human_approval`` bucket
routes to a person rather than trying to decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class RestrictedHit:
    category: str
    phrase: str
    field: str
    bucket: str

    def to_json(self) -> Dict[str, str]:
        return {
            "category": self.category,
            "phrase": self.phrase,
            "field": self.field,
            "bucket": self.bucket,
        }

    def describe(self) -> str:
        return "%s matched %r in %s" % (self.category, self.phrase, self.field)


def scan(
    fields: Sequence[Tuple[str, str]],
    categories: Iterable[Dict[str, Any]],
    bucket: str,
) -> List[RestrictedHit]:
    hits: List[RestrictedHit] = []
    for category in categories:
        key = str(category.get("key", "UNKNOWN"))
        for phrase in category.get("match", []) or []:
            needle = str(phrase).lower().strip()
            if not needle:
                continue
            pattern = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(needle), re.IGNORECASE)
            for field_name, text in fields:
                if text and pattern.search(text):
                    hits.append(
                        RestrictedHit(
                            category=key, phrase=str(phrase), field=field_name, bucket=bucket
                        )
                    )
    return hits


def scan_phrases(
    fields: Sequence[Tuple[str, str]], phrases: Iterable[str], category: str, bucket: str
) -> List[RestrictedHit]:
    return scan(fields, [{"key": category, "match": list(phrases)}], bucket)
