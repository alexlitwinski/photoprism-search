"""Config flow for PhotoPrism AI Search integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client

from .const import (
    CONF_GEMINI_KEY,
    CONF_GEMINI_MODEL,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    DEFAULT_GEMINI_MODEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="http://db21ed7f-photoprism:2342"): str,
        vol.Optional(CONF_USERNAME, default="admin"): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
        vol.Required(CONF_GEMINI_KEY): str,
        vol.Optional(CONF_GEMINI_MODEL, default=DEFAULT_GEMINI_MODEL): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input by checking connection to PhotoPrism and Gemini."""
    url = data[CONF_URL].rstrip("/")
    username = data.get(CONF_USERNAME, "")
    password = data.get(CONF_PASSWORD, "")
    gemini_key = data[CONF_GEMINI_KEY]

    session = aiohttp_client.async_get_clientsession(hass)

    # 1. Test PhotoPrism connection & login
    # Try public session check first, then login if credentials provided
    try:
        if username and password:
            async with session.post(
                f"{url}/api/v1/session",
                json={"username": username, "password": password},
                ssl=False,
                timeout=10,
            ) as resp:
                if resp.status not in (200, 201):
                    _LOGGER.error("PhotoPrism authentication failed with status: %s", resp.status)
                    raise InvalidAuth
        else:
            async with session.post(
                f"{url}/api/v1/session",
                json={},
                ssl=False,
                timeout=10,
            ) as resp:
                if resp.status not in (200, 201):
                    # Fallback test of just reading base endpoint
                    async with session.get(url, ssl=False, timeout=10) as r:
                        r.raise_for_status()
    except Exception as exc:
        _LOGGER.error("Failed to connect to PhotoPrism: %s", exc)
        raise CannotConnect from exc

    # 2. Test Gemini Connection
    model = data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL)
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
    try:
        async with session.post(
            gemini_url,
            json={
                "contents": [
                    {"parts": [{"text": "Hello, output exactly the JSON: {\"test\": true}"}]}
                ],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=10,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                _LOGGER.error("Gemini validation failed: status %s, body %s", resp.status, body)
                raise InvalidGeminiKey
    except Exception as exc:
        if isinstance(exc, InvalidGeminiKey):
            raise
        _LOGGER.error("Failed to connect to Gemini API: %s", exc)
        raise CannotConnectGemini from exc

    return {"title": f"PhotoPrism ({url})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PhotoPrism AI Search."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(title=info["title"], data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect_photoprism"
            except InvalidAuth:
                errors["base"] = "invalid_auth_photoprism"
            except InvalidGeminiKey:
                errors["base"] = "invalid_gemini_key"
            except CannotConnectGemini:
                errors["base"] = "cannot_connect_gemini"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect to PhotoPrism."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth for PhotoPrism."""


class InvalidGeminiKey(Exception):
    """Error to indicate the Gemini Key is invalid."""


class CannotConnectGemini(Exception):
    """Error to indicate we cannot connect to Gemini."""
