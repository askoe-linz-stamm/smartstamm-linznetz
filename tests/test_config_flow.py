"""Config flow."""

from unittest.mock import patch

from custom_components.linznetz.api import LinzNetzAuthError
from custom_components.linznetz.const import CONF_PRICE_ENTITY, DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

FETCH = "custom_components.linznetz.config_flow.LinzNetzClient.async_fetch_csv"
SETUP = "custom_components.linznetz.async_setup_entry"


async def test_wrong_password_then_success(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login = {"username": " verein@example.at ", "password": "pw"}

    with patch(FETCH, side_effect=LinzNetzAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], login)
    assert result["errors"] == {"base": "invalid_auth"}

    with patch(FETCH, return_value=""), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**login, CONF_PRICE_ENTITY: "input_number.price"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"username": "verein@example.at", "password": "pw"}
    assert result["options"] == {CONF_PRICE_ENTITY: "input_number.price"}
