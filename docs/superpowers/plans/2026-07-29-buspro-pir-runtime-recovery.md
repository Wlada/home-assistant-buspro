# Buspro PIR Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore indoor PIR motion updates, expose startup motion availability honestly, correct Buspro temperature decoding, and add a privacy-scoped runtime capture for the still-unknown indoor measurement response.

**Architecture:** Keep the existing YAML-platform integration and split polling intent from the broad device label by adding a `request_profile` to the shared `Sensor`. Binary motion entities explicitly request the known working `0xDB00` family, while numeric PIR entities retain the legacy measurement request for evidence collection. Temperature encoding is normalized once in the protocol device and duplicate YAML compensation is removed.

**Tech Stack:** Python 3.12+, `unittest`, Home Assistant legacy YAML entities, Voluptuous, HDL Buspro UDP telegrams.

## Global Constraints

- Do not modify local HDL device settings, lighting logic, automations, or unrelated integrations.
- Preserve existing Home Assistant entity names and unique IDs.
- Do not log gateway addresses, UDP endpoints, credentials, unrelated devices, or full raw datagrams.
- A motion entity is unavailable until one valid motion response has arrived after startup.
- A missing numeric reading is unavailable; a real zero-lux reading remains valid.
- Decode Buspro sensor temperatures with `raw - 20` exactly once.
- Do not restart Home Assistant until focused tests, the full suite, compilation, and diff review pass.

---

## File Structure

- `pybuspro/devices/sensor.py`: owns request-profile selection, protocol decoding, motion freshness, and scoped diagnostic messages.
- `binary_sensor.py`: selects the motion request profile and exposes motion availability.
- `tests/test_sensor_device.py`: isolates request selection, temperature conversion, motion freshness, and diagnostics.
- `tests/test_binary_sensor_platform.py`: isolates Home Assistant binary-sensor availability behavior.
- `../../configuration.yaml`: removes duplicate indoor PIR temperature compensation only.

---

### Task 1: Restore motion polling with an explicit request profile

**Files:**
- Modify: `tests/test_sensor_device.py`
- Modify: `pybuspro/devices/sensor.py`
- Modify: `binary_sensor.py`

**Interfaces:**
- Consumes: `Sensor(..., device: str, request_profile: str | None = None)`.
- Produces: `Sensor.request_profile == "motion"` selecting `_ReadMotionSensorStatus`; `Sensor.motion_available: bool`.

- [ ] **Step 1: Write the failing request-profile and freshness tests**

Extend `make_sensor()` with:

```python
sensor._request_profile = None
```

Add:

```python
class SensorMotionTests(unittest.TestCase):
    def test_motion_is_unavailable_until_a_valid_response_arrives(self):
        sensor = make_sensor("pir")
        self.assertFalse(sensor.motion_available)

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadMotionSensorStatusResponse,
                payload=[0, 0, 0, 1],
            )
        )

        self.assertTrue(sensor.motion_available)
        self.assertIs(sensor.movement, True)
```

In `SensorReadRequestTests`, include `_ReadMotionSensorStatus` in
`request_names` and add:

```python
async def test_motion_profile_uses_motion_status_request(self):
    created = []
    original = SensorModule._ReadMotionSensorStatus

    class Request:
        def __init__(self, buspro):
            self.subnet_id = None
            self.device_id = None
            created.append("_ReadMotionSensorStatus")

        async def send(self):
            return None

    try:
        SensorModule._ReadMotionSensorStatus = Request
        sensor = make_sensor("pir")
        sensor._buspro = object()
        sensor._request_profile = "motion"
        await sensor.read_sensor_status()
    finally:
        SensorModule._ReadMotionSensorStatus = original

    self.assertEqual(created, ["_ReadMotionSensorStatus"])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorMotionTests tests.test_sensor_device.SensorReadRequestTests.test_motion_profile_uses_motion_status_request -v
```

Expected: failures because `motion_available` and `_request_profile` routing do not exist.

- [ ] **Step 3: Add the minimal device implementation**

Add `request_profile=None` to `Sensor.__init__`, assign
`self._request_profile = request_profile`, and add:

```python
@property
def motion_available(self):
    return self._motion_sensor is not None or self._sonic is not None
```

Before the generic `device == "pir"` branch in `read_sensor_status()`, add:

```python
elif self._request_profile == "motion":
    request = _ReadMotionSensorStatus(self._buspro)
```

Retain the existing guarded `ReadMotionSensorStatusResponse` decoder.

- [ ] **Step 4: Select the motion profile from the binary platform**

Change the `Sensor` construction in `binary_sensor.py` to pass:

```python
request_profile="motion" if sensor_type == CONF_MOTION else None
```

Do not change the device identifier or entity unique ID.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorMotionTests tests.test_sensor_device.SensorReadRequestTests -v
```

Expected: all selected tests pass.

---

### Task 2: Make Home Assistant motion availability reflect fresh data

**Files:**
- Create: `tests/test_binary_sensor_platform.py`
- Modify: `binary_sensor.py`

**Interfaces:**
- Consumes: `Sensor.motion_available`.
- Produces: `BusproBinarySensor.available` requiring both Buspro connectivity and valid motion data for `type: motion`.

- [ ] **Step 1: Create the isolated platform test**

Create a lightweight Home Assistant stub loader following
`tests/test_sensor_platform.py`, then add:

```python
class BusproBinarySensorPlatformTests(unittest.TestCase):
    def make_entity(self, motion_available):
        device = SimpleNamespace(
            name="Test motion",
            device_identifier="test-motion",
            motion_available=motion_available,
            movement=False,
        )
        hass = SimpleNamespace(
            data={BinarySensorPlatform.DATA_BUSPRO: SimpleNamespace(connected=True)}
        )
        return BinarySensorPlatform.BusproBinarySensor(
            hass,
            device,
            BinarySensorPlatform.CONF_MOTION,
            "motion",
            20,
        )

    def test_motion_requires_first_valid_device_response(self):
        self.assertFalse(self.make_entity(False).available)
        self.assertTrue(self.make_entity(True).available)

    def test_disconnected_bus_is_unavailable_even_with_motion_data(self):
        entity = self.make_entity(True)
        entity._hass.data[BinarySensorPlatform.DATA_BUSPRO].connected = False
        self.assertFalse(entity.available)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m unittest tests.test_binary_sensor_platform -v
```

Expected: the first assertion fails because motion availability currently checks only the Buspro connection.

- [ ] **Step 3: Implement the minimal availability branch**

Change `BusproBinarySensor.available` to:

```python
connected = self._hass.data[DATA_BUSPRO].connected
if self._sensor_type == CONF_MOTION:
    return connected and self._device.motion_available
return connected
```

- [ ] **Step 4: Run the platform test and verify GREEN**

Run:

```powershell
python -m unittest tests.test_binary_sensor_platform -v
```

Expected: both tests pass.

---

### Task 3: Decode Sensor-in-One temperature once

**Files:**
- Modify: `tests/test_sensor_device.py`
- Modify: `pybuspro/devices/sensor.py`
- Modify: `../../configuration.yaml`

**Interfaces:**
- Consumes: raw one-byte Buspro sensor temperature.
- Produces: `Sensor.temperature` in degrees Celsius using `raw - 20`.

- [ ] **Step 1: Write the failing Sensor-in-One temperature tests**

Change the existing Sensor-in-One fixtures to encoded bytes:

```python
payload=[0xF8, 49, 0x03, 0xE8, 64, 0, 0, 1, 0, 0]
```

and:

```python
payload=[0xF8, 47, 0x07, 0xD0, 71, 0, 0, 0, 0, 0]
```

Keep their expected temperatures at `29` and `27`. Add:

```python
def test_outdoor_encoded_temperature_61_decodes_to_41(self):
    sensor = make_sensor("sensors_in_one")
    sensor._current_temperature = 61
    self.assertEqual(sensor.temperature, 41)
```

- [ ] **Step 2: Run the temperature tests and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorTelegramTests.test_sensor_in_one_read_decodes_all_exposed_measurements tests.test_sensor_device.SensorTelegramTests.test_sensor_in_one_broadcast_decodes_exposed_measurements tests.test_sensor_device.SensorTelegramTests.test_outdoor_encoded_temperature_61_decodes_to_41 -v
```

Expected: all fail with temperatures 20 degrees too high.

- [ ] **Step 3: Extend protocol conversion to Sensor-in-One**

Change the device-profile condition in `Sensor.temperature` to:

```python
if self._device in ["12in1", "8in1", "pir", "sensors_in_one"]:
    return self._current_temperature - 20
```

- [ ] **Step 4: Run the temperature tests and verify GREEN**

Run the same focused command from Step 2.

Expected: all three tests pass.

- [ ] **Step 5: Remove duplicate YAML compensation**

Remove only `offset: -20` from the Basement, Closet, and Stairs temperature
entries in `../../configuration.yaml`. Do not change names, addresses,
profiles, intervals, or unrelated YAML.

- [ ] **Step 6: Verify the focused configuration**

Run:

```powershell
$lines = Get-Content '..\..\configuration.yaml'
$section = $lines | Select-Object -Skip 140 -First 45
$section
```

Confirm the three indoor PIR temperature entries contain no `offset`, while
the outdoor entry remains otherwise unchanged.

---

### Task 4: Add privacy-scoped PIR measurement diagnostics

**Files:**
- Modify: `tests/test_sensor_device.py`
- Modify: `pybuspro/devices/sensor.py`

**Interfaces:**
- Consumes: recognized Buspro sensor telegrams received by an indoor PIR numeric entity.
- Produces: one warning containing entity name, enum opcode name, payload length, and payload list; no Buspro address or raw UDP data.

- [ ] **Step 1: Write the failing logging test**

Extend `make_sensor()` with:

```python
sensor._name = "Test PIR temperature"
```

Add:

```python
def test_pir_measurement_diagnostic_omits_device_identifier(self):
    sensor = make_sensor("pir")
    sensor._request_profile = None
    telegram = SimpleNamespace(
        operate_code=OperateCode.ReadMotionSensorStatusResponse,
        payload=[0, 0, 0, 1],
    )

    with self.assertLogs(SensorModule._LOGGER, level="WARNING") as logs:
        sensor._telegram_received_cb(telegram)

    message = "\n".join(logs.output)
    self.assertIn("Test PIR temperature", message)
    self.assertIn("ReadMotionSensorStatusResponse", message)
    self.assertIn("payload_length=4", message)
    self.assertIn("payload=[0, 0, 0, 1]", message)
    self.assertNotIn(str(sensor._device_address), message)
```

- [ ] **Step 2: Run the diagnostic test and verify RED**

Run:

```powershell
python -m unittest tests.test_sensor_device.SensorTelegramTests.test_pir_measurement_diagnostic_omits_device_identifier -v
```

Expected: failure because no scoped warning is emitted.

- [ ] **Step 3: Add the minimal scoped log**

At the start of `_telegram_received_cb`, after assigning `payload`, add a set
of recognized measurement/motion opcodes and:

```python
if (
    self._device == "pir"
    and self._request_profile != "motion"
    and self._channel_number is None
    and telegram.operate_code in diagnostic_operate_codes
):
    _LOGGER.warning(
        "PIR measurement diagnostic name=%s operate_code=%s "
        "payload_length=%s payload=%s",
        self._name,
        telegram.operate_code.name,
        len(payload) if isinstance(payload, (list, tuple)) else None,
        payload,
    )
```

The recognized set contains:

```python
OperateCode.ReadMotionSensorStatusResponse
OperateCode.ReadSensorStatusResponse
OperateCode.BroadcastSensorStatusResponse
OperateCode.BroadcastSensorStatusAutoResponse
OperateCode.ReadSensorsInOneStatusResponse
OperateCode.BroadcastSensorsInOneStatusResponse
```

- [ ] **Step 4: Run the diagnostic test and verify GREEN**

Run the same command from Step 2.

Expected: pass with no device address in the captured message.

---

### Task 5: Offline verification and restart handoff

**Files:**
- Verify: `pybuspro/devices/sensor.py`
- Verify: `binary_sensor.py`
- Verify: `tests/test_sensor_device.py`
- Verify: `tests/test_binary_sensor_platform.py`
- Verify: `../../configuration.yaml`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: evidence that Stage 1 is safe to load with a user-approved Home Assistant restart.

- [ ] **Step 1: Run focused tests**

```powershell
python -m unittest tests.test_sensor_device tests.test_binary_sensor_platform tests.test_sensor_platform -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the complete repository test suite**

```powershell
python -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Compile all changed Python files**

```powershell
python -m py_compile pybuspro/devices/sensor.py binary_sensor.py tests/test_sensor_device.py tests/test_binary_sensor_platform.py
```

Expected: exit code `0` with no output.

- [ ] **Step 4: Inspect the exact diff**

```powershell
git diff --check
git diff -- pybuspro/devices/sensor.py binary_sensor.py tests/test_sensor_device.py tests/test_binary_sensor_platform.py
git status --short --branch
```

Confirm there are no unrelated repository edits. Separately inspect only the
four multisensor YAML blocks to confirm the three removed offsets.

- [ ] **Step 5: Commit the Stage 1 repository change**

```powershell
git add pybuspro/devices/sensor.py binary_sensor.py tests/test_sensor_device.py tests/test_binary_sensor_platform.py
git commit -m "fix: restore Buspro PIR motion updates"
```

Do not force-add the live `configuration.yaml` to the nested repository.

- [ ] **Step 6: Request restart approval**

Report the test count, compilation result, commit, live YAML change, and that
no Home Assistant restart has yet occurred. Ask the user to approve one
restart.

- [ ] **Step 7: Verify runtime after the approved restart**

Confirm:

1. each indoor motion entity becomes available only after a valid response;
2. each indoor motion entity changes to detected and returns to clear;
3. outdoor temperature is 20 degrees lower than the previously encoded value;
4. the local HDL light behavior is unchanged;
5. the system log contains only the scoped PIR diagnostic fields.

Use the captured opcode and payload to create the next failing regression
test for indoor temperature and illuminance. Do not guess their decoder.

