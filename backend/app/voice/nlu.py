"""Deterministic natural-language understanding for the credential-free engine.

This is not a model and does not pretend to be one. It is an explicit, pure,
unit-testable NLU layer with three jobs:

* normalise an utterance (contractions, punctuation, smart quotes, unicode);
* extract entities with real grammars rather than one anchored regex --
  spelled-out numbers, unit synonyms, unicode place names, trailing politeness;
* classify intent by scoring weighted evidence at token boundaries, so
  "personal" cannot trigger the human-handoff intent and "management" cannot
  trigger it via a substring of "agent".

Search slots are resolved against the caller's actual workspace vocabulary
(the distributor categories and locations in their tenant), so the engine is
not limited to whatever nouns happen to appear in a demo script.

Everything here is side-effect free: no database, no network, no I/O. The
session loop in app.voice.mock owns all of that.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

_CONTRACTIONS = {
    "i'd": "i would",
    "i'll": "i will",
    "i'm": "i am",
    "i've": "i have",
    "we'd": "we would",
    "we'll": "we will",
    "we're": "we are",
    "we've": "we have",
    "what's": "what is",
    "where's": "where is",
    "who's": "who is",
    "that's": "that is",
    "it's": "it is",
    "let's": "let us",
    "don't": "do not",
    "doesn't": "does not",
    "can't": "cannot",
    "won't": "will not",
    "couldn't": "could not",
    "wouldn't": "would not",
    "shouldn't": "should not",
    "isn't": "is not",
    "aren't": "are not",
}

_SMART_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})

# Filler that may trail a destination or product without belonging to it.
_TRAILING_FILLER = {
    "please", "thanks", "thank", "you", "asap", "urgently", "urgent",
    "immediately", "soon", "quickly", "now", "today", "tomorrow",
    "ok", "okay", "cheers", "kindly",
}

_LEADING_ARTICLES = {"a", "an", "the", "some", "any", "me", "us"}


def normalise(text: str) -> str:
    """Lowercase, expand contractions and strip punctuation noise."""
    value = unicodedata.normalize("NFC", text).translate(_SMART_QUOTES).strip().lower()
    words = [_CONTRACTIONS.get(word, word) for word in value.split()]
    return " ".join(words)


def tokenise(text: str) -> list[str]:
    """Split into word tokens, preserving unicode letters, digits and hyphens."""
    # Grouped digits ("1,000") must survive as one token or the thousands
    # separator silently truncates the quantity.
    return [
        token
        for token in re.findall(
            r"\d[\d,]*\d|[^\W_]+(?:[-'][^\W_]+)*", normalise(text), re.UNICODE
        )
        if token
    ]


def _strip_filler(tokens: Sequence[str]) -> list[str]:
    result = list(tokens)
    while result and result[-1] in _TRAILING_FILLER:
        result.pop()
    while result and result[0] in _LEADING_ARTICLES:
        result.pop(0)
    return result


def titlecase(tokens: Sequence[str]) -> str:
    """Title-case a phrase without destroying interior capitals or accents."""
    return " ".join(token[:1].upper() + token[1:] for token in tokens if token)


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

_SMALL_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "million": 1_000_000}
_NUMBER_WORDS = set(_SMALL_NUMBERS) | set(_TENS) | set(_SCALES)

_DIGITS = re.compile(r"^\d[\d,]*$")


def parse_number(tokens: Sequence[str], start: int = 0) -> tuple[int | None, int]:
    """Read a number starting at ``start``. Returns (value, tokens consumed).

    Handles digit forms ("50", "1,000"), words ("fifty", "two hundred"),
    mixed connectives ("a hundred and fifty") and the article-as-one form
    ("a tonne of rice"). Returns (None, 0) when no number is present.
    """
    total = 0
    current = 0
    index = start
    seen = False

    while index < len(tokens):
        token = tokens[index]

        if _DIGITS.match(token):
            current += int(token.replace(",", ""))
        elif token in _SMALL_NUMBERS:
            current += _SMALL_NUMBERS[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token in _SCALES:
            scale = _SCALES[token]
            if scale == 100:
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * scale
                current = 0
        elif token in {"a", "an"} and not seen:
            # "a tonne of rice" -- only a number if a unit or scale follows.
            following = tokens[index + 1] if index + 1 < len(tokens) else ""
            if following not in _SCALES and resolve_unit(tokens, index + 1)[0] is None:
                break
            current += 1
        elif token == "and" and seen:
            # Only bridge when a number genuinely continues after the "and".
            following = tokens[index + 1] if index + 1 < len(tokens) else ""
            if following not in _NUMBER_WORDS and not _DIGITS.match(following):
                break
            index += 1
            continue
        else:
            break

        seen = True
        index += 1

    if not seen:
        return None, 0
    return total + current, index - start


# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------

# Canonical units are constrained by app.schemas.EnquiryCreate.
_UNIT_PHRASES: list[tuple[tuple[str, ...], str]] = [
    (("metric", "tonnes"), "tonnes"),
    (("metric", "tonne"), "tonnes"),
    (("metric", "tons"), "tonnes"),
    (("metric", "ton"), "tonnes"),
]
_UNIT_WORDS = {
    "tonnes": "tonnes", "tonne": "tonnes", "tons": "tonnes", "ton": "tonnes",
    "t": "tonnes", "mt": "tonnes", "mts": "tonnes",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg", "kilogramme": "kg", "kilogrammes": "kg",
    "unit": "units", "units": "units", "piece": "units", "pieces": "units",
    "pc": "units", "pcs": "units",
}

# Recognised but not representable in the enquiry schema. Naming them lets the
# agent say something useful instead of silently failing to parse.
_UNSUPPORTED_UNITS = {
    "bag", "bags", "sack", "sacks", "carton", "cartons", "box", "boxes",
    "crate", "crates", "container", "containers", "pallet", "pallets",
    "quintal", "quintals", "litre", "litres", "liter", "liters", "lb", "lbs",
    "pound", "pounds", "bushel", "bushels", "truckload", "truckloads",
}


def resolve_unit(tokens: Sequence[str], index: int) -> tuple[str | None, int]:
    """Resolve a unit at ``index``. Returns (canonical unit, tokens consumed)."""
    for phrase, canonical in _UNIT_PHRASES:
        if tuple(tokens[index : index + len(phrase)]) == phrase:
            return canonical, len(phrase)
    if index < len(tokens) and tokens[index] in _UNIT_WORDS:
        return _UNIT_WORDS[tokens[index]], 1
    return None, 0


def _rejected_unit(tokens: Sequence[str], index: int) -> str | None:
    if index < len(tokens) and tokens[index] in _UNSUPPORTED_UNITS:
        return tokens[index]
    return None


# --------------------------------------------------------------------------
# enquiry extraction
# --------------------------------------------------------------------------

# Ordered by strength: an explicit "to" destination beats a bare "for".
_DESTINATION_MARKERS: list[tuple[str, ...]] = [
    ("for", "delivery", "to"),
    ("for", "shipment", "to"),
    ("for", "export", "to"),
    ("delivered", "to"),
    ("shipped", "to"),
    ("shipping", "to"),
    ("destined", "for"),
    ("bound", "for"),
    ("export", "to"),
    ("deliver", "to"),
    ("ship", "to"),
    ("send", "to"),
    ("to",),
    ("for",),
]
_STRONG_MARKERS = {marker for marker in _DESTINATION_MARKERS if marker[-1] == "to"}

# Connectives that belong to a destination phrase, never to a product name.
# "50 tonnes of rice for export to Dubai" must yield "Rice", not "Rice For
# Export", once the destination marker has been chosen.
_PRODUCT_TRAILING_NOISE = {
    "for", "to", "of", "delivery", "shipment", "export", "exports",
    "delivered", "shipped", "shipping", "destined", "bound", "deliver",
    "ship", "send", "sending", "dispatch", "dispatched", "consignment",
}


def _strip_product_noise(tokens: Sequence[str]) -> list[str]:
    result = list(tokens)
    while result and result[-1] in _PRODUCT_TRAILING_NOISE:
        result.pop()
    return result


@dataclass(frozen=True)
class EnquiryDraft:
    product: str
    quantity: int
    unit: str
    destination: str

    def as_tool_arguments(self) -> dict[str, object]:
        return {
            "product": self.product,
            "quantity": self.quantity,
            "unit": self.unit,
            "destination": self.destination,
            "distributor_id": None,
        }


@dataclass(frozen=True)
class EnquiryExtraction:
    """Either a complete draft, or a precise reason it is not complete."""

    draft: EnquiryDraft | None = None
    missing: tuple[str, ...] = ()
    unsupported_unit: str | None = None
    partial: dict[str, object] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.draft is not None


def _find_destination(tokens: Sequence[str]) -> tuple[int, int] | None:
    """Locate the destination marker, preferring the last strong ("to") one."""
    hits: list[tuple[int, int, bool]] = []
    for index in range(len(tokens)):
        for marker in _DESTINATION_MARKERS:
            if tuple(tokens[index : index + len(marker)]) == marker:
                hits.append((index, len(marker), marker in _STRONG_MARKERS))
                break
    if not hits:
        return None
    strong = [hit for hit in hits if hit[2]]
    chosen = (strong or hits)[-1]
    return chosen[0], chosen[1]


def extract_enquiry(text: str) -> EnquiryExtraction:
    """Pull a purchase enquiry out of free text.

    Unlike an anchored regex this tolerates any lead-in ("we need", "I would
    like", "could you order", or nothing at all), spelled-out quantities, unit
    synonyms, intervening clauses and trailing politeness.
    """
    tokens = tokenise(text)
    if not tokens:
        return EnquiryExtraction(missing=("quantity", "unit", "product", "destination"))

    for index in range(len(tokens)):
        quantity, quantity_length = parse_number(tokens, index)
        if quantity is None:
            continue

        unit_index = index + quantity_length
        unit, unit_length = resolve_unit(tokens, unit_index)
        if unit is None:
            rejected = _rejected_unit(tokens, unit_index)
            if rejected is not None:
                return EnquiryExtraction(
                    unsupported_unit=rejected,
                    partial={"quantity": quantity},
                )
            continue

        rest = list(tokens[unit_index + unit_length :])
        if rest and rest[0] == "of":
            rest.pop(0)

        destination_hit = _find_destination(rest)
        if destination_hit is None:
            product_tokens = _strip_filler(rest)
            partial: dict[str, object] = {"quantity": quantity, "unit": unit}
            if product_tokens:
                partial["product"] = titlecase(product_tokens)
                return EnquiryExtraction(missing=("destination",), partial=partial)
            return EnquiryExtraction(missing=("product", "destination"), partial=partial)

        marker_index, marker_length = destination_hit
        product_tokens = _strip_product_noise(_strip_filler(rest[:marker_index]))
        destination_tokens = _strip_filler(rest[marker_index + marker_length :])

        partial = {"quantity": quantity, "unit": unit}
        if product_tokens:
            partial["product"] = titlecase(product_tokens)
        if destination_tokens:
            partial["destination"] = titlecase(destination_tokens)

        missing = tuple(
            name
            for name, value in (("product", product_tokens), ("destination", destination_tokens))
            if not value
        )
        if missing:
            return EnquiryExtraction(missing=missing, partial=partial)

        return EnquiryExtraction(
            draft=EnquiryDraft(
                product=titlecase(product_tokens),
                quantity=quantity,
                unit=unit,
                destination=titlecase(destination_tokens),
            )
        )

    return EnquiryExtraction(missing=("quantity", "unit"))


# --------------------------------------------------------------------------
# intent classification
# --------------------------------------------------------------------------


class Intent(str, Enum):
    CREATE_ENQUIRY = "create_enquiry"
    CHECK_ENQUIRY = "check_enquiry"
    SEARCH_DISTRIBUTORS = "search_distributors"
    DESCRIBE_SELECTED = "describe_selected"
    HUMAN_SUPPORT = "human_support"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    GREETING = "greeting"
    HELP = "help"
    UNKNOWN = "unknown"


ENQUIRY_ID = re.compile(r"\bENQ[-\s]?(\d{3,})\b", re.IGNORECASE)

_CONFIRM_TOKENS = {"confirm", "confirmed", "yes", "yep", "yeah", "yup", "correct", "proceed", "sure"}
_CONFIRM_PHRASES = {"go ahead", "do it", "that is right", "sounds good", "place it", "yes please"}
_CANCEL_TOKENS = {"cancel", "no", "nope", "stop", "abort", "scrap"}
_CANCEL_PHRASES = {"forget it", "never mind", "nevermind", "do not", "leave it", "no thanks"}

_SEARCH_TOKENS = {"find", "search", "looking", "look", "source", "sourcing", "recommend", "suggest"}
_SUPPLIER_NOUNS = {"supplier", "suppliers", "distributor", "distributors", "vendor", "vendors", "exporter", "exporters", "seller", "sellers", "manufacturer", "manufacturers"}

_HUMAN_TOKENS = {"human", "person", "agent", "representative", "rep", "someone", "somebody", "advisor", "adviser", "manager", "staff", "operator"}
_HUMAN_PHRASES = {"speak to", "talk to", "put me through", "customer service", "customer support", "real person", "escalate this", "escalate to"}

_ORDER_VERBS = {"need", "want", "order", "buy", "purchase", "procure", "require", "book", "requisition"}

_SELECTED_PHRASES = {"this distributor", "this supplier", "this company", "this vendor", "this one", "about them", "about this"}

_GREETING_TOKENS = {"hi", "hello", "hey", "namaste", "greetings"}
_HELP_PHRASES = {"what can you do", "how does this work", "what do you do", "your options"}
_HELP_TOKENS = {"help"}

_STATUS_PHRASES = {"status of", "what is happening", "what happened", "any update", "update on", "chase up"}


@dataclass(frozen=True)
class Classification:
    intent: Intent
    confidence: float
    enquiry_id: str | None = None


def _phrase_hits(text: str, phrases: Iterable[str]) -> int:
    return sum(1 for phrase in phrases if phrase in text)


def classify(text: str, *, has_pending_action: bool = False, has_selection: bool = False) -> Classification:
    """Score every intent against weighted evidence and return the strongest."""
    normalised = normalise(text)
    tokens = set(tokenise(text))
    token_count = len(tokenise(text))
    if not tokens:
        return Classification(Intent.UNKNOWN, 0.0)

    # Short affirmations and refusals are only decisive when they are the whole
    # utterance -- "yes, I need 50 tonnes of rice" is an enquiry, not a confirm.
    terse = token_count <= 4
    if terse and (tokens & _CONFIRM_TOKENS or _phrase_hits(normalised, _CONFIRM_PHRASES)):
        return Classification(Intent.CONFIRM, 0.95 if has_pending_action else 0.6)
    if terse and (tokens & _CANCEL_TOKENS or _phrase_hits(normalised, _CANCEL_PHRASES)):
        return Classification(Intent.CANCEL, 0.95 if has_pending_action else 0.6)

    scores: dict[Intent, float] = {intent: 0.0 for intent in Intent}

    identifier = ENQUIRY_ID.search(text)
    if identifier is not None:
        scores[Intent.CHECK_ENQUIRY] += 3.0
    scores[Intent.CHECK_ENQUIRY] += 1.2 * _phrase_hits(normalised, _STATUS_PHRASES)
    if "enquiry" in tokens or "enquiries" in tokens or "order" in tokens:
        scores[Intent.CHECK_ENQUIRY] += 0.4

    scores[Intent.HUMAN_SUPPORT] += 1.6 * len(tokens & _HUMAN_TOKENS)
    scores[Intent.HUMAN_SUPPORT] += 1.6 * _phrase_hits(normalised, _HUMAN_PHRASES)

    scores[Intent.SEARCH_DISTRIBUTORS] += 1.3 * len(tokens & _SEARCH_TOKENS)
    scores[Intent.SEARCH_DISTRIBUTORS] += 1.5 * len(tokens & _SUPPLIER_NOUNS)
    if "who" in tokens and ("supplies" in tokens or "sells" in tokens):
        scores[Intent.SEARCH_DISTRIBUTORS] += 2.0

    extraction = extract_enquiry(text)
    if extraction.complete:
        scores[Intent.CREATE_ENQUIRY] += 3.2
    elif extraction.unsupported_unit is not None:
        scores[Intent.CREATE_ENQUIRY] += 2.4
    elif extraction.partial.get("quantity") is not None:
        scores[Intent.CREATE_ENQUIRY] += 1.6
    scores[Intent.CREATE_ENQUIRY] += 0.8 * len(tokens & _ORDER_VERBS)

    # An explicit supplier hunt outranks an incidental quantity.
    if tokens & _SUPPLIER_NOUNS and tokens & _SEARCH_TOKENS:
        scores[Intent.SEARCH_DISTRIBUTORS] += 1.5

    selected_hits = _phrase_hits(normalised, _SELECTED_PHRASES)
    scores[Intent.DESCRIBE_SELECTED] += 2.2 * selected_hits
    if has_selection and selected_hits:
        scores[Intent.DESCRIBE_SELECTED] += 1.0

    if tokens & _GREETING_TOKENS and token_count <= 3:
        scores[Intent.GREETING] += 2.0
    scores[Intent.HELP] += 2.0 * _phrase_hits(normalised, _HELP_PHRASES)
    if tokens & _HELP_TOKENS and token_count <= 3:
        scores[Intent.HELP] += 1.5

    best = max(scores, key=lambda intent: scores[intent])
    if scores[best] <= 0.0:
        return Classification(Intent.UNKNOWN, 0.0)

    enquiry_id = f"ENQ-{identifier.group(1)}" if identifier is not None else None
    confidence = min(scores[best] / 4.0, 1.0)
    return Classification(best, round(confidence, 3), enquiry_id)


# --------------------------------------------------------------------------
# search slots, resolved against real workspace vocabulary
# --------------------------------------------------------------------------

_LOCATION_MARKERS = ("in", "from", "near", "around", "based in", "located in")


@dataclass(frozen=True)
class SearchSlots:
    product: str = ""
    location: str = ""


def extract_search_slots(
    text: str,
    *,
    products: Iterable[str] = (),
    locations: Iterable[str] = (),
) -> SearchSlots:
    """Resolve product and location against the caller's own workspace data.

    ``products`` and ``locations`` are the distinct distributor categories and
    locations visible to this tenant. Matching against them -- longest phrase
    first -- is what lets the engine handle any commodity the workspace
    actually carries rather than a hardcoded pair of demo nouns.
    """
    tokens = tokenise(text)
    normalised = " ".join(tokens)

    def longest_match(vocabulary: Iterable[str]) -> str:
        best = ""
        for entry in vocabulary:
            phrase = " ".join(tokenise(entry))
            if not phrase:
                continue
            if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalised) and len(phrase) > len(best):
                best = entry
        return best

    location = longest_match(locations)
    product = longest_match(products)

    if not location:
        for marker in _LOCATION_MARKERS:
            marker_tokens = marker.split()
            for index in range(len(tokens) - len(marker_tokens)):
                if tokens[index : index + len(marker_tokens)] == marker_tokens:
                    tail = _strip_filler(tokens[index + len(marker_tokens) :])
                    tail = [token for token in tail if token not in _SUPPLIER_NOUNS]
                    if tail:
                        location = titlecase(tail[:3])
                        break
            if location:
                break

    if not product:
        # Fall back to the noun phrase before a supplier noun: "basmati rice
        # supplier" -> "basmati rice".
        for index, token in enumerate(tokens):
            if token in _SUPPLIER_NOUNS and index:
                candidate = _strip_filler(
                    [
                        item
                        for item in tokens[:index]
                        if item not in _SEARCH_TOKENS and item not in _ORDER_VERBS
                    ]
                )
                if candidate:
                    product = titlecase(candidate[-3:])
                break

    return SearchSlots(product=product, location=location)
