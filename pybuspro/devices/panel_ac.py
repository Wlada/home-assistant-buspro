import logging

from .device import Device
from .generic import Generic
from ..helpers.enums import OperateCode


_LOGGER = logging.getLogger(__name__)
_CAPTURE_LOGGER = logging.getLogger("buspro.ac_capture")
_CAPTURE_OPERATE_CODES = frozenset(
    {
        OperateCode.ControlPanelAC,
        OperateCode.ControlPanelACResponse,
        OperateCode.ReadPanelAC,
        OperateCode.ReadPanelACResponse,
        OperateCode.ReadTemperature,
        OperateCode.ReadTemperatureResponse,
    }
)
_CHANNEL_AT_INDEX_2_OPERATE_CODES = frozenset(
    {
        OperateCode.ControlPanelAC,
        OperateCode.ControlPanelACResponse,
        OperateCode.ReadPanelAC,
        OperateCode.ReadPanelACResponse,
    }
)
_STATE_RESPONSE_OPERATE_CODES = frozenset(
    {
        OperateCode.ControlPanelACResponse,
        OperateCode.ReadPanelACResponse,
    }
)
_STATUS_FIELDS = (3, 4, 5, 6, 7)
_MODE_TO_VALUE = {"cool": 0, "heat": 1}
_VALUE_TO_MODE = {value: mode for mode, value in _MODE_TO_VALUE.items()}
_FAN_TO_VALUE = {"low": 1, "medium": 2, "high": 3}
_VALUE_TO_FAN = {value: mode for mode, value in _FAN_TO_VALUE.items()}


class PanelACDevice(Device):
    """Represent one independently confirmed BusPro panel AC channel."""

    def __init__(
        self,
        buspro,
        device_address,
        channel,
        name="",
        min_temp=None,
        max_temp=None,
    ):
        super().__init__(buspro, device_address, name)
        if (min_temp is None) != (max_temp is None):
            raise ValueError("Both panel AC temperature limits are required")
        if min_temp is not None and (
            type(min_temp) is not int
            or type(max_temp) is not int
            or min_temp >= max_temp
        ):
            raise ValueError("Invalid panel AC temperature limits")
        self._channel = channel
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._is_on = None
        self._selected_mode = None
        self._cool_target_temperature = None
        self._heat_target_temperature = None
        self._fan_mode = None
        self.register_telegram_received_cb(self._telegram_received_cb)

    @property
    def is_on(self):
        """Return confirmed power state, or None until a response arrives."""
        return self._is_on

    @property
    def selected_mode(self):
        """Return the confirmed stored cool/heat selection."""
        return self._selected_mode

    @property
    def cool_target_temperature(self):
        """Return the confirmed cooling target."""
        return self._cool_target_temperature

    @property
    def heat_target_temperature(self):
        """Return the confirmed heating target."""
        return self._heat_target_temperature

    @property
    def target_temperature(self):
        """Return the confirmed target for the selected mode."""
        if self._selected_mode == "cool":
            return self._cool_target_temperature
        if self._selected_mode == "heat":
            return self._heat_target_temperature
        return None

    @property
    def fan_mode(self):
        """Return a supported confirmed fan mode, or None for auto/unknown."""
        return self._fan_mode

    @property
    def min_temp(self):
        return self._min_temp

    @property
    def max_temp(self):
        return self._max_temp

    @property
    def device_identifier(self):
        """Return a stable identifier for this panel AC channel."""
        subnet, device = self._device_address
        return f"panel-ac-{subnet}-{device}-{self._channel}"

    async def set_on(self):
        """Request power on."""
        await self._set_power(1)

    async def set_off(self):
        """Request power off."""
        await self._set_power(0)

    async def set_mode(self, mode):
        """Request a supported mode while the AC is confirmed on."""
        if mode not in _MODE_TO_VALUE:
            raise ValueError(f"Unsupported panel AC mode: {mode}")
        self._require_confirmed_on()
        await self._set_field(6, _MODE_TO_VALUE[mode])

    async def set_target_temperature(self, temperature):
        """Request the selected mode's integer target temperature."""
        self._require_confirmed_on()
        if self._selected_mode not in _MODE_TO_VALUE:
            raise RuntimeError("Panel AC mode is not confirmed")
        if not self._is_valid_temperature(temperature):
            raise ValueError("Panel AC target temperature is out of range")
        field = 4 if self._selected_mode == "cool" else 7
        await self._set_field(field, temperature)

    async def set_fan_mode(self, fan_mode):
        """Request a supported fan mode while the AC is confirmed on."""
        if fan_mode not in _FAN_TO_VALUE:
            raise ValueError(f"Unsupported panel AC fan mode: {fan_mode}")
        self._require_confirmed_on()
        await self._set_field(5, _FAN_TO_VALUE[fan_mode])

    async def read_status(self):
        """Request each confirmed stored climate field for this channel."""
        for field in _STATUS_FIELDS:
            command = Generic(
                self._buspro,
                self._device_address,
                [field, self._channel, self._channel],
                OperateCode.ReadPanelAC,
                self._name,
            )
            await command.run()

    async def _set_power(self, power):
        await self._set_field(3, power)

    async def _set_field(self, field, value):
        command = Generic(
            self._buspro,
            self._device_address,
            [field, value, self._channel],
            OperateCode.ControlPanelAC,
            self._name,
        )
        await command.run()

    def _require_confirmed_on(self):
        if self._is_on is not True:
            raise RuntimeError("Panel AC must be confirmed on")

    def _is_valid_temperature(self, value):
        if type(value) is not int:
            return False
        if self._min_temp is None:
            return True
        return self._min_temp <= value <= self._max_temp

    def _capture_telegram(self, telegram):
        operate_code = telegram.operate_code
        if operate_code not in _CAPTURE_OPERATE_CODES:
            return

        payload = telegram.payload
        if not isinstance(payload, (list, tuple)):
            payload = []

        field = payload[0] if len(payload) > 0 else None
        value = payload[1] if len(payload) > 1 else None
        channel = None
        if operate_code in _CHANNEL_AT_INDEX_2_OPERATE_CODES:
            channel = payload[2] if len(payload) > 2 else None
            if channel != self._channel:
                return

        direction = (
            "from_panel"
            if getattr(telegram, "source_address", None)
            == self._device_address
            else "to_panel"
        )
        opcode = operate_code.value.hex().upper()
        _CAPTURE_LOGGER.debug(
            "instance=%s direction=%s opcode=%s "
            "field=%s value=%s channel=%s payload=%s",
            self._name,
            direction,
            opcode,
            field,
            value,
            channel,
            list(payload),
        )

    def _telegram_received_cb(self, telegram):
        self._capture_telegram(telegram)
        if telegram.operate_code not in _STATE_RESPONSE_OPERATE_CODES:
            return

        payload = telegram.payload
        if not isinstance(payload, (list, tuple)) or len(payload) < 3:
            _LOGGER.debug("Ignoring malformed panel AC response payload")
            return

        field, value, channel = payload[:3]
        if channel != self._channel:
            return

        state_attribute = None
        confirmed_value = None
        if field == 3:
            if value not in (0, 1):
                return
            state_attribute = "_is_on"
            confirmed_value = value == 1
        elif field == 4:
            if not self._is_valid_temperature(value):
                return
            state_attribute = "_cool_target_temperature"
            confirmed_value = value
        elif field == 5:
            if value == 0:
                state_attribute = "_fan_mode"
                confirmed_value = None
            elif value in _VALUE_TO_FAN:
                state_attribute = "_fan_mode"
                confirmed_value = _VALUE_TO_FAN[value]
            else:
                return
        elif field == 6:
            if value not in _VALUE_TO_MODE:
                return
            state_attribute = "_selected_mode"
            confirmed_value = _VALUE_TO_MODE[value]
        elif field == 7:
            if not self._is_valid_temperature(value):
                return
            state_attribute = "_heat_target_temperature"
            confirmed_value = value
        else:
            return

        if getattr(self, state_attribute) == confirmed_value:
            return
        setattr(self, state_attribute, confirmed_value)
        self._call_device_updated()
