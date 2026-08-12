"""
This component provides switch support for Buspro.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.switch import SwitchEntity, PLATFORM_SCHEMA
from homeassistant.const import (CONF_NAME, CONF_DEVICES)
from homeassistant.core import callback

from ..buspro import DATA_BUSPRO

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_RELAY = "relay"
DEVICE_TYPE_PANEL_AC = "panel_ac"
DEVICE_TYPES = (DEVICE_TYPE_RELAY, DEVICE_TYPE_PANEL_AC)


def _validate_device_address(value):
    """Validate a subnet.device.channel switch address."""
    if not isinstance(value, str):
        raise vol.Invalid("address must be subnet.device.channel")

    parts = value.split(".")
    if len(parts) != 3:
        raise vol.Invalid("address must be subnet.device.channel")

    try:
        subnet, device, channel = (int(part) for part in parts)
    except ValueError as error:
        raise vol.Invalid("address parts must be integers") from error

    if not 0 <= subnet <= 255 or not 0 <= device <= 255:
        raise vol.Invalid("subnet and device must be bytes")
    if not 1 <= channel <= 255:
        raise vol.Invalid("channel must be between 1 and 255")
    return value


DEVICE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(
        CONF_DEVICE_TYPE, default=DEVICE_TYPE_RELAY
    ): vol.In(DEVICE_TYPES),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES): {_validate_device_address: DEVICE_SCHEMA},
})


# noinspection PyUnusedLocal
async def async_setup_platform(hass, config, async_add_entites, discovery_info=None):
    """Set up Buspro switch devices."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices import Switch

    hdl = hass.data[DATA_BUSPRO].hdl
    devices = []

    for address, device_config in config[CONF_DEVICES].items():
        name = device_config[CONF_NAME]

        address2 = address.split('.')
        device_address = (int(address2[0]), int(address2[1]))
        channel_number = int(address2[2])
        _LOGGER.debug("Adding switch '{}' with address {} and channel number {}".format(name, device_address, channel_number))

        if device_config[CONF_DEVICE_TYPE] == DEVICE_TYPE_PANEL_AC:
            from .pybuspro.devices.panel_ac import PanelACDevice

            device = PanelACDevice(
                hdl, device_address, channel_number, name
            )
            entity = BusproPanelACSwitch(hass, device)
        else:
            device = Switch(hdl, device_address, channel_number, name)
            entity = BusproSwitch(hass, device)

        devices.append(entity)

    async_add_entites(devices)


# noinspection PyAbstractClass
class BusproSwitch(SwitchEntity):
    """Representation of a Buspro switch."""

    def __init__(self, hass, device):
        self._hass = hass
        self._device = device
        self.async_register_callbacks()

    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

    @property
    def should_poll(self):
        """No polling needed within Buspro."""
        return False

    @property
    def name(self):
        """Return the display name of this light."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        return self._hass.data[DATA_BUSPRO].connected

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._device.is_on

    async def async_turn_on(self, **kwargs):
        """Instruct the switch to turn on."""
        await self._device.set_on()

    async def async_turn_off(self, **kwargs):
        """Instruct the switch to turn off."""
        await self._device.set_off()

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier


class BusproPanelACSwitch(BusproSwitch):
    """Enviro panel AC power switch backed by confirmed panel state."""