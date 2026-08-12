# Buspro Unknown Sensor Telegram Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture privacy-scoped operation-code and payload evidence for unknown telegrams emitted by the installed indoor PIR firmware, without changing Home Assistant entity state.

**Architecture:** Extend the existing bounded `SensorDiagnosticCapture` with one raw-response record shape. The existing per-device `Sensor` callback invokes it only for unknown telegrams on the indoor PIR temperature-role instance, deriving the two operation-code bytes from the datagram but never persisting the datagram or any address.

**Tech Stack:** Python 3, `unittest`, Home Assistant custom component, existing Buspro telegram parser.

## Global Constraints

- This is diagnostic-only: do not change polling, state decoding, availability, or Home Assistant entity values.
- Persist no Buspro source/target address, gateway/UDP address, IP address, raw datagram, or Home Assistant entity/device identifier.
- Record only configured name, device profile, diagnostic role, four-character hexadecimal operation code, payload length, and payload bytes.
- Only an indoor `pir` sensor whose diagnostic role is `temperature` may create a raw-response record.
- Keep the existing 500-record bounded capture file and silently ignore malformed datagrams.

---

### Task 1: Privacy-scoped raw response record

**Files:**
- Modify: `tests/test_sensor_diagnostics.py`
- Modify: `pybuspro/sensor_diagnostics.py`

**Interfaces:**
- Consumes: `SensorDiagnosticCapture._write(record: dict) -> None`
- Produces: `SensorDiagnosticCapture.record_raw_response(*, name: str, device: str, role: str, operate_code: str, payload: list[int] | tuple[int, ...]) -> None`

- [ ] **Step 1: Write the failing raw-response shape test**

Add this test to `SensorDiagnosticCaptureTests`:

```python
def test_raw_response_record_contains_only_safe_protocol_fields(self):
    with tempfile.TemporaryDirectory() as directory:
        capture_path = Path(directory) / "capture.jsonl"
        capture = SensorDiagnosticCapture(capture_path)

        capture.record_raw_response(
            name="Basement temperature",
            device="pir",
            role="temperature",
            operate_code="D993",
            payload=[0, 123],
        )
        record = json.loads(capture_path.read_text(encoding="utf-8"))

    self.assertEqual(
        set(record),
        {
            "timestamp",
            "direction",
            "name",
            "device",
            "role",
            "operate_code",
            "payload_length",
            "payload",
        },
    )
    self.assertEqual(record["direction"], "raw_response")
    self.assertEqual(record["operate_code"], "D993")
    self.assertEqual(record["payload"], [0, 123])
    serialized = json.dumps(record).lower()
    for forbidden in ("address", "udp", "datagram", "entity_id", "device_id"):
        self.assertNotIn(forbidden, serialized)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>;<repo-root>'
python -m unittest tests.test_sensor_diagnostics.SensorDiagnosticCaptureTests.test_raw_response_record_contains_only_safe_protocol_fields -v
```

Expected: `ERROR` because `SensorDiagnosticCapture` has no
`record_raw_response` method.

- [ ] **Step 3: Implement the minimal bounded record method**

Add this method immediately after `record_response` in
`pybuspro/sensor_diagnostics.py`:

```python
def record_raw_response(
    self,
    *,
    name,
    device,
    role,
    operate_code,
    payload,
):
    payload_bytes = list(payload) if isinstance(payload, (list, tuple)) else []
    self._write(
        {
            "timestamp": self._timestamp(),
            "direction": "raw_response",
            "name": name,
            "device": device,
            "role": role,
            "operate_code": operate_code,
            "payload_length": len(payload_bytes),
            "payload": payload_bytes,
        }
    )
```

- [ ] **Step 4: Run diagnostic capture tests and verify GREEN**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>;<repo-root>'
python -m unittest tests.test_sensor_diagnostics -v
```

Expected: all `SensorDiagnosticCaptureTests` pass.

- [ ] **Step 5: Commit the independently tested record writer**

```powershell
git add tests/test_sensor_diagnostics.py pybuspro/sensor_diagnostics.py
git commit -m "test: add safe raw sensor capture record"
```

---

### Task 2: Unknown indoor PIR telegram routing

**Files:**
- Modify: `tests/test_sensor_device.py`
- Modify: `pybuspro/devices/sensor.py`

**Interfaces:**
- Consumes: `SensorDiagnosticCapture.record_raw_response(...)` from Task 1
- Produces: `Sensor._capture_unknown_response(telegram, payload) -> None`
- Produces: `Sensor._unknown_operate_code(telegram) -> str | None`

- [ ] **Step 1: Extend the test capture fake**

Add `self.raw_responses = []` to `RecordingCapture.__init__` and:

```python
def record_raw_response(self, **record):
    self.raw_responses.append(record)
```

- [ ] **Step 2: Write the failing unknown-opcode routing test**

Add to `SensorTelegramTests`:

```python
def test_temperature_role_captures_unknown_pir_opcode_without_decoding(self):
    capture = RecordingCapture()
    sensor = make_sensor("pir", capture=capture)
    datagram = bytearray(25)
    datagram[21:23] = b"\xD9\x93"

    sensor._telegram_received_cb(
        SimpleNamespace(
            operate_code=None,
            payload=[0, 123],
            udp_data=bytes(datagram),
        )
    )

    self.assertEqual(
        capture.raw_responses,
        [
            {
                "name": "Test PIR temperature",
                "device": "pir",
                "role": "temperature",
                "operate_code": "D993",
                "payload": [0, 123],
            }
        ],
    )
    sensor._call_device_updated.assert_not_called()
```

- [ ] **Step 3: Write failing scope and malformed-input tests**

Add:

```python
def test_non_temperature_role_does_not_duplicate_unknown_pir_capture(self):
    capture = RecordingCapture()
    sensor = make_sensor("pir", diagnostic_role="motion", capture=capture)
    datagram = bytearray(25)
    datagram[21:23] = b"\xD9\x93"

    sensor._telegram_received_cb(
        SimpleNamespace(
            operate_code=None,
            payload=[0, 123],
            udp_data=bytes(datagram),
        )
    )

    self.assertEqual(capture.raw_responses, [])

def test_known_or_malformed_telegram_does_not_create_raw_capture(self):
    capture = RecordingCapture()
    sensor = make_sensor("pir", capture=capture)

    sensor._telegram_received_cb(
        SimpleNamespace(
            operate_code=OperateCode.ReadMotionSensorStatusResponse,
            payload=[0, 0, 0, 0],
            udp_data=bytes(25),
        )
    )
    sensor._telegram_received_cb(
        SimpleNamespace(operate_code=None, payload=[1], udp_data=b"\x00")
    )

    self.assertEqual(capture.raw_responses, [])
```

- [ ] **Step 4: Run the three focused tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>;<repo-root>'
python -m unittest `
  tests.test_sensor_device.SensorTelegramTests.test_temperature_role_captures_unknown_pir_opcode_without_decoding `
  tests.test_sensor_device.SensorTelegramTests.test_non_temperature_role_does_not_duplicate_unknown_pir_capture `
  tests.test_sensor_device.SensorTelegramTests.test_known_or_malformed_telegram_does_not_create_raw_capture -v
```

Expected: failures because the sensor callback does not route unknown
telegrams to `record_raw_response`.

- [ ] **Step 5: Implement minimal unknown-opcode extraction and routing**

Add these methods to `Sensor` immediately before `_telegram_received_cb`:

```python
@staticmethod
def _unknown_operate_code(telegram):
    if telegram.operate_code is not None:
        return None
    udp_data = getattr(telegram, "udp_data", None)
    if not isinstance(udp_data, (bytes, bytearray)) or len(udp_data) < 23:
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
```

Call it as the first statement after `payload = telegram.payload`:

```python
self._capture_unknown_response(telegram, payload)
```

- [ ] **Step 6: Run focused sensor tests and verify GREEN**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>;<repo-root>'
python -m unittest tests.test_sensor_device -v
```

Expected: all sensor device tests pass and unknown telegrams do not update
decoded state.

- [ ] **Step 7: Run the complete component suite and static checks**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>;<repo-root>'
python -m unittest discover -s tests -v
python -m py_compile pybuspro/sensor_diagnostics.py pybuspro/devices/sensor.py
git diff --check
```

Expected: the entire suite passes, both files compile, and `git diff --check`
prints no errors.

- [ ] **Step 8: Commit the diagnostic router**

```powershell
git add tests/test_sensor_device.py pybuspro/devices/sensor.py
git commit -m "chore: capture unknown PIR sensor telegrams"
```

---

### Task 3: Deploy and collect controlled evidence

**Files:**
- Deploy the two committed production files to the active network Home Assistant component.
- Read: active bounded `buspro_sensor_capture.jsonl`

**Interfaces:**
- Consumes: `raw_response` records produced by Tasks 1 and 2
- Produces: observed mapping from simulated motion/lux/temperature values to operation codes and payload byte positions

- [ ] **Step 1: Verify both repositories are clean and based on the same pre-change commit**

Run read-only `git status --short --branch` and `git log -3 --oneline` in the
local and active network component repositories. Stop if the active repository
contains unrelated changes.

- [ ] **Step 2: Apply only the two diagnostic commits to the active component**

Transfer the committed changes without copying unrelated configuration or
generated files. Confirm the active repository diff matches the local
diagnostic commits, then commit them on the active branch.

- [ ] **Step 3: Restart Home Assistant once**

The user performs the restart. Do not clear the existing bounded capture;
timestamps distinguish the new run.

- [ ] **Step 4: Run controlled HDL Setup Pro simulations**

On Basement, simulate one value at a time and wait long enough to generate the
corresponding telegram:

1. motion `0`, then motion `1`;
2. illuminance `123 lux`;
3. temperature `25 °C`.

- [ ] **Step 5: Analyze only privacy-scoped records**

Group new `raw_response` records by operation code and payload. Compare the
single changed bytes/words to the known simulated values. Do not print or
persist addresses or raw datagrams.

- [ ] **Step 6: Write a separate production-fix design from observed evidence**

Specify the exact request payloads, response opcodes, byte offsets, encoding,
availability behavior, and required YAML fields. The final parser/polling fix
must have its own failing tests and must remove the temporary unknown-telegram
diagnostic hook after verification.
