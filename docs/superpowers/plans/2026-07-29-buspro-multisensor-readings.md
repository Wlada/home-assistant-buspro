# Buspro Multisensor Readings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore accurate temperature, illuminance, humidity, and motion readings from the installed HDL-MSOUT.4W and HDL-MSP07M.4C sensors without changing local HDL lighting logic.

**Architecture:** Keep the existing YAML-platform integration. Extend the shared Buspro sensor parser with model-aware legacy-PIR and Sensor-in-One decoding, then expose humidity through the existing Home Assistant sensor entity. Preserve entity identity by retaining existing UV-number suffixes where they are already part of a unique ID, while ensuring those suffixes never select the polling protocol.

**Tech Stack:** Python 3.14, `unittest`, Home Assistant legacy YAML entities, Voluptuous, HDL Buspro UDP telegrams.

## Global Constraints

- `HDL-MSOUT.4W` exposes temperature, illuminance, humidity, and wave/motion.
- `HDL-MSP07M.4C` exposes temperature, illuminance, and PIR motion.
- Dry contacts, UV switches, gas, and air-quality values remain unexposed.
- Local HDL brightness-and-wave entrance-light logic must not be modified.
- Illuminance is an unsigned 16-bit big-endian value: `(high << 8) | low`.
- A missing reading is unavailable; a decoded value of zero is valid.
- Existing genuinely channel-based temperature modules retain their current polling behavior.
- Existing entity IDs and unique IDs are preserved wherever possible.
- No Home Assistant restart occurs until tests, compilation, configuration validation, and user review of the final diff are complete.

---

## File Structure

- `pybuspro/helpers/enums.py`: declares the missing Sensor-in-One broadcast opcode.
- `pybuspro/devices/sensor.py`: owns telegram validation, protocol-profile selection, decoded sensor state, and read requests.
- `sensor.py`: exposes numeric Buspro measurements as Home Assistant entities.
- `tests/test_sensor_device.py`: isolated parser and read-request regression tests.
- `tests/test_sensor_platform.py`: isolated Home Assistant humidity and availability tests.
- `../../configuration.yaml`: selects the correct device profile and adds missing installed entities.

---

### Task 1: Decode legacy PIR and Sensor-in-One telegrams correctly

**Files:**
- Create: `tests/test_sensor_device.py`
- Modify: `pybuspro/helpers/enums.py:88-108`
- Modify: `pybuspro/devices/sensor.py:17-245`

**Interfaces:**
- Consumes: existing `OperateCode`, `Sensor._telegram_received_cb(telegram)`, and `Sensor.read_sensor_status()`.
- Produces: `OperateCode.BroadcastSensorsInOneStatusResponse`, `Sensor.humidity`, and correct `Sensor.brightness`, `Sensor.temperature`, and `Sensor.movement` state.

- [ ] **Step 1: Add the isolated sensor-device test harness**

Create `tests/test_sensor_device.py` with a loader that avoids importing Home Assistant:

```python
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PREFIX = "custom_components.buspro.pybuspro"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_sensor_module():
    pybuspro = types.ModuleType(MODULE_PREFIX)
    pybuspro.__path__ = []
    devices = types.ModuleType(f"{MODULE_PREFIX}.devices")
    devices.__path__ = []
    helpers = types.ModuleType(f"{MODULE_PREFIX}.helpers")
    helpers.__path__ = []
    control = types.ModuleType(f"{MODULE_PREFIX}.devices.control")
    for name in (
        "_ReadSensorStatus",
        "_ReadStatusOfUniversalSwitch",
        "_ReadStatusOfChannels",
        "_ReadFloorHeatingStatus",
        "_ReadDryContactStatus",
        "_ReadSensorsInOneStatus",
        "_ReadMotionSensorStatus",
    ):
        setattr(control, name, type(name, (), {}))
    device = types.ModuleType(f"{MODULE_PREFIX}.devices.device")
    device.Device = object

    sys.modules.update(
        {
            MODULE_PREFIX: pybuspro,
            f"{MODULE_PREFIX}.devices": devices,
            f"{MODULE_PREFIX}.helpers": helpers,
            f"{MODULE_PREFIX}.devices.control": control,
            f"{MODULE_PREFIX}.devices.device": device,
        }
    )
    enums = _load_module(
        f"{MODULE_PREFIX}.helpers.enums",
        COMPONENT_ROOT / "pybuspro" / "helpers" / "enums.py",
    )
    sensor_module = _load_module(
        f"{MODULE_PREFIX}.devices.sensor",
        COMPONENT_ROOT / "pybuspro" / "devices" / "sensor.py",
    )
    return sensor_module, enums.OperateCode


SensorModule, OperateCode = _load_sensor_module()
Sensor = SensorModule.Sensor


def make_sensor(device):
    sensor = Sensor.__new__(Sensor)
    sensor._device = device
    sensor._device_address = (10, 20)
    sensor._current_temperature = None
    sensor._brightness = None
    sensor._humidity = None
    sensor._motion_sensor = None
    sensor._sonic = None
    sensor._dry_contact_1_status = None
    sensor._dry_contact_2_status = None
    sensor._universal_switch_number = None
    sensor._channel_number = None
    sensor._switch_number = None
    sensor._call_device_updated = Mock()
    return sensor
```

- [ ] **Step 2: Write failing tests for the missing opcode and Sensor-in-One reads**

Append:

```python
class SensorTelegramTests(unittest.TestCase):
    def test_sensor_in_one_broadcast_opcode_is_known(self):
        self.assertIn(b"\x16\x30", {item.value for item in OperateCode})

    def test_sensor_in_one_read_decodes_all_exposed_measurements(self):
        sensor = make_sensor("sensors_in_one")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                payload=[0xF8, 29, 0x03, 0xE8, 64, 0, 0, 1, 0, 0],
            )
        )

        self.assertEqual(sensor.temperature, 29)
        self.assertEqual(sensor.brightness, 1000)
        self.assertEqual(sensor.humidity, 64)
        self.assertIs(sensor.movement, True)
        sensor._call_device_updated.assert_called_once_with()
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorTelegramTests -v
```

Expected: failures because `0x1630` is absent, Sensor-in-One does not set brightness or humidity, and `Sensor.humidity` does not exist.

- [ ] **Step 4: Add the missing opcode and minimal Sensor-in-One read decoding**

In `pybuspro/helpers/enums.py`, add:

```python
BroadcastSensorsInOneStatusResponse = b"\x16\x30"
```

In `Sensor.__init__`, initialize:

```python
self._humidity = None
```

In `Sensor`, add:

```python
@staticmethod
def _decode_uint16_be(high_byte, low_byte):
    return (high_byte << 8) | low_byte

@property
def humidity(self):
    return self._humidity
```

Replace the `ReadSensorsInOneStatusResponse` branch with guarded decoding:

```python
elif telegram.operate_code == OperateCode.ReadSensorsInOneStatusResponse:
    payload = telegram.payload
    if not isinstance(payload, (list, tuple)) or len(payload) < 8:
        return
    self._current_temperature = payload[1]
    self._brightness = self._decode_uint16_be(payload[2], payload[3])
    self._humidity = payload[4]
    self._motion_sensor = payload[7]
    self._dry_contact_1_status = payload[8] if len(payload) > 8 else None
    self._dry_contact_2_status = payload[9] if len(payload) > 9 else None
    self._call_device_updated()
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorTelegramTests.test_sensor_in_one_broadcast_opcode_is_known tests.test_sensor_device.SensorTelegramTests.test_sensor_in_one_read_decodes_all_exposed_measurements -v
```

Expected: two tests pass.

- [ ] **Step 6: Write failing tests for Sensor-in-One broadcast, legacy PIR, zero lux, and malformed payloads**

Append:

```python
    def test_sensor_in_one_broadcast_decodes_exposed_measurements(self):
        sensor = make_sensor("sensors_in_one")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.BroadcastSensorsInOneStatusResponse,
                payload=[0xF8, 27, 0x07, 0xD0, 71, 0, 0, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.temperature, 27)
        self.assertEqual(sensor.brightness, 2000)
        self.assertEqual(sensor.humidity, 71)
        self.assertIs(sensor.movement, False)

    def test_legacy_pir_read_uses_integer_success_and_16_bit_lux(self):
        sensor = make_sensor("pir")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorStatusResponse,
                payload=[0xF8, 49, 0x04, 0xB0, 1, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.temperature, 49)
        self.assertEqual(sensor.brightness, 1200)
        self.assertIs(sensor.movement, True)

    def test_zero_lux_is_preserved_as_valid_data(self):
        sensor = make_sensor("pir")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.BroadcastSensorStatusResponse,
                payload=[49, 0, 0, 0, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.brightness, 0)

    def test_short_payload_does_not_erase_last_valid_values(self):
        sensor = make_sensor("sensors_in_one")
        sensor._current_temperature = 25
        sensor._brightness = 900
        sensor._humidity = 55

        try:
            sensor._telegram_received_cb(
                SimpleNamespace(
                    operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                    payload=[0xF8, 30],
                )
            )
        except Exception as error:
            self.fail(f"short payload raised {error!r}")

        self.assertEqual(sensor.temperature, 25)
        self.assertEqual(sensor.brightness, 900)
        self.assertEqual(sensor.humidity, 55)
        sensor._call_device_updated.assert_not_called()
```

- [ ] **Step 7: Run the new tests and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorTelegramTests -v
```

Expected: Sensor-in-One broadcast is ignored, legacy lux is not decoded as 1200, or short legacy/broadcast paths lack complete validation.

- [ ] **Step 8: Implement guarded decoding for all response families**

In `_telegram_received_cb`:

- normalize `payload = telegram.payload`;
- require list/tuple payloads of the exact minimum length before indexing;
- compare the legacy success byte with `SuccessOrFailure.Success.value[0]`;
- use `_decode_uint16_be` in `ReadSensorStatusResponse`,
  `BroadcastSensorStatusResponse`, and
  `BroadcastSensorStatusAutoResponse`;
- add a `BroadcastSensorsInOneStatusResponse` branch with the same field
  mapping as `ReadSensorsInOneStatusResponse`;
- call `_call_device_updated()` once only after a valid telegram has updated
  state.

Change the brightness property from:

```python
return self._brightness or 0
```

to:

```python
return self._brightness
```

This preserves real zero while keeping missing data as `None`.

- [ ] **Step 9: Write a failing read-request profile test**

Append this async test:

```python
class SensorReadRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_uv_suffix_does_not_override_pir_or_sensor_in_one_profile(self):
        created = []
        request_names = (
            "_ReadSensorStatus",
            "_ReadStatusOfUniversalSwitch",
            "_ReadStatusOfChannels",
            "_ReadFloorHeatingStatus",
            "_ReadDryContactStatus",
            "_ReadSensorsInOneStatus",
        )
        originals = {
            name: getattr(SensorModule, name) for name in request_names
        }

        def request_class(name):
            class Request:
                def __init__(self, buspro):
                    self.subnet_id = None
                    self.device_id = None
                    self.switch_number = None
                    created.append(name)

                async def send(self):
                    return None

            Request.__name__ = name
            return Request

        try:
            for name in request_names:
                setattr(SensorModule, name, request_class(name))
            for device, suffix, expected_request in (
                ("pir", 254, "_ReadSensorStatus"),
                ("sensors_in_one", 255, "_ReadSensorsInOneStatus"),
            ):
                with self.subTest(device=device):
                    created.clear()
                    sensor = make_sensor(device)
                    sensor._buspro = object()
                    sensor._channel_number = suffix
                    await sensor.read_sensor_status()
                    self.assertEqual(created, [expected_request])
        finally:
            for name, request in originals.items():
                setattr(SensorModule, name, request)
```

- [ ] **Step 10: Run the profile test and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorReadRequestTests.test_uv_suffix_does_not_override_pir_or_sensor_in_one_profile -v
```

Expected: the existing channel-number branch selects `_ReadStatusOfChannels`.

- [ ] **Step 11: Make protocol profile selection precede UV suffix handling**

In `read_sensor_status()` use this decision order:

```python
if self._universal_switch_number is not None:
    ...
elif self._device == "pir":
    request = _ReadSensorStatus(self._buspro)
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
...
```

Set `request.subnet_id` and `request.device_id` from `_device_address`, then
await `request.send()`. This keeps low numbered real channels on their current
path while preventing UV identifiers from becoming measurement channels.

- [ ] **Step 12: Run all parser tests and existing tests**

Run:

```powershell
python -m unittest tests.test_sensor_device -v
python -m unittest discover -s tests -v
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 13: Commit the parser change**

```powershell
git add tests/test_sensor_device.py pybuspro/helpers/enums.py pybuspro/devices/sensor.py
git commit -m "fix: decode Buspro multisensor readings"
```

---

### Task 2: Expose humidity and correct numeric availability

**Files:**
- Create: `tests/test_sensor_platform.py`
- Modify: `sensor.py:15-202`

**Interfaces:**
- Consumes: `Sensor.humidity`, `Sensor.brightness`, and the existing `BusproSensor` callback.
- Produces: YAML `type: humidity` support and a Home Assistant humidity measurement entity.

- [ ] **Step 1: Create Home Assistant platform stubs and failing tests**

Create `tests/test_sensor_platform.py`. Install lightweight stubs for
`homeassistant.components.sensor.PLATFORM_SCHEMA`,
`homeassistant.const`, `homeassistant.helpers.config_validation`, and
`homeassistant.helpers.entity.Entity`, then load the repository `sensor.py`
with `importlib.util`.

The stubs must define the constants used by the module as their lowercase YAML
names, `PERCENTAGE = "%"`, and an `Entity` with a no-op
`async_write_ha_state()`.

Add:

```python
class BusproSensorPlatformTests(unittest.TestCase):
    def make_entity(self, sensor_type):
        device = SimpleNamespace(
            name="Test sensor",
            device_identifier="test-device",
            temperature=25,
            brightness=None,
            humidity=None,
        )
        entity = BusproSensor(device, sensor_type, 30, 0)
        entity.hass = SimpleNamespace(
            data={DATA_BUSPRO: SimpleNamespace(connected=True)}
        )
        return entity

    def test_humidity_metadata_and_state(self):
        entity = self.make_entity("humidity")
        entity._humidity = 63

        self.assertEqual(entity.state, 63)
        self.assertEqual(entity.device_class, "humidity")
        self.assertEqual(entity.unit_of_measurement, "%")
        self.assertTrue(entity.available)

    def test_missing_lux_and_humidity_are_unavailable_but_zero_lux_is_valid(self):
        lux = self.make_entity("illuminance")
        humidity = self.make_entity("humidity")

        self.assertFalse(lux.available)
        self.assertFalse(humidity.available)

        lux._brightness = 0
        self.assertTrue(lux.available)
        self.assertEqual(lux.state, 0)

    def test_update_callback_copies_humidity(self):
        entity = self.make_entity("humidity")
        entity._device.humidity = 58

        asyncio.run(entity.after_update_callback(entity._device))

        self.assertEqual(entity.state, 58)
```

- [ ] **Step 2: Run the platform tests and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_platform -v
```

Expected: humidity is not an allowed type, has no state/metadata branch, and
the update callback does not copy humidity.

- [ ] **Step 3: Add minimal humidity entity support**

In repository `sensor.py`:

```python
HUMIDITY = "humidity"
```

Add `HUMIDITY` to `SENSOR_TYPES`. Initialize `self._humidity = None` in
`BusproSensor.__init__`, copy `self._device.humidity` in
`after_update_callback`, and add exact `HUMIDITY` branches to:

```python
available
state
device_class
unit_of_measurement
```

The branches return `self._humidity`, `"humidity"`, and `"%"` respectively.
Do not infer availability from truthiness; `0` is valid.

- [ ] **Step 4: Run platform and full tests**

Run:

```powershell
python -m unittest tests.test_sensor_platform -v
python -m unittest discover -s tests -v
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 5: Commit humidity platform support**

```powershell
git add tests/test_sensor_platform.py sensor.py
git commit -m "feat: expose Buspro humidity readings"
```

---

### Task 3: Align installed YAML profiles and add missing readings

**Files:**
- Modify: `../../configuration.yaml:125-164`
- Modify: `../../configuration.yaml:335-361`

**Interfaces:**
- Consumes: `device: pir`, `device: sensors_in_one`, and `type: humidity`.
- Produces: installed entities for every requested physical measurement while retaining existing entity names.

- [ ] **Step 1: Save the exact pre-change configuration excerpt**

Run:

```powershell
git diff -- ../../configuration.yaml
```

If the file is not tracked by the Buspro repository, capture only the
`sensor: - platform: buspro` and `binary_sensor: - platform: buspro` excerpts
for later diff review. Do not read or include secrets or unrelated YAML.

- [ ] **Step 2: Update only the installed multisensor entries**

Apply these exact semantic changes:

- Keep the existing Outdoor temperature and Outdoor LUX names and addresses.
- Add `Outdoor humidity`, base outdoor device address,
  `device: "sensors_in_one"`, `type: humidity`, `device_class: humidity`, and
  `scan_interval: 30`.
- Change Basement, Closet, and Stairs temperature profiles from
  `sensors_in_one` to `pir`; retain their names, addresses, offsets, and scan
  intervals.
- Add `Basement LUX` and `Closet LUX` entries using their existing physical
  device addresses with UV suffix `254`, `device: "pir"`,
  `type: illuminance`, `device_class: illuminance`, and `scan_interval: 10`.
- Change the existing Stairs LUX profile to `pir` while retaining its name,
  UV suffix, and interval.
- Keep Basement, Closet, and Stairs motion entity names and addresses on
  `device: "pir"`.
- Change Outdoor Motion to `device: "sensors_in_one"` while retaining its
  existing name and address so its entity identity is stable.

Do not add dry-contact, UV-switch, air-quality, or gas entities.

- [ ] **Step 3: Validate YAML syntax without restarting Home Assistant**

Use the Home Assistant host command:

```sh
ha core check
```

Expected: `Command completed successfully`.

If the HA CLI is not reachable from this workspace, run the equivalent
Supervisor “Check configuration” action and capture its success result before
proceeding. A generic YAML parser is not a substitute for Home Assistant
schema validation.

- [ ] **Step 4: Review the focused installed-config diff**

Confirm the diff contains only:

- three PIR profile corrections for temperature;
- one PIR profile correction for existing Stairs LUX;
- two new indoor PIR lux entities;
- one new outdoor humidity entity;
- one Sensor-in-One profile correction for Outdoor Motion.

Confirm that no automation, local HDL logic, IP configuration, unrelated
entity, or dry-contact setting changed.

- [ ] **Step 5: Commit only if configuration belongs to the same repository**

The Home Assistant `configuration.yaml` is outside the nested Buspro Git
repository. Do not force-add it to the Buspro repository. Leave the focused
live configuration change uncommitted unless a containing configuration
repository is discovered and already tracks the file.

---

### Task 4: Final offline verification and restart handoff

**Files:**
- Verify: `pybuspro/helpers/enums.py`
- Verify: `pybuspro/devices/sensor.py`
- Verify: `sensor.py`
- Verify: `tests/test_sensor_device.py`
- Verify: `tests/test_sensor_platform.py`
- Verify: `../../configuration.yaml`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: evidence that the code and configuration are ready for a
  user-approved Home Assistant restart.

- [ ] **Step 1: Run the complete unit-test suite**

```powershell
python -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Compile all changed Python files**

```powershell
python -m py_compile pybuspro/helpers/enums.py pybuspro/devices/sensor.py sensor.py tests/test_sensor_device.py tests/test_sensor_platform.py
```

Expected: exit code `0` with no output.

- [ ] **Step 3: Re-run Home Assistant configuration validation**

```sh
ha core check
```

Expected: `Command completed successfully`.

- [ ] **Step 4: Inspect repository and live-config diffs**

```powershell
git status --short --branch
git diff --check
git log --oneline -5
```

Verify the parser and platform commits are present, there are no unintended
repository changes, and the only live YAML edits are those listed in Task 3.

- [ ] **Step 5: Present evidence and request restart approval**

Report:

- exact unit-test count and result;
- compilation result;
- HA configuration-check result;
- changed entities and preserved entity names;
- exact commits created;
- that local HDL entrance-light logic was not touched.

Do not restart Home Assistant in this step.

- [ ] **Step 6: After explicit approval, restart and verify runtime state**

Restart Home Assistant, then verify:

- Outdoor temperature, lux, humidity, and motion update from one physical
  MSOUT telegram family.
- Basement, Closet, and Stairs temperature, lux, and motion update from their
  legacy PIR telegram family.
- Lux values above 510 are represented correctly.
- Real zero lux is represented as `0`, not unavailable.
- The local entrance light still responds to brightness and wave presence
  without depending on Home Assistant.

If any runtime field is wrong, capture only the relevant Buspro opcode and
payload bytes, add a failing regression test, and return to Task 1 before
changing the parser.
