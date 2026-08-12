"""Home Assistant adapter for an Enviro panel AC channel."""

import asyncio

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from ..buspro import DATA_BUSPRO

_POWER_ON_CONFIRMATION_TIMEOUT = 5


class BusproPanelACClimate(ClimateEntity):
    """Expose confirmed Enviro panel AC state without optimistic writes."""

    def __init__(self, hass, device, temperature_sensor):
        self._hass = hass
        self._device = device
        self._temperature_sensor = temperature_sensor
        self._power_on_confirmed = asyncio.Event()
        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._register_callbacks()

    def _register_callbacks(self):
        async def after_device_update(device):
            if self._device.is_on is True:
                self._power_on_confirmed.set()
            elif self._device.is_on is False:
                self._power_on_confirmed.clear()
            if self._hass is not None:
                self.async_write_ha_state()

        async def after_temperature_update(device):
            if self._hass is not None:
                self.async_write_ha_state()

        self._device.register_device_updated_cb(after_device_update)
        self._temperature_sensor.register_device_updated_cb(
            after_temperature_update
        )

    async def async_added_to_hass(self):
        """Request the panel's stored state after entity registration."""
        await self._device.read_status()

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
        return self._temperature_sensor.temperature

    @property
    def target_temperature(self):
        return self._device.target_temperature

    @property
    def hvac_modes(self):
        return [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT]

    @property
    def hvac_mode(self):
        if self._device.is_on is False:
            return HVACMode.OFF
        if self._device.is_on is not True:
            return None
        if self._device.selected_mode == "cool":
            return HVACMode.COOL
        if self._device.selected_mode == "heat":
            return HVACMode.HEAT
        return None

    @property
    def fan_modes(self):
        return ["low", "medium", "high"]

    @property
    def fan_mode(self):
        return self._device.fan_mode

    async def async_turn_off(self):
        await self._device.set_off()

    async def async_turn_on(self):
        await self._device.set_on()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self._device.set_off()
            return
        if hvac_mode not in (HVACMode.COOL, HVACMode.HEAT):
            raise ValueError(f"Unsupported panel AC HVAC mode: {hvac_mode}")
        mode = "cool" if hvac_mode == HVACMode.COOL else "heat"
        if self._device.is_on is not True:
            await self._device.set_on()
            if self._device.selected_mode == mode:
                return
            await self._async_wait_for_power_on()
        await self._device.set_mode(mode)

    async def _async_wait_for_power_on(self):
        if self._device.is_on is True:
            return
        await asyncio.wait_for(
            self._power_on_confirmed.wait(),
            timeout=_POWER_ON_CONFIRMATION_TIMEOUT,
        )
        if self._device.is_on is not True:
            raise RuntimeError(
                "Panel AC power-on confirmation did not update state"
            )

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if isinstance(temperature, bool):
            raise ValueError("Panel AC temperature must be an integer")
        integer_temperature = int(temperature)
        if integer_temperature != temperature:
            raise ValueError("Panel AC temperature must be an integer")
        if not self.min_temp <= integer_temperature <= self.max_temp:
            raise ValueError("Panel AC temperature is out of range")
        await self._device.set_target_temperature(integer_temperature)

    async def async_set_fan_mode(self, fan_mode):
        if fan_mode not in self.fan_modes:
            raise ValueError(f"Unsupported panel AC fan mode: {fan_mode}")
        await self._device.set_fan_mode(fan_mode)
