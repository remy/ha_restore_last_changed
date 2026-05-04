"""Config schema for the Restore Last Changed integration."""

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import CONF_ENTITIES, CONF_GROUPS, DOMAIN

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_GROUPS, default=list): vol.All(
                    cv.ensure_list, [cv.entity_id]
                ),
                vol.Optional(CONF_ENTITIES, default=list): vol.All(
                    cv.ensure_list, [cv.entity_id]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)
