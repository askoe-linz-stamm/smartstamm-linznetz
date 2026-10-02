"""Render the shipped Markdown card with Home Assistant's template engine."""

from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
import pytest
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template


CARD_PATH = Path(__file__).parents[1] / "docs" / "dashboard-status-card.yaml"


@pytest.mark.parametrize(
    ("status", "until", "expected"),
    [
        ("ok", "2026-10-01T22:00:00+00:00", "Für heute liegen noch keine Werte vor."),
        (
            "ok",
            "2026-10-01T23:00:00+00:00",
            "Für heute wurden bereits Verbrauchswerte importiert.",
        ),
        ("ok", "unknown", "Noch keine Verbrauchsdaten importiert."),
        (
            "connection_error",
            "2026-10-01T22:00:00+00:00",
            "Der letzte Abruf ist fehlgeschlagen.",
        ),
        ("portal_changed", "unknown", "Der letzte Abruf ist fehlgeschlagen."),
        ("login_rejected", "unknown", "Die Anmeldung bei Linz Netz ist fehlgeschlagen."),
        ("unavailable", "unknown", "Der Abrufstatus ist derzeit nicht verfügbar."),
    ],
)
async def test_card_messages_and_local_times(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    status: str,
    until: str,
    expected: str,
) -> None:
    await hass.config.async_set_time_zone("Europe/Vienna")
    freezer.move_to("2026-10-02 08:41:00+02:00")
    card = yaml.safe_load(CARD_PATH.read_text())
    entities = card["entity_id"]
    # Use custom IDs to verify that changing the single list is sufficient.
    card["entity_id"] = [
        entity.replace("linz_netz_stromzahler", "mein_zaehler") for entity in entities
    ]
    for entity, value in zip(
        card["entity_id"],
        [until, "2026-10-02T04:00:00+00:00", "2026-10-02T10:00:00+00:00", status],
        strict=True,
    ):
        hass.states.async_set(entity, value)

    rendered = Template(card["content"], hass).async_render({"config": card})
    assert expected in rendered
    assert "02.10.2026, 06:00 Uhr" in rendered
    assert "02.10.2026, 12:00 Uhr" in rendered
    assert "[Energiedashboard öffnen](/energy)" in rendered
    assert "keine Live-Daten" in rendered
    if status != "ok":
        assert "Für heute liegen noch keine Werte vor." not in rendered
        assert ("zuletzt erfolgreich importierten Daten" in rendered) == (
            until != "unknown"
        )


async def test_card_unknown_times_and_midnight_rollover(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Vienna")
    freezer.move_to("2026-10-01 23:59:00+02:00")
    card = yaml.safe_load(CARD_PATH.read_text())
    for entity, value in zip(
        card["entity_id"],
        ["2026-10-01T22:00:00+00:00", "unknown", "unknown", "ok"],
        strict=True,
    ):
        hass.states.async_set(entity, value)
    template = Template(card["content"], hass)
    rendered = template.async_render({"config": card})
    assert "Für heute wurden bereits Verbrauchswerte importiert." in rendered
    assert "Noch nicht verfügbar" in rendered
    assert "Derzeit kein Abruf geplant" in rendered

    freezer.move_to("2026-10-02 00:00:00+02:00")
    rendered = template.async_render({"config": card})
    assert "Für heute liegen noch keine Werte vor." in rendered
