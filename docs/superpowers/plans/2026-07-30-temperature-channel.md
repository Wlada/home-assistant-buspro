# Buspro Temperature Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reliable polling-only temperature reads for channel-based HDL temperature probes and the Enviro panel without changing `sensors_in_one`.

**Architecture:** Introduce a `temperature_channel` sensor profile backed by the official `E3E7/E3E8` designated-address protocol. The profile sends the configured channel in the read payload, accepts only matching direct responses, and ignores temperature broadcasts.

**Tech Stack:** Python 3, `unittest`, Home Assistant YAML configuration, existing `pybuspro` telegram/control abstractions.

## Global Constraints

- Do not modify the working `sensors_in_one`, PIR, Outdoor, motion, or lux request/response behavior.
- Do not enable or depend on HDL temperature broadcasting.
- `temperature_channel` must use the third address component as the temperature channel.
- Enviro must use temperature channel 1 and no legacy `-20` offset.
- Deploy only the source state that passed the complete test suite.
- Do not merge, push, or tag until live Home Assistant values are confirmed.

---

### Task 1: Temperature Protocol Control

**Files:**
- Modify: `pybuspro/helpers/enums.py`
- Modify: `pybuspro/devices/control.py`
- Create: `tests/test_temperature_channel_control.py`

**Interfaces:**
- Consumes: `OperateCode`, `_Control.build_telegram_from_control()`, and `Telegram`.
- Produces: `OperateCode.ReadTemperature`, `OperateCode.ReadTemperatureResponse`, and `_ReadTemperature.channel_number: int`.

- [ ] **Step 1: Write the failing control test**

Create a test that constructs `_ReadTemperature`, sets its target and channel,
and asserts:

```python
self.assertEqual(request.telegram.operate_code, OperateCode.ReadTemperature)
self.assertEqual(request.telegram.target_address, (10, 20))
self.assertEqual(request.telegram.payload, [3])
```

Also assert the wire enum values:

```python
self.assertEqual(OperateCode.ReadTemperature.value, b"\xE3\xE7")
self.assertEqual(OperateCode.ReadTemperatureResponse.value, b"\xE3\xE8")
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python -m unittest discover -s tests -p 'test_temperature_channel_control.py' -v
```

Expected: failure because the active byte enum and `_ReadTemperature` control do
not exist.

- [ ] **Step 3: Implement the minimal protocol control**

Add the two byte-valued members to the active `OperateCode(Enum)`:

```python
ReadTemperature = b"\xE3\xE7"
ReadTemperatureResponse = b"\xE3\xE8"
```

Add `_ReadTemperature` with `channel_number = None`, and map it in
`_Control.build_telegram_from_control()`:

```python
elif type(control) == _ReadTemperature:
    operate_code = OperateCode.ReadTemperature
    payload = [control.channel_number]
```

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run the same focused command. Expected: all control tests pass.

- [ ] **Step 5: Commit the protocol control**

```powershell
git add pybuspro/helpers/enums.py pybuspro/devices/control.py tests/test_temperature_channel_control.py
git commit -m "feat: add channel temperature read control"
```

### Task 2: Temperature Channel Sensor Profile

**Files:**
- Modify: `pybuspro/devices/sensor.py`
- Modify: `tests/test_sensor_device.py`

**Interfaces:**
- Consumes: `_ReadTemperature` and the two new `OperateCode` members from Task 1.
- Produces: `device="temperature_channel"` polling and direct-response decoding.

- [ ] **Step 1: Write failing request-routing tests**

Extend the sensor test control stub with `_ReadTemperature`. Add an async test
that creates a `temperature_channel` sensor with channel 3 and asserts that only
`_ReadTemperature` is constructed and receives channel 3.

Retain the existing test proving that `sensors_in_one` still constructs
`_ReadSensorsInOneStatus`.

- [ ] **Step 2: Run the focused routing tests and confirm RED**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python -m unittest discover -s tests -p 'test_sensor_device.py' -v
```

Expected: the new profile still selects `_ReadStatusOfChannels`.

- [ ] **Step 3: Implement minimal request routing**

Import `_ReadTemperature` and place this branch before the generic
channel-number branch:

```python
elif self._device == "temperature_channel":
    if self._channel_number is None:
        return
    request = _ReadTemperature(self._buspro)
    request.channel_number = self._channel_number
```

Leave the `sensors_in_one` branch unchanged.

- [ ] **Step 4: Write failing response-decoding tests**

Add tests for:

```python
# positive Enviro-style payload
payload=[1, 27]

# negative signed-magnitude payload
payload=[1, 0x85]  # -5 C

# dry-contact payload with optional trailing float
payload=[2, 29, 0x00, 0x00, 0xE8, 0x41]
```

Each matching response must update once. Add malformed (`[]`, `[1]`) and
wrong-channel cases that retain the prior value and do not update.

- [ ] **Step 5: Run the focused parser tests and confirm RED**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python -m unittest discover -s tests -p 'test_sensor_device.py' -v
```

Expected: no `ReadTemperatureResponse` parsing exists.

- [ ] **Step 6: Implement direct-response decoding**

Add a focused helper:

```python
@staticmethod
def _decode_signed_temperature(value):
    magnitude = value & 0x7F
    return -magnitude if value & 0x80 else magnitude
```

Handle `ReadTemperatureResponse` only for `temperature_channel`, require at
least two payload bytes, require the returned channel to match, set
`_current_temperature` from byte 2, and call `_call_device_updated()`.

- [ ] **Step 7: Write and pass the broadcast-isolation test**

Send `BroadcastTemperatureResponse` to a `temperature_channel` sensor with a
previous valid value. Assert the value is unchanged and no update callback is
made. Implement an early return for this profile in the existing broadcast
branch while preserving the legacy branch for other device profiles.

- [ ] **Step 8: Run sensor tests and commit**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python -m unittest discover -s tests -p 'test_sensor_device.py' -v
```

Expected: all sensor tests pass.

Commit:

```powershell
git add pybuspro/devices/sensor.py tests/test_sensor_device.py
git commit -m "feat: support polled temperature channels"
```

### Task 3: Regression Verification and Configuration Migration

**Files:**
- Modify on HA host: `configuration.yaml`
- Create outside the HA host: a timestamped pre-change configuration backup.

**Interfaces:**
- Consumes: the `temperature_channel` profile from Task 2.
- Produces: six dry-contact probe entities and one Enviro entity using the new profile.

- [ ] **Step 1: Run the complete component suite**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python -m unittest discover -s tests -q
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 2: Create and verify the configuration backup**

Copy `configuration.yaml` to a task-specific backup directory outside the HA
share. Confirm the backup exists and has the same length before editing.

- [ ] **Step 3: Apply the seven scoped configuration changes**

- Change exactly six affected dry-contact temperature entries from
  `device: "sensors_in_one"` to `device: "temperature_channel"`.
- Change Living room temperature from `device: "dlp"` to
  `device: "temperature_channel"`.
- Append `.1` to that Enviro address.
- Change only that Enviro offset from `-20` to `0`.
- Do not alter any other entity.

- [ ] **Step 4: Validate the migrated configuration structurally**

Use a redacted script to assert:

- exactly seven `temperature_channel` entities exist;
- every one has a three-part address;
- Enviro channel is 1 and offset is 0;
- all existing PIR/Outdoor `sensors_in_one` entries remain unchanged.

Do not print addresses.

- [ ] **Step 5: Deploy the verified component state**

Copy only the changed component files and committed docs/tests as appropriate
from the verified local repository to the active network component. Verify
file hashes for the deployed runtime Python files against the tested local
files.

- [ ] **Step 6: Restart and live-test**

After the user restarts Home Assistant:

- confirm the Enviro temperature becomes available;
- simulate one dry-contact temperature channel and confirm only its matching HA
  entity changes;
- confirm a second channel remains unchanged;
- confirm PIR temperature, lux, motion, and Outdoor remain available.

- [ ] **Step 7: Final repository integration**

Only after live confirmation:

- commit the scoped configuration migration in a repository that actually
  tracks it, or explicitly record that the HA root is not a Git repository;
- fast-forward/merge the feature branch into `main`;
- run the complete suite once more;
- push `main`;
- create and push a descriptive working-state tag;
- verify clean status and remote refs.
