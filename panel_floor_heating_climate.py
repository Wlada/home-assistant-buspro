"""Home Assistant adapter for an Enviro-controlled floor-heating zone."""

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from ..buspro import DATA_BUSPRO


_HDL_MODE_TO_PRESET = {
    1: "none",
    2: "home",
    3: "sleep",
    4: "away",
}


class BusproPanelFloorHeatingClimate(ClimateEntity):
    """Control an Enviro FH channel while exposing only confirmed state."""

    def __init__(self, hass, device, temperature_entity=None):
        self._hass = hass
        self._device = device
        self._temperature_entity = temperature_entity
        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._device.register_device_updated_cb(self._after_device_update)

    async def _after_device_update(self, device):
        if self._hass is not None:
            self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Track the HA sensor and request one confirmed startup snapshot."""
        if self._temperature_entity is not None:
            unsubscribe = async_track_state_change_event(
                self._hass,
                [self._temperature_entity],
                self._async_temperature_changed,
            )
            self.async_on_remove(unsubscribe)
        await self._device.read_status()

    @callback
    def _async_temperature_changed(self, event):
        self.async_write_ha_state()

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._device.name

    @property
    def available(self):
        return self._hass.data[DATA_BUSPRO].connected

    @property
    def unique_id(self):
        return self._device.device_identifier

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self):
        return self._device.min_temp

    @property
    def max_temp(self):
        return self._device.max_temp

    @property
    def target_temperature_step(self):
        return 1

    @property
    def current_temperature(self):
        if self._temperature_entity is None:
            return None
        state = self._hass.states.get(self._temperature_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self):
        return self._device.target_temperature

    @property
    def hvac_modes(self):
        return [HVACMode.OFF, HVACMode.HEAT]

    @property
    def hvac_mode(self):
        if self._device.is_on is True:
            return HVACMode.HEAT
        if self._device.is_on is False:
            return HVACMode.OFF
        return None

    async def async_turn_on(self):
        await self._device.set_on()

    async def async_turn_off(self):
        await self._device.set_off()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self._device.set_off()
            return
        if hvac_mode == HVACMode.HEAT:
            await self._device.set_on()
            return
        raise ValueError(f"Unsupported floor-heating HVAC mode: {hvac_mode}")

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if isinstance(temperature, bool):
            raise ValueError("Floor-heating temperature must be an integer")
        integer_temperature = int(temperature)
        if integer_temperature != temperature:
            raise ValueError("Floor-heating temperature must be an integer")
        await self._device.set_target_temperature(integer_temperature)

    @property
    def hvac_action(self):
        if self._device.is_on is False:
            return HVACAction.OFF
        if self._device.is_on is not True:
            return None
        if self._device.actuator_is_on is True:
            return HVACAction.HEATING
        if self._device.actuator_is_on is False:
            return HVACAction.IDLE
        return None

    @property
    def preset_mode(self):
        return _HDL_MODE_TO_PRESET.get(self._device.mode)

    @property
    def extra_state_attributes(self):
        return {
            "read_only": False,
            "control_path": "enviro_panel_confirmed",
            "actuator_open": self._device.actuator_is_on,
            "controller_mode": self._device.mode,
            "normal_temperature": self._device.normal_temperature,
            "day_temperature": self._device.day_temperature,
            "night_temperature": self._device.night_temperature,
            "away_temperature": self._device.away_temperature,
        }
