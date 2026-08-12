import asyncio
import logging

from .control import (
    _ReadSensorStatus,
    _ReadStatusOfUniversalSwitch,
    _ReadStatusOfChannels,
    _ReadFloorHeatingStatus,
    _ReadDryContactStatus,
    _ReadSensorsInOneStatus,
    _ReadMotionSensorStatus,
    _ReadTemperature,
)
from .device import Device
from ..helpers.enums import *

_LOGGER = logging.getLogger(__name__)

_PIR_DIAGNOSTIC_OPERATE_CODES = {
    OperateCode.ReadMotionSensorStatusResponse,
    OperateCode.ReadSensorStatusResponse,
    OperateCode.BroadcastSensorStatusResponse,
    OperateCode.BroadcastSensorStatusAutoResponse,
    OperateCode.ReadSensorsInOneStatusResponse,
    OperateCode.BroadcastSensorsInOneStatusResponse,
}

class Sensor(Device):
    def __init__(
        self,
        buspro,
        device_address,
        universal_switch_number=None,
        channel_number=None,
        device=None,
        request_profile=None,
        switch_number=None,
        name="",
        delay_read_current_state_seconds=0,
        diagnostic_role=None,
    ):
        super().__init__(buspro, device_address, name)

        self._buspro = buspro
        self._device_address = device_address
        self._universal_switch_number = universal_switch_number
        self._channel_number = channel_number
        self._name = name
        self._device = device
        self._request_profile = request_profile
        self._diagnostic_role = diagnostic_role
        self._switch_number = switch_number

        self._current_temperature = None
        self._brightness = None
        self._humidity = None
        self._motion_sensor = None
        self._sonic = None
        self._dry_contact_1_status = None
        self._dry_contact_2_status = None
        self._universal_switch_status = OnOffStatus.OFF
        self._channel_status = 0
        self._switch_status = 0

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_sensor(run_from_init=True)

    @staticmethod
    def _decode_uint16_be(high_byte, low_byte):
        return (high_byte << 8) | low_byte
    @staticmethod
    def _decode_signed_temperature(value):
        magnitude = value & 0x7F
        return -magnitude if value & 0x80 else magnitude


    @property
    def _diagnostic_capture(self):
        return getattr(self._buspro, "sensor_diagnostic_capture", None)

    def _capture_request(self, request):
        capture = self._diagnostic_capture
        if (
            capture is None
            or self._device not in ("pir", "sensors_in_one")
            or self._diagnostic_role not in ("temperature", "motion")
        ):
            return
        capture.record_request(
            name=self._name,
            device=self._device,
            role=self._diagnostic_role,
            request_profile=self._request_profile,
            operate_code=request.telegram.operate_code.name,
        )

    def _capture_response(self, telegram, payload):
        capture = self._diagnostic_capture
        if (
            capture is None
            or self._device not in ("pir", "sensors_in_one")
            or self._diagnostic_role not in ("temperature", "motion")
        ):
            return
        capture.record_response(
            name=self._name,
            device=self._device,
            role=self._diagnostic_role,
            request_profile=self._request_profile,
            operate_code=telegram.operate_code.name,
            payload=payload,
            temperature=self.temperature,
            illuminance=self.brightness,
            humidity=self.humidity,
            raw_motion=self._motion_sensor,
            movement=self.movement,
        )

    @staticmethod
    def _unknown_operate_code(telegram):
        if telegram.operate_code is not None:
            return None
        udp_data = getattr(telegram, "udp_data", None)
        if (
            not isinstance(udp_data, (bytes, bytearray))
            or len(udp_data) < 23
        ):
            return None
        return bytes(udp_data[21:23]).hex().upper()

    def _capture_unknown_response(self, telegram, payload):
        capture = self._diagnostic_capture
        operate_code = self._unknown_operate_code(telegram)
        if (
            capture is None
            or self._device != "pir"
            or self._diagnostic_role != "temperature"
            or operate_code is None
        ):
            return
        capture.record_raw_response(
            name=self._name,
            device=self._device,
            role=self._diagnostic_role,
            operate_code=operate_code,
            payload=payload,
        )

    def _telegram_received_cb(self, telegram):
        """Handle incoming telegrams."""
        payload = telegram.payload
        self._capture_unknown_response(telegram, payload)
        if (
            self._device == "pir"
            and self._request_profile != "motion"
            and self._channel_number is None
            and telegram.operate_code in _PIR_DIAGNOSTIC_OPERATE_CODES
        ):
            _LOGGER.warning(
                "PIR measurement diagnostic name=%s operate_code=%s "
                "payload_length=%s payload=%s",
                self._name,
                telegram.operate_code.name,
                len(payload) if isinstance(payload, (list, tuple)) else None,
                payload,
            )

        if telegram.operate_code == OperateCode.ReadSensorStatusResponse:
            if not isinstance(payload, (list, tuple)) or len(payload) < 7:
                return
            if payload[0] != SuccessOrFailure.Success.value[0]:
                return
            self._current_temperature = payload[1]
            self._brightness = self._decode_uint16_be(payload[2], payload[3])
            self._motion_sensor = payload[4]
            if len(payload) >= 8:
                self._sonic = payload[5]
                self._dry_contact_1_status = payload[6]
                self._dry_contact_2_status = payload[7]
            else:
                self._sonic = None
                self._dry_contact_1_status = payload[5]
                self._dry_contact_2_status = payload[6]
            self._capture_response(telegram, payload)
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadMotionSensorStatusResponse:
            if not isinstance(payload, (list, tuple)) or len(payload) < 4:
                return
            self._motion_sensor = payload[3]
            self._capture_response(telegram, payload)
            self._call_device_updated()

        elif telegram.operate_code in (
            OperateCode.ReadSensorsInOneStatusResponse,
            OperateCode.BroadcastSensorsInOneStatusResponse,
        ):
            if not isinstance(payload, (list, tuple)) or len(payload) < 8:
                return
            self._current_temperature = payload[1]
            self._brightness = self._decode_uint16_be(payload[2], payload[3])
            self._humidity = payload[4]
            self._motion_sensor = payload[7]
            self._dry_contact_1_status = payload[8] if len(payload) > 8 else None
            self._dry_contact_2_status = payload[9] if len(payload) > 9 else None
            self._capture_response(telegram, payload)
            self._call_device_updated()

        elif telegram.operate_code in (
            OperateCode.BroadcastSensorStatusResponse,
            OperateCode.BroadcastSensorStatusAutoResponse,
        ):
            if not isinstance(payload, (list, tuple)) or len(payload) < 7:
                return
            self._current_temperature = payload[0]
            self._brightness = self._decode_uint16_be(payload[1], payload[2])
            self._motion_sensor = payload[3]
            self._sonic = payload[4]
            self._dry_contact_1_status = payload[5]
            self._dry_contact_2_status = payload[6]
            self._capture_response(telegram, payload)
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadFloorHeatingStatusResponse:
            self._current_temperature = telegram.payload[1]
            self._call_device_updated()
        elif telegram.operate_code == OperateCode.ReadTemperatureResponse:
            if self._device != "temperature_channel":
                return
            if not isinstance(payload, (list, tuple)) or len(payload) < 2:
                return
            if payload[0] != self._channel_number:
                return
            self._current_temperature = self._decode_signed_temperature(
                payload[1]
            )
            self._call_device_updated()


        elif telegram.operate_code == OperateCode.BroadcastTemperatureResponse:
            if self._device == "temperature_channel":
                return
            if not isinstance(payload, (list, tuple)) or len(payload) < 2:
                return
            self._current_temperature = payload[1]
            self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadStatusOfUniversalSwitchResponse:
            switch_number = telegram.payload[0]
            universal_switch_status = telegram.payload[1]

            if switch_number == self._universal_switch_number:
                self._universal_switch_status = universal_switch_status
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastStatusOfUniversalSwitch:
            if (
                self._universal_switch_number is not None
                and self._universal_switch_number <= telegram.payload[0]
            ):
                self._universal_switch_status = telegram.payload[self._universal_switch_number]
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.UniversalSwitchControlResponse:
            switch_number = telegram.payload[0]
            universal_switch_status = telegram.payload[1]

            if switch_number == self._universal_switch_number:
                self._universal_switch_status = universal_switch_status
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse:
            if self._channel_number:
                if self._channel_number <= telegram.payload[0]:
                    self._channel_status = telegram.payload[self._channel_number]
                    self._call_device_updated()

        elif telegram.operate_code == OperateCode.SingleChannelControlResponse:
            if self._channel_number == telegram.payload[0]:
                self._channel_status = telegram.payload[2]
                self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadDryContactStatusResponse:
            if self._switch_number == telegram.payload[1]:
                self._switch_status = telegram.payload[2]
                self._call_device_updated()

    async def read_sensor_status(self):
        """Read the status of the sensor."""
        _LOGGER.debug(
            "Sensor %s: Temperature updated to %s, device: %s",
            self.device_identifier,
            self._current_temperature,
            self._device
        )
        if self._universal_switch_number is not None:
            request = _ReadStatusOfUniversalSwitch(self._buspro)
            request.switch_number = self._universal_switch_number
        elif self._request_profile == "motion":
            request = _ReadMotionSensorStatus(self._buspro)
        elif self._device == "pir":
            request = _ReadSensorStatus(self._buspro)
        elif self._device == "temperature_channel":
            if self._channel_number is None:
                return
            request = _ReadTemperature(self._buspro)
            request.channel_number = self._channel_number
        elif (
            self._device == "sensors_in_one"
            and (
                self._channel_number is None
                or 201 <= self._channel_number <= 255
            )
        ):
            request = _ReadSensorsInOneStatus(self._buspro)
        elif self._channel_number is not None:
            request = _ReadStatusOfChannels(self._buspro)
        elif self._device == "dlp":
            request = _ReadFloorHeatingStatus(self._buspro)
        elif self._device == "dry_contact":
            request = _ReadDryContactStatus(self._buspro)
            request.switch_number = self._switch_number
        else:
            request = _ReadSensorStatus(self._buspro)

        request.subnet_id, request.device_id = self._device_address
        self._capture_request(request)
        await request.send()

    @property
    def temperature(self):
        if self._current_temperature is None:
            return None
        if self._device == "dlp":
            return self._current_temperature
        if self._device in ["12in1", "8in1", "pir", "sensors_in_one"]:
            return self._current_temperature - 20
        return self._current_temperature

    @property
    def brightness(self):
        return self._brightness

    @property
    def humidity(self):
        return self._humidity

    @property
    def motion_available(self):
        return self._motion_sensor is not None or self._sonic is not None

    @property
    def movement(self):
        if self._motion_sensor == 1 or self._sonic == 1:
            return True
        return False

    @property
    def dry_contact_1_is_on(self):
        return self._dry_contact_1_status == 1

    @property
    def dry_contact_2_is_on(self):
        return self._dry_contact_2_status == 1

    @property
    def universal_switch_is_on(self):
        return self._universal_switch_status == 1

    @property
    def single_channel_is_on(self):
        return self._channel_status > 0

    @property
    def switch_status(self):
        return self._switch_status == 1

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._universal_switch_number}-{self._channel_number}-{self._switch_number}"

    def _call_read_current_status_of_sensor(self, run_from_init=False):
        """Initiate reading of the current sensor status."""
        async def read_current_status_of_sensor():
            if run_from_init:
                await asyncio.sleep(5)
            await self.read_sensor_status()

        asyncio.ensure_future(read_current_status_of_sensor(), loop=self._buspro.loop)