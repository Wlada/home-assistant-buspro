''' pybuspro version 1.0.0  '''

import asyncio
import logging

from .helpers.enums import *
from .transport.network_interface import NetworkInterface


# ip, port = gateway_address
# subnet_id, device_id, channel = device_address

_SENSOR_DIAGNOSTIC_OPERATE_CODES = {
    OperateCode.ReadMotionSensorStatusResponse,
    OperateCode.ReadSensorStatusResponse,
    OperateCode.BroadcastSensorStatusResponse,
    OperateCode.BroadcastSensorStatusAutoResponse,
    OperateCode.ReadSensorsInOneStatusResponse,
    OperateCode.BroadcastSensorsInOneStatusResponse,
}

_FLOOR_HEATING_OPERATE_CODES = frozenset(
    {
        OperateCode.ReadFloorHeatingStatus,
        OperateCode.ReadFloorHeatingStatusResponse,
        OperateCode.ControlFloorHeatingStatus,
        OperateCode.ControlFloorHeatingStatusResponse,
    }
)
_PANEL_OPERATE_CODES = frozenset(
    {
        OperateCode.ControlPanelAC,
        OperateCode.ControlPanelACResponse,
        OperateCode.ReadPanelAC,
        OperateCode.ReadPanelACResponse,
    }
)
_PANEL_FLOOR_HEATING_COMMANDS = frozenset({20, 21, 22, 23, 25, 26, 27, 28})


class StateUpdater:
    def __init__(self, buspro, sleep=10):
        self.buspro = buspro
        self.run_forever = True
        self.run_task = None
        self.sleep = sleep

    async def start(self):
        self.run_task = self.buspro.loop.create_task(self.run())

    async def run(self):
        await asyncio.sleep(0)
        self.buspro.logger.info("Starting StateUpdater with {} seconds interval".format(self.sleep))

        while True:
            await asyncio.sleep(self.sleep)
            await self.buspro.sync()


class Buspro:

    def __init__(self, gateway_address_send_receive, loop_=None):
        self.loop = loop_ or asyncio.get_event_loop()
        self.state_updater = None
        self.started = False
        self.network_interface = None
        self.logger = logging.getLogger("buspro.log")
        self.telegram_logger = logging.getLogger("buspro.telegram")

        self.callback_all_messages = None
        self._telegram_received_cbs = []

        self.gateway_address_send_receive = gateway_address_send_receive

    def __del__(self):
        if self.started:
            try:
                task = self.loop.create_task(self.stop())
                self.loop.run_until_complete(task)
            except RuntimeError as exp:
                self.logger.warning("Could not close loop, reason: {}".format(exp))

    # noinspection PyUnusedLocal
    async def start(self, state_updater=False):  # , daemon_mode=False):
        self.network_interface = NetworkInterface(self, self.gateway_address_send_receive)
        self.network_interface.register_callback(self._callback_all_messages)
        await self.network_interface.start()

        if state_updater:
            self.state_updater = StateUpdater(self)
            await self.state_updater.start()

        '''
        if daemon_mode:
            await self._loop_until_sigint()
        '''

        self.started = True

        # await asyncio.sleep(5)
        # await self.network_interface.send_message(b'\0x01')

    async def stop(self):
        await self._stop_network_interface()
        self.started = False

    def _callback_all_messages(self, telegram):
        self._capture_floor_heating_telegram(telegram, "incoming")
        self._capture_sensor_dispatch(telegram)

        if self.callback_all_messages is not None:
            self.callback_all_messages(telegram)

        for telegram_received_cb in self._telegram_received_cbs:
            device_address = telegram_received_cb['device_address']

            # Sender callback kun for oppgitt kanal
            if device_address == telegram.target_address or device_address == telegram.source_address:
                if telegram.operate_code is not OperateCode.TIME_IF_FROM_LOGIC_OR_SECURITY:
                    postfix = telegram_received_cb['postfix']
                    if postfix is not None:
                        telegram_received_cb['callback'](telegram, postfix)
                    else:
                        telegram_received_cb['callback'](telegram)

    def _capture_sensor_dispatch(self, telegram):
        capture = getattr(self, "sensor_diagnostic_capture", None)
        if (
            capture is None
            or telegram.operate_code not in _SENSOR_DIAGNOSTIC_OPERATE_CODES
        ):
            return

        candidates = []
        for registered_callback in self._telegram_received_cbs:
            callback = registered_callback["callback"]
            sensor = getattr(callback, "__self__", None)
            device = getattr(sensor, "_device", None)
            role = getattr(sensor, "_diagnostic_role", None)
            if (
                device not in ("pir", "sensors_in_one")
                or role not in ("temperature", "motion")
            ):
                continue

            registered_address = registered_callback["device_address"]
            source_matches = registered_address == telegram.source_address
            target_matches = registered_address == telegram.target_address
            if source_matches and target_matches:
                matched_by = "both"
            elif source_matches:
                matched_by = "source"
            elif target_matches:
                matched_by = "target"
            else:
                matched_by = None

            candidates.append(
                {
                    "name": getattr(sensor, "_name", ""),
                    "device": device,
                    "role": role,
                    "matched_by": matched_by,
                }
            )

        if candidates:
            capture.record_dispatch(
                operate_code=telegram.operate_code.name,
                candidates=candidates,
            )

    @staticmethod
    def _address_prefix(address):
        if not isinstance(address, (list, tuple)) or len(address) < 2:
            return None
        return tuple(address[:2])

    def _diagnostic_aliases_for(self, address):
        address_prefix = self._address_prefix(address)
        if address_prefix is None:
            return ["unknown"]
        if address_prefix == (200, 200):
            return ["home_assistant"]
        if address_prefix == (255, 255):
            return ["broadcast"]

        aliases = []
        seen = set()
        for registered_callback in self._telegram_received_cbs:
            registered_address = registered_callback.get("device_address")
            if self._address_prefix(registered_address) != address_prefix:
                continue

            callback = registered_callback.get("callback")
            owner = getattr(callback, "__self__", None)
            name = getattr(owner, "_name", None)
            if not name and owner is not None:
                name = owner.__class__.__name__
            if not name:
                name = "registered_device"

            alias = {"name": str(name)}
            if isinstance(registered_address, (list, tuple)) and len(registered_address) > 2:
                channel = registered_address[2]
            else:
                channel = self._diagnostic_channel_for_callback(
                    registered_address, callback
                )
            if type(channel) is int:
                alias["configured_channel"] = channel

            identity = (alias["name"], alias.get("configured_channel"))
            if identity in seen:
                continue
            seen.add(identity)
            aliases.append(alias)

        return aliases or ["unmapped"]

    def _diagnostic_channel_for_callback(self, registered_address, callback):
        owner = getattr(callback, "__self__", None)
        callback_name = getattr(callback, "__name__", "")
        if owner is None:
            return None

        if callback_name == "_panel_telegram_received_cb":
            mappings = (("_panel_address", "_panel_channel"),)
        elif callback_name == "_actuator_telegram_received_cb":
            mappings = (("_actuator_address", "_actuator_channel"),)
        else:
            mappings = (
                ("_panel_address", "_panel_channel"),
                ("_actuator_address", "_actuator_channel"),
            )

        registered_prefix = self._address_prefix(registered_address)
        for address_attribute, channel_attribute in mappings:
            owner_address = getattr(owner, address_attribute, None)
            if self._address_prefix(owner_address) != registered_prefix:
                continue
            channel = getattr(owner, channel_attribute, None)
            if type(channel) is int:
                return channel
        return None

    @staticmethod
    def _is_floor_heating_telegram(telegram):
        operate_code = getattr(telegram, "operate_code", None)
        if operate_code in _FLOOR_HEATING_OPERATE_CODES:
            return True
        if operate_code not in _PANEL_OPERATE_CODES:
            return False

        payload = getattr(telegram, "payload", None)
        return (
            isinstance(payload, (bytes, bytearray, list, tuple))
            and bool(payload)
            and payload[0] in _PANEL_FLOOR_HEATING_COMMANDS
        )

    def _capture_floor_heating_telegram(self, telegram, direction):
        capture = getattr(self, "floor_heating_diagnostic_capture", None)
        if capture is None or not self._is_floor_heating_telegram(telegram):
            return

        try:
            source_aliases = (
                ["home_assistant"]
                if direction == "outgoing"
                else self._diagnostic_aliases_for(
                    getattr(telegram, "source_address", None)
                )
            )
            capture.record_telegram(
                direction=direction,
                operate_code=telegram.operate_code.name,
                source_aliases=source_aliases,
                target_aliases=self._diagnostic_aliases_for(
                    getattr(telegram, "target_address", None)
                ),
                payload=getattr(telegram, "payload", None),
            )
        except Exception:
            self.logger.debug(
                "Floor-heating diagnostic capture failed",
                exc_info=True,
            )

    async def _stop_network_interface(self):
        if self.network_interface is not None:
            await self.network_interface.stop()
            self.network_interface = None

    def register_telegram_received_all_messages_cb(self, telegram_received_cb):
        self.callback_all_messages = telegram_received_cb

    def register_telegram_received_device_cb(self, telegram_received_cb, device_address, postfix=None):
        self._telegram_received_cbs.append({
            'callback': telegram_received_cb,
            'device_address': device_address,
            'postfix': postfix})

    def unregister_telegram_received_device_cb(self, telegram_received_cb, device_address, postfix=None):
        self._telegram_received_cbs.remove({
            'callback': telegram_received_cb,
            'device_address': device_address,
            'postfix': postfix})

    @staticmethod
    async def sync():
        # await self.callback("LOG: Sync() triggered from StateUpdater")
        # print("LOG: Sync() triggered from StateUpdater")
        raise NotImplementedError
