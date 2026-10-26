"""Config flow."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.linznetz.api import LinzNetzAuthError
from custom_components.linznetz.const import (
    CONF_FIXED_COST_ENTITY,
    CONF_PRICE_ENTITY,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

FETCH = "custom_components.linznetz.config_flow.LinzNetzClient.async_fetch_csv"
SETUP = "custom_components.linznetz.async_setup_entry"


async def test_wrong_password_then_success(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login = {"username": " verein@example.at ", "password": "pw"}

    with patch(FETCH, side_effect=LinzNetzAuthError("login-rejected", "nope")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], login)
    assert result["errors"] == {"base": "invalid_auth"}

    costs = {
        CONF_PRICE_ENTITY: "input_number.price",
        CONF_FIXED_COST_ENTITY: "input_number.fixed",
    }
    with patch(FETCH, return_value=""), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**login, **costs}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"username": "verein@example.at", "password": "pw"}
    assert result["options"] == costs


async def test_options_set_cost_entities(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "verein", "password": "pw"},
        options={CONF_PRICE_ENTITY: "input_number.price"},
    )
    entry.add_to_hass(hass)
    costs = {
        CONF_PRICE_ENTITY: "input_number.price",
        CONF_FIXED_COST_ENTITY: "input_number.fixed",
    }

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert set(result["data_schema"].schema) == set(costs)
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_configure(result["flow_id"], costs)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == costs
