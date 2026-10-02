from __future__ import annotations

import functools
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from ._util import resolve_under_root as _resolve_under_root

DEFAULT_ACTION_CLASSES = {
    "default_class": "review_required",
    "classes": {
        "safe_local_transform": ["docs", "format", "summarize"],
        "review_required": [
            "commit",
            "push",
            "deploy",
            "deployment",
            "redeploy",
            "publish",
            "republish",
            "send",
            "resend",
            "email",
            "execute",
            "run script",
            "delete",
            "deletion",
            "remove",
            "rm -rf",
            "sudo",
            "drop table",
            "truncate table",
            "git reset --hard",
            "terraform apply",
            "kubectl apply",
            "wire transfer",
            "transfer funds",
            "bank transfer",
            "| sh",
            "| bash",
            "oversized_input",
        ],
        "blocked": ["credential", "secret", "production", "password", "api key", "private key"],
        "forbidden": [],
    },
}


# --- keyword matching --------------------------------------------------------
#
# The gate is a tripwire, not a guarantee. Matching is normalized (NFKC, zero-width
# characters stripped, case-folded, common look-alike letters mapped, spaced-out
# letters collapsed) and whole-word with simple inflections, so "deployed" and
# "send_email" trip it but "committee", "resend the sender list" and
# "reproduction" do not. List explicit forms (e.g. "deployment", "resend") in the
# SOP when a bare keyword's inflections are not enough.

# ponytail: a hand-picked set of Cyrillic/Greek look-alikes, not the full Unicode
# confusables table. Swap that in if homoglyph evasion becomes a real concern.
_LOOKALIKES = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
        "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s",
        "ԁ": "d", "һ": "h", "ο": "o", "ν": "v", "ρ": "p",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "τ": "t",
        "υ": "u",
    }
)
_SEPARATORS = " ._-*"
_SPACED_LETTERS = re.compile(r"(?<!\w)(?:\w[ ._\-*]){2,}\w(?!\w)")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.casefold().translate(_LOOKALIKES).split())


def _collapse_spaced_letters(text: str) -> str:
    # "d e p l o y" / "d.e.p.l.o.y" -> "deploy"
    return _SPACED_LETTERS.sub(
        lambda match: "".join(ch for ch in match.group() if ch not in _SEPARATORS), text
    )


@functools.lru_cache(maxsize=None)
def _keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    word = normalize_text(keyword)
    if not word:
        return None
    base = re.escape(word).replace("\\ ", " ")
    forms = [base + r"(?:s|es|d|ed|ing)?"]
    if word.endswith("e"):
        forms.append(re.escape(word[:-1]) + "ing")  # delete -> deleting
    if word[-1] in "tpdgn":
        forms.append(base + re.escape(word[-1]) + "(?:ed|ing)")  # commit -> committed
    # Word boundary on letters/digits only, so snake_case identifiers still match.
    return re.compile(r"(?<![^\W_])(?:" + "|".join(forms) + r")(?![^\W_])")


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    normalized = normalize_text(text)
    haystacks = (normalized, _collapse_spaced_letters(normalized))
    matches: list[str] = []
    for keyword in keywords:
        pattern = _keyword_pattern(str(keyword))
        if pattern is not None and any(pattern.search(hay) for hay in haystacks):
            matches.append(str(keyword))
    return matches


# --- action classification ---------------------------------------------------


def classify_action(root: Path | str, action: str) -> str:
    return _classify(_read_action_config(Path(root).resolve()), action)


def classify_reasons(root: Path | str, reasons: list[str]) -> str:
    config = _read_action_config(Path(root).resolve())
    classes = [_classify(config, reason) for reason in reasons]
    for action_class in ("forbidden", "blocked", "review_required"):
        if action_class in classes:
            return action_class
    return "review_required"


def _classify(config: dict[str, Any], action: str) -> str:
    normalized = action.lower()
    for action_class, terms in config["classes"].items():
        if normalized in {str(term).lower() for term in terms}:
            return str(action_class)
    return str(config["default_class"])


def _read_action_config(root: Path) -> dict[str, Any]:
    config = {
        "default_class": DEFAULT_ACTION_CLASSES["default_class"],
        "classes": dict(DEFAULT_ACTION_CLASSES["classes"]),
    }
    config_path = _resolve_under_root(root, "config/action_classes.json")
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        config["default_class"] = loaded.get("default_class", config["default_class"])
        config["classes"] = loaded.get("classes", config["classes"])
    return config
