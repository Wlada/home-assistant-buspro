"""Confirmed state and control for an Enviro floor-heating zone."""

import logging

from .device import Device
from .generic import Generic
from ..helpers.enums import OperateCode


_LOGGER = logging.getLogger(__name__)

_PANEL_FIELD_RESPONSE = OperateCode.ControlPanelACResponse
_PANEL_FIELD_READ_RESPONSE = OperateCode.ReadPanelACResponse
_FLOOR_HEATING_READ_STATUS_RESPONSE = OperateCode.ReadFloorHeatingStatusResponse
_FLOOR_HEATING_STATUS_RESPONSE = (
    OperateCode.ControlFloorHeatingStatusResponse
)
_TARGET_ATTRIBUTE_BY_MODE = {
    1: "_normal_temperature",
    2: "_day_temperature",
    3: "_night_temperature",
    4: "_away_temperature",
}
_POWER_FIELD = 20
_MODE_FIELD = 21
_NORMAL_TEMPERATURE_FIELD = 25
_STATUS_FIELDS = (_POWER_FIELD, _MODE_FIELD, _NORMAL_TEMPERATURE_FIELD)


class PanelFloorHeatingDevice(Device):
    """Combine confirmed Enviro state with a 6CH actuator output.

    State changes are published only after a panel or 6CH response; commands
    never update local state optimistically.
    """

    def __init__(
        self,
        buspro,
        panel_address,
        panel_channel,
        actuator_address,
        actuator_channel,
        name="",
        status_route=None,
        min_temp=5,
        max_temp=35,
    ):
        super().__init__(buspro, panel_address, name)
        if not 1 <= panel_channel <= 255:
            raise ValueError("Panel floor-heating channel must be 1..255")
        if not 1 <= actuator_channel <= 255:
            raise ValueError("Floor-heating actuator channel must be 1..255")
        if status_route is not None and (
            type(status_route) is not int or not 0 <= status_route <= 255
        ):
            raise ValueError("Floor-heating status route must be a byte")
        if (
            type(min_temp) is not int
            or type(max_temp) is not int
            or min_temp >= max_temp
        ):
            raise ValueError("Invalid floor-heating temperature limits")

        self._panel_address = panel_address
        self._panel_channel = panel_channel
        self._actuator_address = actuator_address
        self._actuator_channel = actuator_channel
        self._status_route = status_route
        self._min_temp = min_temp
        self._max_temp = max_temp

        self._is_on = None
        self._mode = None
        self._normal_temperature = None
        self._day_temperature = None
        self._night_temperature = None
        self._away_temperature = None
        self._actuator_is_on = None

        self._buspro.register_telegram_received_device_cb(
            self._panel_telegram_received_cb, self._panel_address
        )
        self._buspro.register_telegram_received_device_cb(
            self._actuator_telegram_received_cb, self._actuator_address
        )

    @property
    def is_on(self):
        return self._is_on

    @property
    def mode(self):
        return self._mode

    @property
    def normal_temperature(self):
        return self._normal_temperature

    @property
    def day_temperature(self):
        return self._day_temperature

    @property
    def night_temperature(self):
        return self._night_temperature

    @property
    def away_temperature(self):
        return self._away_temperature

    @property
    def target_temperature(self):
        attribute = _TARGET_ATTRIBUTE_BY_MODE.get(self._mode)
        if attribute is None:
            return None
        return getattr(self, attribute)

    @property
    def actuator_is_on(self):
        return self._actuator_is_on

    @property
    def min_temp(self):
        return self._min_temp

    @property
    def max_temp(self):
        return self._max_temp

    @property
    def device_identifier(self):
        """Preserve the legacy zone ID so HA keeps the existing entity ID."""
        legacy_address = self._actuator_address + (self._actuator_channel,)
        return f"{legacy_address}"

    async def set_on(self):
        """Request heating enable through the mapped Enviro FH channel."""
        await self._set_panel_field(_POWER_FIELD, 1)

    async def set_off(self):
        """Request heating disable through the mapped Enviro FH channel."""
        await self._set_panel_field(_POWER_FIELD, 0)

    async def set_mode(self, mode):
        """Request a confirmed HDL floor-heating mode."""
        if mode not in _TARGET_ATTRIBUTE_BY_MODE:
            raise ValueError(f"Unsupported floor-heating mode: {mode}")
        await self._set_panel_field(_MODE_FIELD, mode)

    async def set_target_temperature(self, temperature):
        """Request the Normal target without changing confirmed local state."""
        if self._mode != 1:
            raise RuntimeError(
                "Normal floor-heating mode must be confirmed before "
                "changing its target"
            )
        if (
            type(temperature) is not int
            or not self._min_temp <= temperature <= self._max_temp
        ):
            raise ValueError("Floor-heating target temperature is out of range")
        await self._set_panel_field(_NORMAL_TEMPERATURE_FIELD, temperature)

    async def read_status(self):
        """Request confirmed Enviro state and the actual 6CH output."""
        for field in _STATUS_FIELDS:
            await self._read_panel_field(
                self._panel_address, field, self._panel_channel
            )
        await self._read_panel_field(
            self._actuator_address, _POWER_FIELD, self._actuator_channel
        )

    async def _read_panel_field(self, address, field, channel):
        command = Generic(
            self._buspro,
            address,
            [field, channel, channel],
            OperateCode.ReadPanelAC,
            self._name,
        )
        await command.run()

    async def _set_panel_field(self, field, value):
        command = Generic(
            self._buspro,
            self._panel_address,
            [field, value, self._panel_channel],
            OperateCode.ControlPanelAC,
            self._name,
        )
        await command.run()

    def _panel_telegram_received_cb(self, telegram):
        payload = getattr(telegram, "payload", None)
        if not isinstance(payload, (list, tuple)):
            return

        if telegram.operate_code in (
            _PANEL_FIELD_RESPONSE,
            _PANEL_FIELD_READ_RESPONSE,
        ):
            self._update_from_panel_field(payload)
        elif telegram.operate_code == _FLOOR_HEATING_READ_STATUS_RESPONSE:
            self._update_from_floor_heating_read_status(payload)
        elif telegram.operate_code == _FLOOR_HEATING_STATUS_RESPONSE:
            self._update_from_floor_heating_status(payload)

    def _update_from_panel_field(self, payload):
        if len(payload) < 3:
            return
        field, value, channel = payload[:3]
        if channel != self._panel_channel:
            return

        if field == 20 and value in (0, 1):
            self._set_confirmed_value("_is_on", value == 1)
        elif field == 21 and value in _TARGET_ATTRIBUTE_BY_MODE:
            self._set_confirmed_value("_mode", value)
        elif field == 25 and self._valid_temperature(value):
            self._set_confirmed_value("_normal_temperature", value)

    def _update_from_floor_heating_read_status(self, payload):
        if len(payload) < 11:
            _LOGGER.debug("Ignoring malformed floor-heating read response")
            return
        self._update_complete_status(
            payload,
            route_index=10,
            enabled_index=2,
            mode_index=3,
            temperature_start=4,
        )

    def _update_from_floor_heating_status(self, payload):
        if len(payload) < 12:
            _LOGGER.debug("Ignoring malformed floor-heating status response")
            return

        self._update_complete_status(
            payload,
            route_index=11,
            enabled_index=2,
            mode_index=3,
            temperature_start=4,
        )

    def _update_complete_status(
        self,
        payload,
        route_index,
        enabled_index,
        mode_index,
        temperature_start,
    ):
        if (
            self._status_route is None
            or payload[route_index] != self._status_route
        ):
            return

        enabled, mode = payload[enabled_index], payload[mode_index]
        temperatures = payload[temperature_start:temperature_start + 4]
        if enabled not in (0, 1):
            return
        if mode not in _TARGET_ATTRIBUTE_BY_MODE:
            return
        if not all(self._valid_temperature(value) for value in temperatures):
            return

        changed = False
        updates = (
            ("_is_on", enabled == 1),
            ("_mode", mode),
            ("_normal_temperature", temperatures[0]),
            ("_day_temperature", temperatures[1]),
            ("_night_temperature", temperatures[2]),
            ("_away_temperature", temperatures[3]),
        )
        for attribute, value in updates:
            if getattr(self, attribute) != value:
                setattr(self, attribute, value)
                changed = True
        if changed:
            self._call_device_updated()

    def _actuator_telegram_received_cb(self, telegram):
        if telegram.operate_code not in (
            _PANEL_FIELD_RESPONSE,
            _PANEL_FIELD_READ_RESPONSE,
        ):
            return
        payload = getattr(telegram, "payload", None)
        if not isinstance(payload, (list, tuple)) or len(payload) < 3:
            return

        field, value, channel = payload[:3]
        if field != 20 or value not in (0, 1):
            return
        if channel != self._actuator_channel:
            return
        self._set_confirmed_value("_actuator_is_on", value == 1)

    @staticmethod
    def _valid_temperature(value):
        return type(value) is int and 5 <= value <= 50

    def _set_confirmed_value(self, attribute, value):
        if getattr(self, attribute) == value:
            return
        setattr(self, attribute, value)
        self._call_device_updated()
