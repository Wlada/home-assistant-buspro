# BusPro Enviro AC Power Switch MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Home Assistant power switch for an Enviro panel AC channel whose state changes only after a matching panel response.

**Architecture:** A new protocol-level `PanelACDevice` owns typed E3D8 sends and filtered E3D9 state updates. The existing switch platform selects relay or panel AC devices from YAML and wraps AC devices in a thin `BusproPanelACSwitch`, leaving floor heating and existing relay behavior unchanged.

**Tech Stack:** Python 3.12+, asyncio, Home Assistant `SwitchEntity`, voluptuous, unittest, existing embedded pybuspro transport.

## Global Constraints

- The Enviro panel is the only source of truth.
- ON sends `OperateCode.ControlPanelAC` (`E3D8`) with `[3, 1, channel]`.
- OFF sends `OperateCode.ControlPanelAC` (`E3D8`) with `[3, 0, channel]`.
- State changes only after matching `ControlPanelACResponse` (`E3D9`).
- Initial state is `None`/unknown; do not send an unverified status-read command.
- Do not use optimistic state.
- Do not modify floor-heating climate behavior.
- Do not send universal-switch commands to the IR emitter.
- Missing `device_type` preserves existing relay behavior.
- The protocol model must remain reusable by a future AC `ClimateEntity`.

---

### Task 1: Protocol-level Enviro AC device

**Files:**
- Create: `pybuspro/devices/panel_ac.py`
- Create: `tests/test_panel_ac_device.py`

**Interfaces:**
- Consumes: `Device`, `Generic`, `OperateCode.ControlPanelAC`, and `OperateCode.ControlPanelACResponse`.
- Produces: `PanelACDevice(buspro, device_address: tuple[int, int], channel: int, name: str = "")` with async `set_on()`, async `set_off()`, `is_on -> bool | None`, and `device_identifier -> str`.
- State callback contract: valid E3D9 payload `[3, power, channel]` stores confirmed state and calls the inherited `_call_device_updated()` exactly once.

- [ ] **Step 1: Write failing protocol tests**

Create `tests/test_panel_ac_device.py` using `unittest`. Load the production module with test doubles only at the transport boundary. Cover these literal behaviors:

```python
class PanelACDeviceTests(unittest.IsolatedAsyncioTestCase):
    def test_initial_state_is_unknown(self):
        device = make_device(channel=1)
        self.assertIsNone(device.is_on)

    async def test_on_sends_typed_panel_command_without_optimistic_state(self):
        device, sent = make_device_with_captured_generic(channel=1)
        await device.set_on()
        self.assertEqual(sent, [([1, 49], [3, 1, 1], OperateCode.ControlPanelAC)])
        self.assertIsNone(device.is_on)

    async def test_off_sends_typed_panel_command_without_optimistic_state(self):
        device, sent = make_device_with_captured_generic(channel=1)
        await device.set_off()
        self.assertEqual(sent, [([1, 49], [3, 0, 1], OperateCode.ControlPanelAC)])
        self.assertIsNone(device.is_on)
```

Add table-driven response tests using a Telegram-shaped object:

```python
valid_cases = ((0, False), (1, True))
for power, expected in valid_cases:
    device._telegram_received_cb(
        SimpleNamespace(
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[3, power, 1],
        )
    )
    self.assertIs(device.is_on, expected)
```

Verify no update for another opcode, another field, another channel, invalid power, and payloads of lengths 0, 1, and 2. Verify `device_identifier == "panel-ac-1-49-1"`.

- [ ] **Step 2: Run protocol tests and verify RED**

Run:

```powershell
python tests\test_panel_ac_device.py -v
```

Expected: import/file failure because `pybuspro/devices/panel_ac.py` and `PanelACDevice` do not exist.

- [ ] **Step 3: Implement the minimal protocol device**

Create `pybuspro/devices/panel_ac.py` with this structure:

```python
import logging

from .device import Device
from .generic import Generic
from ..helpers.enums import OperateCode

_LOGGER = logging.getLogger(__name__)
_POWER_FIELD = 3


class PanelACDevice(Device):
    def __init__(self, buspro, device_address, channel, name=""):
        super().__init__(buspro, device_address, name)
        self._channel = channel
        self._is_on = None
        self.register_telegram_received_cb(self._telegram_received_cb)

    async def set_on(self):
        await self._set_power(1)

    async def set_off(self):
        await self._set_power(0)

    async def _set_power(self, power):
        _LOGGER.debug(
            "Set panel AC power address=%s channel=%s power=%s",
            self._device_address,
            self._channel,
            power,
        )
        command = Generic(
            self._buspro,
            self._device_address,
            [_POWER_FIELD, power, self._channel],
            OperateCode.ControlPanelAC,
        )
        await command.run()

    def _telegram_received_cb(self, telegram):
        if telegram.operate_code != OperateCode.ControlPanelACResponse:
            return
        payload = telegram.payload
        if not isinstance(payload, (list, tuple)) or len(payload) < 3:
            _LOGGER.debug("Ignore malformed panel AC power response")
            return
        field, power, channel = payload[:3]
        if field != _POWER_FIELD or power not in (0, 1) or channel != self._channel:
            return
        self._is_on = bool(power)
        self._call_device_updated()

    @property
    def is_on(self):
        return self._is_on

    @property
    def device_identifier(self):
        subnet, device = self._device_address
        return f"panel-ac-{subnet}-{device}-{self._channel}"
```

Do not add temperature, modes, fan, polling, or read-status code.

- [ ] **Step 4: Run protocol tests and verify GREEN**

Run:

```powershell
python tests\test_panel_ac_device.py -v
```

Expected: all protocol tests pass with no warnings or errors.

- [ ] **Step 5: Commit the protocol unit**

```powershell
git add pybuspro/devices/panel_ac.py tests/test_panel_ac_device.py
git commit -m "Add Enviro panel AC protocol device"
```

---

### Task 2: Home Assistant switch platform integration

**Files:**
- Modify: `switch.py`
- Create: `tests/test_panel_ac_switch.py`
- No service-definition files change in this task.

**Interfaces:**
- Consumes: `PanelACDevice` from Task 1 and the existing `BusproSwitch`/relay `Switch` behavior.
- Produces: `device_type` configuration with exact values `relay` and `panel_ac`, plus `BusproPanelACSwitch(BusproSwitch)`.
- Configuration address contract: exactly `subnet.device.channel`; subnet and device are integers from 0 through 255; channel is an integer from 1 through 255.

- [ ] **Step 1: Write failing schema and factory tests**

Create `tests/test_panel_ac_switch.py` with Home Assistant module stubs and real voluptuous validation. Test the production `switch.py` boundary:

```python
def test_missing_device_type_defaults_to_relay():
    validated = buspro_switch.PLATFORM_SCHEMA(
        {"devices": {"1.49.1": {"name": "Existing relay"}}}
    )
    self.assertEqual(validated["devices"]["1.49.1"]["device_type"], "relay")


def test_panel_ac_type_is_accepted():
    validated = buspro_switch.PLATFORM_SCHEMA(
        {"devices": {"1.49.1": {"name": "Klima", "device_type": "panel_ac"}}}
    )
    self.assertEqual(validated["devices"]["1.49.1"]["device_type"], "panel_ac")
```

Reject `device_type: unknown`, addresses with two or four parts, non-integer parts, subnet/device outside 0..255, and channel outside 1..255.

Call `async_setup_platform` with captured `async_add_entities` and injected relay/AC device classes. Verify:

- missing or explicit `relay` constructs the existing relay `Switch` and `BusproSwitch`;
- `panel_ac` constructs `PanelACDevice` with address `(1, 49)`, channel `1`, and name, then wraps it in `BusproPanelACSwitch`;
- one configured item produces exactly one entity.

- [ ] **Step 2: Run switch tests and verify RED**

Run:

```powershell
python tests\test_panel_ac_switch.py -v
```

Expected: failures because `device_type`, address validation, AC factory selection, and `BusproPanelACSwitch` do not exist.

- [ ] **Step 3: Implement strict configuration and entity selection**

In `switch.py`, add:

```python
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_RELAY = "relay"
DEVICE_TYPE_PANEL_AC = "panel_ac"
DEVICE_TYPES = (DEVICE_TYPE_RELAY, DEVICE_TYPE_PANEL_AC)


def _validate_device_address(value):
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
```

Extend the device schema:

```python
DEVICE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_DEVICE_TYPE, default=DEVICE_TYPE_RELAY): vol.In(DEVICE_TYPES),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES): {_validate_device_address: DEVICE_SCHEMA},
})
```

In `async_setup_platform`, parse the validated address once. Select the device and entity:

```python
if device_config[CONF_DEVICE_TYPE] == DEVICE_TYPE_PANEL_AC:
    from .pybuspro.devices.panel_ac import PanelACDevice
    device = PanelACDevice(hdl, device_address, channel_number, name)
    entity = BusproPanelACSwitch(hass, device)
else:
    device = Switch(hdl, device_address, channel_number, name)
    entity = BusproSwitch(hass, device)
devices.append(entity)
```

Add the explicit adapter class without duplicating inherited switch behavior:

```python
class BusproPanelACSwitch(BusproSwitch):
    """Enviro panel AC power switch backed by confirmed panel state."""
```

Do not write state after `async_turn_on` or `async_turn_off`; inherited delegation already waits for device callbacks.

- [ ] **Step 4: Run switch and regression tests**

Run:

```powershell
python tests\test_panel_ac_switch.py -v
python tests\test_panel_ac_device.py -v
python tests\test_panel_ac_service.py -v
python -m compileall -q .
```

Expected: all tests pass; compileall exits 0; no warnings or errors.

- [ ] **Step 5: Verify scope and manual configuration**

Verify that only the protocol device, switch platform, tests, and plan/spec files changed. Confirm `climate.py` and `pybuspro/devices/climate.py` are byte-for-byte unchanged from the branch base.

Add the approved YAML entry to the live Home Assistant configuration only after the code tests pass:

```yaml
switch:
  - platform: buspro
    devices:
      "1.49.1":
        name: Klima
        device_type: panel_ac
```

If a `switch:` BusPro block already exists, add the new device to its existing `devices` mapping instead of creating a duplicate platform block.

- [ ] **Step 6: Commit and push the HA integration unit**

```powershell
git add switch.py tests/test_panel_ac_switch.py
git commit -m "Add Enviro panel AC power switch"
git push
```

Do not tag or merge until Home Assistant restart, ON, OFF, and panel-originated state changes are manually verified.