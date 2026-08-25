from __future__ import annotations

import pytest

from app.voice import nlu
from app.voice.nlu import Intent

# Workspace vocabulary stands in for the seeded tenant catalogue.
PRODUCTS = ["Basmati Rice", "Rice", "Wheat", "Pulses", "Spices", "Tea", "Coffee", "Soybean"]
LOCATIONS = ["Punjab", "West Bengal", "Gujarat", "Kerala", "Madhya Pradesh"]


# ---------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    "text,expected",
    [
        ("50", 50),
        ("1,000", 1000),
        ("fifty", 50),
        ("fifteen", 15),
        ("two hundred", 200),
        ("a hundred and fifty", 150),
        ("twelve thousand", 12000),
        ("three lakh", 300000),
        ("twenty five", 25),
    ],
)
def test_number_words_and_digit_groups_parse(text, expected):
    value, _ = nlu.parse_number(nlu.tokenise(text))
    assert value == expected


def test_and_does_not_bridge_into_a_product_list():
    """ "rice and wheat" must not be read as a continuing number."""
    tokens = nlu.tokenise("two rice and wheat")
    value, consumed = nlu.parse_number(tokens)
    assert (value, consumed) == (2, 1)


# ------------------------------------------------------------------ units


@pytest.mark.parametrize(
    "phrase,canonical",
    [
        ("tonnes", "tonnes"),
        ("tonne", "tonnes"),
        ("tons", "tonnes"),
        ("mt", "tonnes"),
        ("metric tonnes", "tonnes"),
        ("kg", "kg"),
        ("kilos", "kg"),
        ("kilograms", "kg"),
        ("pcs", "units"),
        ("pieces", "units"),
    ],
)
def test_unit_synonyms_resolve_to_schema_units(phrase, canonical):
    unit, _ = nlu.resolve_unit(nlu.tokenise(phrase), 0)
    assert unit == canonical


# -------------------------------------------------------------- extraction


@pytest.mark.parametrize(
    "text,quantity,unit,product,destination",
    [
        # None of these phrasings appear in the demo script.
        ("We need 50 tonnes of rice for Dubai", 50, "tonnes", "Rice", "Dubai"),
        ("I'd like 20 tonnes of wheat for Muscat", 20, "tonnes", "Wheat", "Muscat"),
        ("I need fifty tonnes of rice for Dubai", 50, "tonnes", "Rice", "Dubai"),
        ("two hundred kg of cardamom for Oman", 200, "kg", "Cardamom", "Oman"),
        ("Order 1,000 kg of turmeric to Abu Dhabi", 1000, "kg", "Turmeric", "Abu Dhabi"),
        ("50 MT of rice for Dubai", 50, "tonnes", "Rice", "Dubai"),
        ("50 metric tonnes of rice for Dubai", 50, "tonnes", "Rice", "Dubai"),
        ("book 20 tonnes of soybean for delivery to Jebel Ali", 20, "tonnes", "Soybean", "Jebel Ali"),
        ("50 tonnes of wheat flour to Oman", 50, "tonnes", "Wheat Flour", "Oman"),
        ("12 thousand kg of tea for shipment to Colombo", 12000, "kg", "Tea", "Colombo"),
    ],
)
def test_enquiry_extraction_handles_natural_phrasing(text, quantity, unit, product, destination):
    result = nlu.extract_enquiry(text)
    assert result.complete, f"failed to extract from {text!r}"
    assert (result.draft.quantity, result.draft.unit) == (quantity, unit)
    assert result.draft.product == product
    assert result.draft.destination == destination


def test_unicode_destinations_survive():
    result = nlu.extract_enquiry("I need 50 tonnes of rice for São Paulo")
    assert result.complete
    assert result.draft.destination == "São Paulo"


@pytest.mark.parametrize(
    "text,destination",
    [
        # Regression: an intervening clause used to be absorbed into the
        # destination, writing "Export To Dubai" to the database.
        ("I need 50 tonnes of rice for export to Dubai", "Dubai"),
        # Regression: trailing politeness used to become part of the address.
        ("I need 50 tonnes of basmati rice for Dubai please", "Dubai"),
        ("I need 50 tonnes of rice for Dubai, thanks", "Dubai"),
    ],
)
def test_destination_is_not_polluted_by_surrounding_words(text, destination):
    result = nlu.extract_enquiry(text)
    assert result.complete
    assert result.draft.destination == destination


def test_product_excludes_destination_connectives():
    result = nlu.extract_enquiry("I need 50 tonnes of rice for export to Dubai")
    assert result.draft.product == "Rice"


def test_missing_destination_is_reported_not_guessed():
    result = nlu.extract_enquiry("I need 50 tonnes of rice")
    assert not result.complete
    assert result.missing == ("destination",)
    assert result.partial == {"quantity": 50, "unit": "tonnes", "product": "Rice"}


def test_unsupported_unit_is_named_rather_than_silently_dropped():
    result = nlu.extract_enquiry("I need 30 bags of rice for Dubai")
    assert not result.complete
    assert result.unsupported_unit == "bags"


# ---------------------------------------------------------- classification


@pytest.mark.parametrize(
    "text,intent",
    [
        ("Tell me about this distributor.", Intent.DESCRIBE_SELECTED),
        ("Find me a basmati rice supplier in Punjab.", Intent.SEARCH_DISTRIBUTORS),
        ("Who supplies wheat in Gujarat?", Intent.SEARCH_DISTRIBUTORS),
        ("I need 50 tonnes of basmati rice for Dubai.", Intent.CREATE_ENQUIRY),
        ("We would like to order 20 tonnes of pulses to Muscat", Intent.CREATE_ENQUIRY),
        ("What's happening with ENQ-1001?", Intent.CHECK_ENQUIRY),
        ("any update on ENQ 1003", Intent.CHECK_ENQUIRY),
        ("I want to speak to someone.", Intent.HUMAN_SUPPORT),
        ("put me through to a real person", Intent.HUMAN_SUPPORT),
        ("hello", Intent.GREETING),
        ("what can you do", Intent.HELP),
    ],
)
def test_intent_classification(text, intent):
    assert nlu.classify(text).intent is intent


@pytest.mark.parametrize(
    "text",
    [
        # Regression: substring matching fired HUMAN_SUPPORT on "person"
        # inside "personal" and on "agent" inside longer words.
        "Do you have personal protective equipment?",
        "Send me the personnel policy",
    ],
)
def test_substrings_do_not_trigger_human_handoff(text):
    assert nlu.classify(text).intent is not Intent.HUMAN_SUPPORT


def test_affirmation_inside_a_real_request_is_not_a_bare_confirm():
    result = nlu.classify("yes, I need 50 tonnes of rice for Dubai", has_pending_action=True)
    assert result.intent is Intent.CREATE_ENQUIRY


def test_terse_confirmation_is_recognised():
    assert nlu.classify("confirm", has_pending_action=True).intent is Intent.CONFIRM
    assert nlu.classify("go ahead", has_pending_action=True).intent is Intent.CONFIRM
    assert nlu.classify("cancel", has_pending_action=True).intent is Intent.CANCEL


def test_incidental_verb_does_not_reach_actionable_confidence():
    """ "find" alone should not fire a distributor search."""
    result = nlu.classify("I can't find my invoice")
    assert result.confidence < 0.4


def test_unrecognised_input_is_unknown():
    assert nlu.classify("blorp zorp").intent is Intent.UNKNOWN


# --------------------------------------------------------------- search


@pytest.mark.parametrize(
    "text,product,location",
    [
        ("Find me a basmati rice supplier in Punjab", "Basmati Rice", "Punjab"),
        ("who sells tea in Kerala", "Tea", "Kerala"),
        ("I am looking for pulses distributors in Madhya Pradesh", "Pulses", "Madhya Pradesh"),
        ("find a wheat exporter", "Wheat", ""),
    ],
)
def test_search_slots_resolve_against_workspace_vocabulary(text, product, location):
    slots = nlu.extract_search_slots(text, products=PRODUCTS, locations=LOCATIONS)
    assert slots.product == product
    assert slots.location == location


def test_search_is_not_limited_to_the_demo_vocabulary():
    """A commodity the old engine could not represent at all."""
    slots = nlu.extract_search_slots(
        "find me a saffron supplier in Jammu",
        products=["Saffron", "Rice"],
        locations=["Jammu", "Punjab"],
    )
    assert slots.product == "Saffron"
    assert slots.location == "Jammu"


def test_unknown_product_falls_back_to_the_phrase_before_the_supplier_noun():
    slots = nlu.extract_search_slots("find me a dragon fruit supplier", products=PRODUCTS, locations=LOCATIONS)
    assert slots.product == "Dragon Fruit"
