# Buspro Sensor Runtime Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the request, received payload, and decoded state for the three HDL-MSP07M.4C indoor PIR sensors and the HDL-MSOUT.4W outdoor sensor without recording addresses or unrelated traffic.

**Architecture:** Add one bounded JSONL capture object to the Buspro runtime and let each Sensor instance submit explicit, whitelisted request or response fields. Only temperature-role Sensor instances record decoded responses, while temperature and motion roles record outgoing requests. This avoids duplicate response rows and keeps transport identifiers outside the capture API.

**Tech Stack:** Python standard library, Home Assistant custom component, unittest.

## Global Constraints

- Do not record Buspro addresses, gateway endpoints, credentials, full UDP datagrams, or unrelated devices.
- Keep at most the newest 500 JSONL records.
- Capture failures must never interrupt Buspro parsing or Home Assistant updates.
- Keep the capture temporary and remove it after runtime diagnosis.
- Do not change local HDL lighting logic or device configuration.

---

### Task 1: Bounded privacy-scoped JSONL writer

**Files:**
- Create: `pybuspro/sensor_diagnostics.py`
- Create: `tests/test_sensor_diagnostics.py`

**Interfaces:**
- Produces: `SensorDiagnosticCapture(path, max_records=500)`
- Produces: `record_request(name, device, role, request_profile, operate_code)`
- Produces: `record_response(name, device, role, request_profile, operate_code, payload, temperature, illuminance, humidity, raw_motion, movement)`

- [ ] **Step 1: Write failing tests**

Test that 502 writes leave exactly 500 valid JSON rows, request/response rows contain only the explicit schema, no address-like keys exist, and an invalid output path does not raise.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_sensor_diagnostics -v`
Expected: import failure because `pybuspro.sensor_diagnostics` does not exist.

- [ ] **Step 3: Implement the minimal writer**

Use `collections.deque(maxlen=500)`, load at most the existing last 500 rows, serialize with `json.dumps`, and rewrite through a same-directory temporary file plus `os.replace`. Catch `OSError`, `TypeError`, and `ValueError` inside the writer.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_sensor_diagnostics -v`
Expected: all capture tests pass.

### Task 2: Instrument Sensor request and response boundaries

**Files:**
- Modify: `pybuspro/devices/sensor.py`
- Modify: `sensor.py`
- Modify: `binary_sensor.py`
- Modify: `tests/test_sensor_device.py`
- Modify: `tests/test_sensor_platform.py`
- Modify: `tests/test_binary_sensor_platform.py`

**Interfaces:**
- Consumes: `buspro.sensor_diagnostic_capture`
- Adds: `Sensor(..., diagnostic_role=None)`
- Produces one response record only when `diagnostic_role == "temperature"` and `device in {"pir", "sensors_in_one"}`.
- Produces request records only for temperature and motion roles on those same device profiles.

- [ ] **Step 1: Write failing parser-boundary tests**

Attach a recording fake to the Buspro fake, feed one DB01 response and one Sensor-in-One response, and assert payload bytes plus decoded temperature, illuminance, humidity, raw motion, and Boolean movement are recorded. Assert an illuminance-role instance does not duplicate the response.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_sensor_device -v`
Expected: failure because `diagnostic_role` and capture calls are absent.

- [ ] **Step 3: Implement response capture**

Add `_capture_response(telegram, payload)` and call it after each recognized sensor response is decoded. Pass only explicit scalar/list values to `record_response`; never pass a Telegram or device address object.

- [ ] **Step 4: Write and verify failing request/platform tests**

Assert motion role emits a request record naming `ReadMotionSensorStatus`, temperature role names the measurement request, and both HA platforms pass their entity type as `diagnostic_role`.

- [ ] **Step 5: Implement request and platform wiring**

Call `record_request` immediately before `request.send()` and pass `sensor_type` from both platform setup functions.

- [ ] **Step 6: Verify GREEN**

Run: `python -m unittest tests.test_sensor_device tests.test_sensor_platform tests.test_binary_sensor_platform -v`
Expected: all focused tests pass.

### Task 3: Create one runtime capture and verify deployment

**Files:**
- Modify: `__init__.py`
- Modify: `tests/test_component_services.py` if its existing fakes cover BusproModule construction.

**Interfaces:**
- Creates: `SensorDiagnosticCapture(hass.config.path("buspro_sensor_capture.jsonl"))`
- Attaches: `hdl.sensor_diagnostic_capture`

- [ ] **Step 1: Write a failing runtime-wiring test where feasible**

Construct BusproModule with a fake `hass.config.path`, then assert the HDL object receives one shared capture object and the resolved filename is `buspro_sensor_capture.jsonl`.

- [ ] **Step 2: Verify RED and implement minimal wiring**

Create the capture once during BusproModule initialization and attach it to the Buspro instance before platform entities are created.

- [ ] **Step 3: Run complete verification**

Run: `$env:PYTHONPATH='<path-to-test-dependencies>'; python -m unittest discover -s tests -v`
Expected: complete suite passes.

Run: `python -m py_compile __init__.py sensor.py binary_sensor.py pybuspro/devices/sensor.py pybuspro/sensor_diagnostics.py`
Expected: no output.

Run: `git diff --check`
Expected: no output.

- [ ] **Step 4: Commit**

Commit message: `chore: capture Buspro sensor runtime data`

- [ ] **Step 5: Runtime evidence cycle**

Restart Home Assistant once. Keep moving for at least 60 seconds in front of each indoor PIR, then leave it clear. Read `buspro_sensor_capture.jsonl`, identify the failing boundary and actual temperature encoding, implement only the evidence-supported fix in a separate TDD cycle, and remove the capture afterward.
