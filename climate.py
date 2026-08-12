"""
This component provides sensor support for Buspro.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging
from typing import Optional, List

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.climate import (
    PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_DEVICES,
    CONF_ADDRESS,
    UnitOfTemperature,
    ATTR_TEMPERATURE,
)
from homeassistant.core import callback

# from homeassistant.helpers.entity import Entity
from ..buspro import DATA_BUSPRO
# noinspection PyUnresolvedReferences
from .pybuspro.devices.climate import ControlFloorHeatingStatus
# noinspection PyUnresolvedReferences
from .pybuspro.helpers.enums import OnOffStatus
from .panel_ac_climate import BusproPanelACClimate
from .panel_floor_heating_climate import BusproPanelFloorHeatingClimate

_LOGGER = logging.getLogger(__name__)

PRESET_NONE = "none"
PRESET_AWAY = "away"
PRESET_HOME = "home"
PRESET_SLEEP = "sleep"

HA_PRESET_TO_HDL = {
    PRESET_NONE: 1,     # Normal
    PRESET_HOME: 2,     # Day
    PRESET_SLEEP: 3,    # Night
    PRESET_AWAY: 4,     # Away
}
HDL_TO_HA_PRESET = {
    1: PRESET_NONE,     # Normal
    2: PRESET_HOME,     # Day
    3: PRESET_SLEEP,    # Night
    4: PRESET_AWAY,     # Away
}

CONF_PRESET_MODES = "preset_modes"
CONF_RELAY_ADDRESS = "relay_address"
CONF_DEVICE_TYPE = "device_type"
CONF_TEMPERATURE_CHANNEL = "temperature_channel"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_PANEL_ADDRESS = "panel_address"
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_STATUS_ROUTE = "status_route"
DEVICE_TYPE_FLOOR_HEATING = "floor_heating"
DEVICE_TYPE_PANEL_AC = "panel_ac"
DEVICE_TYPE_PANEL_FLOOR_HEATING = "panel_floor_heating"
DEVICE_TYPES = (DEVICE_TYPE_FLOOR_HEATING, DEVICE_TYPE_PANEL_AC, DEVICE_TYPE_PANEL_FLOOR_HEATING)


def _validate_climate_device(config):
    """Validate panel AC-only configuration without changing floor heating."""
    if config[CONF_DEVICE_TYPE] != DEVICE_TYPE_PANEL_AC:
        if config[CONF_DEVICE_TYPE] != DEVICE_TYPE_PANEL_FLOOR_HEATING:
            return config

        if CONF_PANEL_ADDRESS not in config:
            raise vol.Invalid("panel floor heating address is required")
        status_route = config.get(CONF_STATUS_ROUTE)
        if status_route is not None and (
            type(status_route) is not int or not 0 <= status_route <= 255
        ):
            raise vol.Invalid("floor heating status route must be a byte")
        required = (CONF_MIN_TEMP, CONF_MAX_TEMP)
        if any(key not in config for key in required):
            raise vol.Invalid(
                "panel floor heating temperature limits are required"
            )
        min_temp = config[CONF_MIN_TEMP]
        max_temp = config[CONF_MAX_TEMP]
        if (
            type(min_temp) is not int
            or type(max_temp) is not int
            or min_temp >= max_temp
        ):
            raise vol.Invalid(
                "panel floor heating temperature limits are invalid"
            )
        for key, label in (
            (CONF_ADDRESS, "actuator"),
            (CONF_PANEL_ADDRESS, "panel floor heating"),
        ):
            parts = config[key].split(".")
            if len(parts) != 3:
                raise vol.Invalid(f"{label} address must be subnet.device.channel")
            try:
                subnet, device, channel = (int(part) for part in parts)
            except ValueError as error:
                raise vol.Invalid(f"{label} address parts must be integers") from error
            if not 0 <= subnet <= 255 or not 0 <= device <= 255:
                raise vol.Invalid(f"{label} subnet and device must be bytes")
            if not 1 <= channel <= 255:
                raise vol.Invalid(f"{label} channel must be between 1 and 255")
        return config

    parts = config[CONF_ADDRESS].split(".")
    if len(parts) != 3:
        raise vol.Invalid("panel AC address must be subnet.device.channel")
    try:
        subnet, device, channel = (int(part) for part in parts)
    except ValueError as error:
        raise vol.Invalid("panel AC address parts must be integers") from error
    if not 0 <= subnet <= 255 or not 0 <= device <= 255:
        raise vol.Invalid("panel AC subnet and device must be bytes")
    if not 1 <= channel <= 255:
        raise vol.Invalid("panel AC channel must be between 1 and 255")

    required = (CONF_TEMPERATURE_CHANNEL, CONF_MIN_TEMP, CONF_MAX_TEMP)
    if any(key not in config for key in required):
        raise vol.Invalid("panel AC temperature channel and limits are required")
    temperature_channel = config[CONF_TEMPERATURE_CHANNEL]
    min_temp = config[CONF_MIN_TEMP]
    max_temp = config[CONF_MAX_TEMP]
    if type(temperature_channel) is not int or not 1 <= temperature_channel <= 255:
        raise vol.Invalid("temperature channel must be between 1 and 255")
    if (
        type(min_temp) is not int
        or type(max_temp) is not int
        or min_temp >= max_temp
    ):
        raise vol.Invalid("panel AC temperature limits are invalid")
    return config


CLIMATE_DEVICE_SCHEMA = vol.All(
    {
        vol.Required(CONF_ADDRESS): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(
            CONF_DEVICE_TYPE, default=DEVICE_TYPE_FLOOR_HEATING
        ): vol.In(DEVICE_TYPES),
        vol.Optional(CONF_PRESET_MODES, default=[]): vol.All(
            cv.ensure_list, [vol.In(HA_PRESET_TO_HDL)]
        ),
        vol.Optional(CONF_RELAY_ADDRESS, default=""): cv.string,
        vol.Optional(CONF_TEMPERATURE_CHANNEL): object,
        vol.Optional(CONF_MIN_TEMP): object,
        vol.Optional(CONF_MAX_TEMP): object,
        vol.Optional(CONF_PANEL_ADDRESS): cv.string,
        vol.Optional(CONF_TEMPERATURE_ENTITY): cv.string,
        vol.Optional(CONF_STATUS_ROUTE): object,
    },
    _validate_climate_device,
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES):
        vol.All(cv.ensure_list, [CLIMATE_DEVICE_SCHEMA])
})


# noinspection PyUnusedLocal
async def async_setup_platform(hass, config, async_add_entites, discovery_info=None):
    """Set up BusPro floor-heating and panel AC climate devices."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices import Climate
    from .pybuspro.devices import Sensor

    hdl = hass.data[DATA_BUSPRO].hdl
    devices = []
    temperature_sensors = {}

    for device_config in config[CONF_DEVICES]:
        address = device_config[CONF_ADDRESS]
        name = device_config[CONF_NAME]
        preset_modes = device_config[CONF_PRESET_MODES]

        address_parts = address.split(".")
        if device_config[CONF_DEVICE_TYPE] == DEVICE_TYPE_PANEL_FLOOR_HEATING:
            from .pybuspro.devices.panel_floor_heating import (
                PanelFloorHeatingDevice,
            )

            panel_parts = device_config[CONF_PANEL_ADDRESS].split(".")
            panel_floor_heating = PanelFloorHeatingDevice(
                hdl,
                (int(panel_parts[0]), int(panel_parts[1])),
                int(panel_parts[2]),
                (int(address_parts[0]), int(address_parts[1])),
                int(address_parts[2]),
                name=name,
                status_route=device_config.get(CONF_STATUS_ROUTE),
                min_temp=device_config[CONF_MIN_TEMP],
                max_temp=device_config[CONF_MAX_TEMP],
            )
            devices.append(
                BusproPanelFloorHeatingClimate(
                    hass,
                    panel_floor_heating,
                    device_config.get(CONF_TEMPERATURE_ENTITY),
                )
            )
            continue
        if device_config[CONF_DEVICE_TYPE] == DEVICE_TYPE_PANEL_AC:
            from .pybuspro.devices.panel_ac import PanelACDevice

            device_address = (int(address_parts[0]), int(address_parts[1]))
            channel = int(address_parts[2])
            temperature_channel = device_config[CONF_TEMPERATURE_CHANNEL]
            sensor_key = (device_address, temperature_channel)
            temperature_sensor = temperature_sensors.get(sensor_key)
            if temperature_sensor is None:
                temperature_sensor = Sensor(
                    hdl,
                    device_address,
                    channel_number=temperature_channel,
                    device="temperature_channel",
                    name=f"{name} room temperature",
                )
                temperature_sensors[sensor_key] = temperature_sensor

            panel_ac = PanelACDevice(
                hdl,
                device_address,
                channel,
                name,
                min_temp=device_config[CONF_MIN_TEMP],
                max_temp=device_config[CONF_MAX_TEMP],
            )
            devices.append(
                BusproPanelACClimate(hass, panel_ac, temperature_sensor)
            )
            continue

        if len(address_parts) in (2, 3):
            device_address = tuple(int(part) for part in address_parts)
        else:
            _LOGGER.error(f"Invalid address format: {address}")
            continue

        _LOGGER.debug("Adding climate '%s' with address %s", name, device_address)

        climate = Climate(hdl, device_address, name)

        relay_sensor = None
        relay_address = device_config[CONF_RELAY_ADDRESS]
        if relay_address:
            relay_address2 = relay_address.split(".")
            relay_device_address = (int(relay_address2[0]), int(relay_address2[1]))
            if len(relay_address2) == 3:
                relay_channel_number = int(relay_address2[2])
                relay_sensor = Sensor(
                    hdl,
                    relay_device_address,
                    channel_number=relay_channel_number,
                )
            elif len(relay_address2) == 2:
                relay_sensor = Sensor(hdl, relay_device_address)

        devices.append(BusproClimate(hass, climate, preset_modes, relay_sensor))

    async_add_entites(devices)


# noinspection PyAbstractClass
class BusproClimate(ClimateEntity):
    """Representation of a Buspro switch."""

    def __init__(self, hass, device, preset_modes, relay_sensor):
        self._hass = hass
        self._device = device
        self._target_temperature = self._device.target_temperature
        self._is_on = self._device.is_on
        self._preset_modes = preset_modes
        self._mode = self._device.mode  # 1/3/4

        self._relay_sensor = relay_sensor
        self._relay_sensor_is_on = None
        if self._relay_sensor is not None:
            self._relay_sensor_is_on = self._relay_sensor.single_channel_is_on

        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON

        _LOGGER.debug("Climate class init device '{}' with relay_sensor {} and tearget temp {}")
        # _LOGGER.debug(device.device_address)
        # _LOGGER.debug(relay_sensor.device_address)
        _LOGGER.debug(self._target_temperature)


        self.async_register_callbacks()

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)
    
    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            self._device = device
            self._target_temperature = device.target_temperature
            self._is_on = device.is_on
            self._mode = device.mode

            _LOGGER.debug(f"Device '{self._device.name}', " \
                            f"IsOn: {self._is_on}, " \
                            f"Mode: {self._device.mode}, " \
                            f"TargetTemp: {self._device.target_temperature}")

            if self._hass is not None:
                self.async_write_ha_state()

        async def after_relay_sensor_update_callback(device):
            """Call after device was updated."""
            self._relay_sensor_is_on = device.single_channel_is_on
            self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

        if self._relay_sensor is not None:
            self._relay_sensor.register_device_updated_cb(after_relay_sensor_update_callback)

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
    def temperature_unit(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._device.temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        target_temperature = self._target_temperature
        if target_temperature is None:
            target_temperature = self._device.target_temperature

        return target_temperature

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode, e.g., home, away, temp.
        """
        if self._mode not in list(HDL_TO_HA_PRESET):
            return PRESET_NONE
        return HDL_TO_HA_PRESET[self._mode]

    @property
    def preset_modes(self) -> Optional[List[str]]:
        """Return a list of available preset modes.
        Requires SUPPORT_PRESET_MODE.
        """
        if len(self._preset_modes) == 0:
            return None

        keys = HA_PRESET_TO_HDL.keys() & self._preset_modes
        ha_preset_to_hdl_configured = {k:HA_PRESET_TO_HDL[k] for k in keys}
        return list(ha_preset_to_hdl_configured)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in list(HA_PRESET_TO_HDL):
            preset_mode = PRESET_NONE
        mode = HA_PRESET_TO_HDL[preset_mode]

        _LOGGER.debug(f"Setting preset mode to '{preset_mode}' ({mode}) for device '{self._device.name}'")

        climate_control = ControlFloorHeatingStatus()
        climate_control.mode = mode

        await self._device.control_heating_status(climate_control)
        self.async_write_ha_state()

    @property
    def hvac_action(self) -> Optional[str]:
        """Return current action ie. heating, idle, off."""
        if self._is_on:
            if self._relay_sensor_is_on is None:
                return HVACAction.HEATING
            else:
                if self._relay_sensor_is_on:
                    return HVACAction.HEATING
                else:
                    return HVACAction.IDLE
        else:
            return HVACAction.OFF

    @property
    def hvac_mode(self) -> Optional[str]:
        """Return current operation ie. heat, cool, idle."""
        if self._is_on:
            return HVACMode.HEAT
        else:
            return HVACMode.OFF

    @property
    def hvac_modes(self) -> Optional[List[str]]:
        """Return the list of available operation modes."""
        return [HVACMode.HEAT, HVACMode.OFF]

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set operation mode."""
        if hvac_mode == HVACMode.OFF:
            climate_control = ControlFloorHeatingStatus()
            climate_control.status = OnOffStatus.OFF.value
            await self._device.control_heating_status(climate_control)
            self.async_write_ha_state()
        elif hvac_mode == HVACMode.HEAT:
            climate_control = ControlFloorHeatingStatus()
            climate_control.status = OnOffStatus.ON.value
            await self._device.control_heating_status(climate_control)
            self.async_write_ha_state()
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 1

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        climate_control = ControlFloorHeatingStatus()
        preset = HDL_TO_HA_PRESET[self._mode]
        target_temperature = int(temperature)

        _LOGGER.debug(f"Setting '{preset}' temperature to {target_temperature}")

        if preset == PRESET_NONE:
            climate_control.normal_temperature = target_temperature
        elif preset == PRESET_HOME:
            climate_control.day_temperature = target_temperature
        elif preset == PRESET_SLEEP:
            climate_control.night_temperature = target_temperature
        elif preset == PRESET_AWAY:
            climate_control.away_temperature = target_temperature
        else:
            climate_control.normal_temperature = target_temperature

        await self._device.control_heating_status(climate_control)
        self.async_write_ha_state()
