"""Setup, re-authentication and price option for Linz Netz."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from aiohttp import ClientSession
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import LinzNetzAuthError, LinzNetzClient, LinzNetzError
from .const import CONF_PRICE_ENTITY, DOMAIN

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_PRICE = EntitySelector(EntitySelectorConfig(domain=["input_number", "sensor"]))


async def _async_check_login(hass: HomeAssistant, username: str, password: str) -> str | None:
    """Fetch one day to prove the login works; returns an error key or None."""
    today = dt_util.now().date()
    # Throwaway session so a failed attempt leaves no portal cookies behind.
    async with ClientSession() as session:
        try:
            await LinzNetzClient(session, username, password).async_fetch_csv(
                today - timedelta(days=1), today
            )
        except LinzNetzAuthError:
            return "invalid_auth"
        except LinzNetzError:
            return "cannot_connect"
    return None


class LinzNetzConfigFlow(ConfigFlow, domain=DOMAIN):
    """Asks for the Serviceportal login and an optional price entity."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            error = await _async_check_login(self.hass, username, user_input[CONF_PASSWORD])
            if error is None:
                price = user_input.get(CONF_PRICE_ENTITY)
                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    options={CONF_PRICE_ENTITY: price} if price else {},
                )
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
                vol.Optional(CONF_PRICE_ENTITY): _PRICE,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = entry.data[CONF_USERNAME]
            error = await _async_check_login(self.hass, username, user_input[CONF_PASSWORD])
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LinzNetzOptionsFlow:
        return LinzNetzOptionsFlow()


class LinzNetzOptionsFlow(OptionsFlowWithReload):
    """Changes the price entity used for the cost statistic."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema({vol.Optional(CONF_PRICE_ENTITY): _PRICE})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
