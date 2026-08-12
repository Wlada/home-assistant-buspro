"""
This component provides sensor support for Buspro.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging
from datetime import timedelta


import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_NAME,
    CONF_DEVICES,
    CONF_ADDRESS,
    CONF_TYPE,
    CONF_UNIT_OF_MEASUREMENT,
    ILLUMINANCE,
    TEMPERATURE,
    CONF_DEVICE_CLASS,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from ..buspro import DATA_BUSPRO


DEFAULT_CONF_UNIT_OF_MEASUREMENT = ""
DEFAULT_CONF_DEVICE_CLASS = "None"
DEFAULT_CONF_SCAN_INTERVAL = 0
DEFAULT_CONF_OFFSET = 0
CONF_DEVICE = "device"
CONF_OFFSET = "offset"
HUMIDITY = "humidity"
SCAN_INTERVAL = timedelta(minutes=2)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = {
    HUMIDITY,
    ILLUMINANCE,
    TEMPERATURE,
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES):
        vol.All(cv.ensure_list, [
            vol.All({
                vol.Required(CONF_ADDRESS): cv.string,
                vol.Required(CONF_NAME): cv.string,
                vol.Required(CONF_TYPE): vol.In(SENSOR_TYPES),
                vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=DEFAULT_CONF_UNIT_OF_MEASUREMENT): cv.string,
                vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_CONF_DEVICE_CLASS): cv.string,
                vol.Optional(CONF_DEVICE, default=""): cv.string,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_CONF_SCAN_INTERVAL): cv.string,
                vol.Optional(CONF_OFFSET, default=DEFAULT_CONF_OFFSET): cv.string,
            })
        ])
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up Buspro sensor devices."""
    from .pybuspro.devices.sensor import Sensor

    hdl = hass.data[DATA_BUSPRO].hdl
    devices = []

    for device_config in config[CONF_DEVICES]:
        address = device_config[CONF_ADDRESS]
        name = device_config[CONF_NAME]
        sensor_type = device_config[CONF_TYPE]
        device = device_config[CONF_DEVICE]
        offset = device_config[CONF_OFFSET]

        scan_interval = device_config[CONF_SCAN_INTERVAL]
        interval = 0
        if scan_interval is not None:
            interval = int(scan_interval)

        address_parts = address.split('.')
        subnet_id = int(address_parts[0])
        device_id = int(address_parts[1])
        channel_number = int(address_parts[2]) if len(address_parts) > 2 else None
        device_address = (subnet_id, device_id)

        _LOGGER.debug("Adding sensor '%s' with address %s, sensor type '%s'", name, device_address, sensor_type)

        sensor = Sensor(
            hdl,
            device_address,
            device=device,
            name=name,
            channel_number=channel_number,
            diagnostic_role=sensor_type,
        )

        devices.append(BusproSensor(sensor, sensor_type, interval, offset))

    async_add_entities(devices)


class BusproSensor(Entity):
    """Representation of a Buspro sensor."""

    def __init__(self, device, sensor_type, scan_interval, offset):
        self._device = device
        self._sensor_type = sensor_type
        self._offset = int(offset) if offset else 0
        self._temperature = None
        self._brightness = None
        self._humidity = None
        self._should_poll = scan_interval > 0

    async def async_added_to_hass(self):
        """Run when entity is about to be added to hass."""
        await self.async_register_callbacks()
        await self.async_read_status()

    async def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""
        self._device.register_device_updated_cb(self.after_update_callback)
        

    async def after_update_callback(self, device):
        """Call after device was updated."""
        self._temperature = self._device.temperature
        self._brightness = self._device.brightness
        self._humidity = self._device.humidity
        _LOGGER.debug("Entity %s received update: Temperature = %s", self.name, self._temperature)

        self.async_write_ha_state()

    @property
    def should_poll(self):
        """Return whether polling is needed."""
        return self._should_poll

    async def async_update(self):
        """Update the sensor's state."""
        await self._device.read_sensor_status()

    @property
    def name(self):
        """Return the display name of this sensor."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        connected = self.hass.data[DATA_BUSPRO].connected

        if self._sensor_type == TEMPERATURE:
            return connected and self._current_temperature is not None

        if self._sensor_type == ILLUMINANCE:
            return connected and self._brightness is not None

        if self._sensor_type == HUMIDITY:
            return connected and self._humidity is not None

        return connected

    @property
    def state(self):
        """Return the state of the sensor."""
        if self._sensor_type == TEMPERATURE:
            return self._current_temperature

        if self._sensor_type == ILLUMINANCE:
            return self._brightness

        if self._sensor_type == HUMIDITY:
            return self._humidity

    @property
    def _current_temperature(self):
        if self._temperature is None:
            return None

        temperature = self._temperature
        if temperature != 0:
            temperature += self._offset

        return temperature

    @property
    def device_class(self):
        """Return the class of this sensor."""
        if self._sensor_type == TEMPERATURE:
            return "temperature"
        if self._sensor_type == ILLUMINANCE:
            return "illuminance"
        if self._sensor_type == HUMIDITY:
            return "humidity"
        return None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        if self._sensor_type == TEMPERATURE:
            return "°C"
        if self._sensor_type == ILLUMINANCE:
            return "lux"
        if self._sensor_type == HUMIDITY:
            return "%"
        return ""

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            'state_class': "measurement"
        }

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self._device.device_identifier}-{self._sensor_type}"

    async def async_read_status(self):
        """Read the status of the device."""
        await self._device.read_sensor_status()
        self.async_write_ha_state()
