"""
Support for Buspro devices.

For more details about this component, please refer to the documentation at
https://home-assistant.io/...
"""

import asyncio
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import (
    CONF_HOST, 
    CONF_PORT, 
    CONF_NAME,
)
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

DOMAIN = "buspro"
DATA_BUSPRO = "buspro"
DATA_BUSPRO_SETUP_LOCK = "buspro_setup_lock"
DEPENDENCIES = []

DEFAULT_CONF_NAME = ""

DEFAULT_SCENE_NAME = "BUSPRO SCENE"
DEFAULT_SEND_MESSAGE_NAME = "BUSPRO MESSAGE"

SERVICE_BUSPRO_SEND_MESSAGE = "send_message"
SERVICE_BUSPRO_ACTIVATE_SCENE = "activate_scene"
SERVICE_BUSPRO_UNIVERSAL_SWITCH = "set_universal_switch"
SERVICE_BUSPRO_SET_PANEL_AC = "set_panel_ac"

SERVICE_BUSPRO_ATTR_OPERATE_CODE = "operate_code"
SERVICE_BUSPRO_ATTR_ADDRESS = "address"
SERVICE_BUSPRO_ATTR_PAYLOAD = "payload"
SERVICE_BUSPRO_ATTR_SCENE_ADDRESS = "scene_address"
SERVICE_BUSPRO_ATTR_SWITCH_NUMBER = "switch_number"
SERVICE_BUSPRO_ATTR_STATUS = "status"
SERVICE_BUSPRO_ATTR_CHANNEL = "channel"
SERVICE_BUSPRO_ATTR_POWER = "power"


def _validate_buspro_address(value):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(byte) is not int or not 0 <= byte <= 255 for byte in value)
    ):
        raise vol.Invalid("address must contain exactly two BusPro bytes")
    return value


def _validate_positive_int(value):
    if type(value) is not int or value < 1:
        raise vol.Invalid("value must be a positive integer")
    return value


def _validate_panel_ac_power(value):
    if type(value) is not int or value not in (0, 1):
        raise vol.Invalid("power must be 0 or 1")
    return value


def _validate_universal_switch_status(value):
    if type(value) is not int or value not in (0, 1):
        raise vol.Invalid("status must be 0 or 1")
    return value


def _validate_operate_code(value):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(byte) is not int or not 0 <= byte <= 255 for byte in value)
    ):
        raise vol.Invalid("operate_code must contain exactly two bytes")

    from .pybuspro.helpers.enums import OperateCode

    try:
        return OperateCode(bytes(value))
    except ValueError as error:
        raise vol.Invalid("operate_code is not recognized") from error

"""{ "address": [1,74], "scene_address": [3,5] }"""
SERVICE_BUSPRO_ACTIVATE_SCENE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_SCENE_ADDRESS): vol.Any([cv.positive_int]),
})

"""{ "address": [1,74], "operate_code": [4,12], "payload": [1,75,0,3] }"""
SERVICE_BUSPRO_SEND_MESSAGE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_OPERATE_CODE): _validate_operate_code,
    vol.Required(SERVICE_BUSPRO_ATTR_PAYLOAD): vol.Any([cv.positive_int]),
})

"""{ "address": [1,100], "switch_number": 100, "status": 1 }"""
SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_SWITCH_NUMBER): vol.Any(cv.positive_int),
    vol.Required(
        SERVICE_BUSPRO_ATTR_STATUS
    ): _validate_universal_switch_status,
})

SERVICE_BUSPRO_SET_PANEL_AC_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): _validate_buspro_address,
    vol.Required(SERVICE_BUSPRO_ATTR_CHANNEL): _validate_positive_int,
    vol.Required(SERVICE_BUSPRO_ATTR_POWER): _validate_panel_ac_power,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PORT): cv.port,
        vol.Optional(CONF_NAME, default=DEFAULT_CONF_NAME): cv.string
    })
}, extra=vol.ALLOW_EXTRA)

async def async_setup(hass: HomeAssistant, config: dict):
    """Setup the Buspro component. """
    if DOMAIN not in config:
        return True

    host = config[DOMAIN][CONF_HOST]
    port = config[DOMAIN][CONF_PORT]
    _LOGGER.warning(
        "BusPro YAML setup entered host=%s port=%s module=%s",
        host,
        port,
        __file__,
    )

    return await _async_setup_buspro(hass, host, port)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Setup the Buspro component. """
    host = config_entry.data.get(CONF_HOST, "")
    port = config_entry.data.get(CONF_PORT, 1)
    _LOGGER.warning(
        "BusPro config-entry setup entered host=%s port=%s module=%s",
        host,
        port,
        __file__,
    )

    return await _async_setup_buspro(hass, host, port)


async def _async_setup_buspro(hass: HomeAssistant, host: str, port: int) -> bool:
    """Start one shared BusPro gateway for YAML and config-entry setup."""
    endpoint = (host, port)
    setup_lock = hass.data.setdefault(DATA_BUSPRO_SETUP_LOCK, asyncio.Lock())
    async with setup_lock:
        existing = hass.data.get(DATA_BUSPRO)
        if existing is not None:
            existing_endpoint = existing.gateway_address_send_receive[0]
            if existing_endpoint != endpoint:
                _LOGGER.error(
                    "BusPro is already configured for %s; refusing duplicate %s",
                    existing_endpoint,
                    endpoint,
                )
                return False

            _LOGGER.info("Reusing existing BusPro gateway for %s", endpoint)
            return True

        module = BusproModule(hass, host, port)
        try:
            await module.start()
        except BaseException:
            try:
                await module.stop(None)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Failed to clean up partial BusPro startup")
            raise
        module.register_services()
        hass.data[DATA_BUSPRO] = module

    return True

class BusproModule:
    """Representation of Buspro Object."""

    def __init__(self, hass, host, port):
        """Initialize of Buspro module."""
        self.hass = hass
        self.connected = False
        self.hdl = None
        self.gateway_address_send_receive = ((host, port), ('', port))
        self.init_hdl()

    def init_hdl(self):
        """Initialize of Buspro object."""
        # noinspection PyUnresolvedReferences
        from .pybuspro.buspro import Buspro
        from .pybuspro.sensor_diagnostics import SensorDiagnosticCapture
        from .pybuspro.floor_heating_diagnostics import FloorHeatingDiagnosticCapture

        self.hdl = Buspro(self.gateway_address_send_receive, self.hass.loop)
        self.hdl.sensor_diagnostic_capture = SensorDiagnosticCapture(
            self.hass.config.path("buspro_sensor_capture.jsonl")
        )
        self.hdl.floor_heating_diagnostic_capture = FloorHeatingDiagnosticCapture(
            self.hass.config.path("buspro_floor_heating_capture.jsonl")
        )
        # self.hdl.register_telegram_received_all_messages_cb(self.telegram_received_cb)

    async def start(self):
        """Start Buspro object. Connect to tunneling device."""
        await self.hdl.start(state_updater=False)
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.stop)
        self.connected = True

    # noinspection PyUnusedLocal
    async def stop(self, event):
        """Stop Buspro object. Disconnect from tunneling device."""
        transport_error = None
        try:
            await self.hdl.stop()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            transport_error = error
        finally:
            captures = (
                getattr(self.hdl, "sensor_diagnostic_capture", None),
                getattr(self.hdl, "floor_heating_diagnostic_capture", None),
            )
            close_tasks = [
                capture.async_close()
                for capture in captures
                if callable(getattr(capture, "async_close", None))
            ]
            if close_tasks:
                results = await asyncio.gather(
                    *close_tasks, return_exceptions=True
                )
                for result in results:
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    if isinstance(result, Exception):
                        _LOGGER.warning(
                            "Failed to flush BusPro diagnostics during stop: %s",
                            result,
                        )
            self.connected = False

        if transport_error is not None:
            raise transport_error

    async def service_activate_scene(self, call):
        """Service for activatign a __scene"""
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.scene import Scene

        attr_address = call.data.get(SERVICE_BUSPRO_ATTR_ADDRESS)
        attr_scene_address = call.data.get(SERVICE_BUSPRO_ATTR_SCENE_ADDRESS)
        scene = Scene(self.hdl, attr_address, attr_scene_address, DEFAULT_SCENE_NAME)
        await scene.run()

    async def service_send_message(self, call):
        """Service for send an arbitrary message"""
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.generic import Generic

        attr_address = call.data.get(SERVICE_BUSPRO_ATTR_ADDRESS)
        attr_payload = call.data.get(SERVICE_BUSPRO_ATTR_PAYLOAD)
        attr_operate_code = call.data.get(SERVICE_BUSPRO_ATTR_OPERATE_CODE)
        generic = Generic(self.hdl, attr_address, attr_payload, attr_operate_code, DEFAULT_SEND_MESSAGE_NAME)
        await generic.run()

    async def service_set_universal_switch(self, call):
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.universal_switch import UniversalSwitch

        attr_address = call.data.get(SERVICE_BUSPRO_ATTR_ADDRESS)
        attr_switch_number = call.data.get(SERVICE_BUSPRO_ATTR_SWITCH_NUMBER)
        universal_switch = UniversalSwitch(self.hdl, attr_address, attr_switch_number)

        status = call.data.get(SERVICE_BUSPRO_ATTR_STATUS)
        if status == 1:
            await universal_switch.set_on()
        else:
            await universal_switch.set_off()

    async def service_set_panel_ac(self, call):
        """Set an Enviro panel AC channel power state."""
        from .pybuspro.devices.generic import Generic
        from .pybuspro.helpers.enums import OperateCode

        address = call.data[SERVICE_BUSPRO_ATTR_ADDRESS]
        channel = call.data[SERVICE_BUSPRO_ATTR_CHANNEL]
        power = call.data[SERVICE_BUSPRO_ATTR_POWER]

        _LOGGER.debug(
            "Set panel AC power address=%s channel=%s power=%s",
            address,
            channel,
            power,
        )
        generic = Generic(
            self.hdl,
            address,
            [3, power, channel],
            OperateCode.ControlPanelAC,
            DEFAULT_SEND_MESSAGE_NAME,
        )
        await generic.run()

    def register_services(self):

        """ activate_scene """
        self.hass.services.async_register(
            DOMAIN, SERVICE_BUSPRO_ACTIVATE_SCENE,
            self.service_activate_scene,
            schema=SERVICE_BUSPRO_ACTIVATE_SCENE_SCHEMA)

        """ send_message """
        self.hass.services.async_register(
            DOMAIN, SERVICE_BUSPRO_SEND_MESSAGE,
            self.service_send_message,
            schema=SERVICE_BUSPRO_SEND_MESSAGE_SCHEMA)

        """ universal_switch """
        self.hass.services.async_register(
            DOMAIN, SERVICE_BUSPRO_UNIVERSAL_SWITCH,
            self.service_set_universal_switch,
            schema=SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA)

        """ panel_ac """
        self.hass.services.async_register(
            DOMAIN, SERVICE_BUSPRO_SET_PANEL_AC,
            self.service_set_panel_ac,
            schema=SERVICE_BUSPRO_SET_PANEL_AC_SCHEMA)

    '''
    def telegram_received_cb(self, telegram):
        #     """Call invoked after a KNX telegram was received."""
        #     self.hass.bus.fire('knx_event', {
        #         'address': str(telegram.group_address),
        #         'data': telegram.payload.value
        #     })
        # _LOGGER.info(f"Callback: '{telegram}'")
        return False
    '''
