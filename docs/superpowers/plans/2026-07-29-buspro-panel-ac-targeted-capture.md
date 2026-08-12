# BusPro Panel AC Targeted Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace broad, address-hardcoded BusPro diagnostics with a sanitized AC-only logger, configure both Enviro AC channels for capture, and produce an evidence-backed protocol map for the full Climate implementation.

**Architecture:** `PanelACDevice` receives telegrams already scoped to its configured Enviro panel, so it will emit sanitized capture records only for the known panel AC operation-code family and its own AC channel. Existing global parsed/raw capture blocks are removed. Two temporary panel AC switch instances host channel-specific listeners until the full Climate entities are implemented.

**Tech Stack:** Python 3.12+, asyncio, Python logging, Home Assistant YAML logger configuration, unittest, existing embedded pybuspro transport.

## Global Constraints

- Do not implement target temperature, HVAC mode, fan, swing, or status-read commands in this plan.
- Do not guess protocol field or value bytes.
- Do not log UDP sources, raw UDP datagrams, panel addresses, IR addresses, credentials, usernames, or unrelated BusPro traffic.
- Use English instance names `AC-1` and `AC-2`.
- Keep existing relay switches, floor-heating climate entities, and AC power behavior unchanged.
- State remains non-optimistic and channel-scoped.
- Exact private addresses remain only in the local Home Assistant configuration.
- Do not commit `configuration.yaml` or captured Home Assistant logs.
- Stage B planning starts only after the protocol map is supported by captured evidence.

---

### Task 1: Replace broad diagnostics with channel-scoped AC capture

**Files:**
- Modify: `pybuspro/devices/panel_ac.py`
- Modify: `pybuspro/buspro.py`
- Modify: `pybuspro/transport/network_interface.py`
- Modify: `tests/test_panel_ac_device.py`
- Create: `tests/test_panel_ac_capture_scope.py`

**Interfaces:**
- Consumes: `PanelACDevice(buspro, device_address, channel, name="")`, `OperateCode.ControlPanelAC`, `ControlPanelACResponse`, `ReadPanelAC`, and `ReadPanelACResponse`.
- Produces: debug logger `buspro.ac_capture` with records containing `instance`, `direction`, `opcode`, `field`, `value`, `channel`, and parsed `payload`.
- Preserves: existing `PanelACDevice` power sends, E3D9 state parsing, callbacks, and stable identifier.

- [ ] **Step 1: Add failing sanitized-capture tests**

In `tests/test_panel_ac_device.py`, import `logging` and add source/target addresses to Telegram-shaped test objects. Add:

```python
def test_matching_ac_response_emits_sanitized_capture(self):
    self.device._call_device_updated = Mock()
    telegram = SimpleNamespace(
        source_address=(10, 20),
        target_address=(10, 30),
        operate_code=OperateCode.ControlPanelACResponse,
        payload=[3, 1, 3],
    )

    with self.assertLogs("buspro.ac_capture", logging.DEBUG) as logs:
        self.device._telegram_received_cb(telegram)

    message = "\n".join(logs.output)
    self.assertIn("instance=Office AC", message)
    self.assertIn("direction=from_panel", message)
    self.assertIn("opcode=E3D9", message)
    self.assertIn("field=3", message)
    self.assertIn("value=1", message)
    self.assertIn("channel=3", message)
    self.assertIn("payload=[3, 1, 3]", message)
    self.assertNotIn("source=", message)
    self.assertNotIn("target=", message)
    self.assertNotIn("raw=", message)
```

Add channel and opcode isolation:

```python
def test_capture_ignores_another_ac_channel(self):
    telegram = SimpleNamespace(
        source_address=(10, 20),
        target_address=(10, 30),
        operate_code=OperateCode.ControlPanelACResponse,
        payload=[3, 1, 4],
    )

    with self.assertNoLogs("buspro.ac_capture", logging.DEBUG):
        self.device._telegram_received_cb(telegram)


def test_capture_ignores_non_ac_opcode(self):
    telegram = SimpleNamespace(
        source_address=(10, 20),
        target_address=(10, 30),
        operate_code=OperateCode.ReadStatusOfChannelsResponse,
        payload=[3, 1, 3],
    )

    with self.assertNoLogs("buspro.ac_capture", logging.DEBUG):
        self.device._telegram_received_cb(telegram)
```

Add a known read-response capture test without assigning semantics:

```python
def test_read_ac_response_is_captured_without_state_mutation(self):
    self.device._call_device_updated = Mock()
    telegram = SimpleNamespace(
        source_address=(10, 20),
        target_address=(10, 30),
        operate_code=OperateCode.ReadPanelACResponse,
        payload=[9, 8],
    )

    with self.assertLogs("buspro.ac_capture", logging.DEBUG) as logs:
        self.device._telegram_received_cb(telegram)

    self.assertIn("opcode=E3DB", "\n".join(logs.output))
    self.assertIsNone(self.device.is_on)
    self.device._call_device_updated.assert_not_called()
```

- [ ] **Step 2: Add a failing global-capture removal regression test**

Create `tests/test_panel_ac_capture_scope.py`:

```python
import unittest
from pathlib import Path


COMPONENT_ROOT = Path(__file__).resolve().parents[1]


class PanelACCaptureScopeTests(unittest.TestCase):
    def test_hardcoded_global_capture_blocks_are_removed(self):
        buspro_source = (
            COMPONENT_ROOT / "pybuspro" / "buspro.py"
        ).read_text(encoding="utf-8")
        network_source = (
            COMPONENT_ROOT
            / "pybuspro"
            / "transport"
            / "network_interface.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Enviro telegram source=", buspro_source)
        self.assertNotIn("Enviro raw datagram", network_source)
        self.assertNotIn("data.hex()", network_source)


if __name__ == "__main__":
    unittest.main()
```

This source-level regression is intentional: it prevents restoration of the privacy-sensitive, address-hardcoded diagnostic blocks without embedding the private address in a test.

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python tests\test_panel_ac_device.py -v
python tests\test_panel_ac_capture_scope.py -v
```

Expected:

- capture tests fail because `buspro.ac_capture` emits no records;
- scope test fails because the global parsed/raw capture strings still exist;
- existing power behavior tests continue to pass.

- [ ] **Step 4: Add the minimal targeted capture implementation**

In `pybuspro/devices/panel_ac.py`, add:

```python
_CAPTURE_LOGGER = logging.getLogger("buspro.ac_capture")
_CAPTURE_OPERATE_CODES = frozenset(
    {
        OperateCode.ControlPanelAC,
        OperateCode.ControlPanelACResponse,
        OperateCode.ReadPanelAC,
        OperateCode.ReadPanelACResponse,
    }
)
_CHANNEL_AT_INDEX_2_OPERATE_CODES = frozenset(
    {
        OperateCode.ControlPanelAC,
        OperateCode.ControlPanelACResponse,
    }
)
```

Add this method to `PanelACDevice`:

```python
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
```

Call it as the first line of `_telegram_received_cb`:

```python
self._capture_telegram(telegram)
```

Do not add addresses or raw data to the message.

- [ ] **Step 5: Remove both hard-coded global capture blocks**

In `pybuspro/buspro.py`, remove only the temporary Enviro logging block at the start of `_callback_all_messages`:

```python
if (...) in (...):
    ...
    self.telegram_logger.debug(...)
```

Keep callback dispatch and device-address filtering unchanged.

In `pybuspro/transport/network_interface.py`, remove only the temporary block at the start of `_udp_request_received`:

```python
if len(data) >= 25 and (...):
    self.buspro.telegram_logger.warning(...)
```

Keep parsing and callback invocation unchanged.

- [ ] **Step 6: Run targeted and regression tests**

Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python tests\test_panel_ac_device.py -v
python tests\test_panel_ac_capture_scope.py -v
python tests\test_panel_ac_switch.py -v
python tests\test_panel_ac_service.py -v
python -m compileall -q .
git diff --check
```

Expected: all tests pass, compilation exits 0, and no warning about pending asyncio tasks appears.

- [ ] **Step 7: Verify privacy and scope**

Run:

```powershell
rg -n "Enviro raw datagram|Enviro telegram source=|udp_source=|raw=" pybuspro
git diff --name-only
```

Expected:

- the privacy-sensitive log markers are absent;
- changed production files are limited to `panel_ac.py`, `buspro.py`, and `network_interface.py`;
- no climate or floor-heating file changed.

- [ ] **Step 8: Commit the capture unit**

```powershell
git add pybuspro/devices/panel_ac.py `
  pybuspro/buspro.py `
  pybuspro/transport/network_interface.py `
  tests/test_panel_ac_device.py `
  tests/test_panel_ac_capture_scope.py
git commit -m "feat: add targeted panel AC capture logging"
```

---

### Task 2: Configure two temporary AC listeners and narrow HA logging

**Files:**
- Modify locally, do not commit: `configuration.yaml`

**Interfaces:**
- Consumes: existing `switch.buspro` device mapping and the first configured `device_type: panel_ac` entry.
- Produces: two temporary panel AC switch instances named `AC-1` and `AC-2`, using the same panel subnet/device components and AC channels 1 and 2.
- Produces: Home Assistant logs only `buspro.ac_capture` at debug level for BusPro protocol diagnostics.

- [ ] **Step 1: Hash and inspect only the relevant YAML structures**

Before editing:

```powershell
Get-FileHash -Algorithm SHA256 configuration.yaml
```

Read only the `logger:` and BusPro `switch:` structures. Redact names and addresses from command output.

- [ ] **Step 2: Narrow logger configuration**

Under `logger.logs`:

- remove explicit debug overrides whose logger names begin with `buspro`;
- remove explicit debug overrides for `custom_components.buspro` and `homeassistant.components.buspro`;
- add exactly:

```yaml
    buspro.ac_capture: debug
```

Do not change the global default logger level or non-BusPro logger entries.

- [ ] **Step 3: Configure the two temporary switch listeners**

In the existing BusPro switch `devices` mapping:

1. Locate the current `device_type: panel_ac` entry.
2. Preserve its panel subnet/device components and channel 1.
3. Set its YAML name to `AC-1`.
4. Add a second entry with the same panel subnet/device components and channel 2.
5. Set the second YAML name to `AC-2`.
6. Set `device_type: panel_ac` on both.

Do not add the IR transmitter addresses as switch targets. Commands and callbacks remain addressed to the Enviro panel plus AC channel.

- [ ] **Step 4: Validate the edited YAML structure**

Verify:

- exactly two `device_type: panel_ac` switch entries exist;
- their channel components are 1 and 2;
- both share the same panel subnet/device components;
- the old broad BusPro debug entries are absent;
- `buspro.ac_capture: debug` occurs exactly once;
- unrelated switch, climate, logger, and ping tracker entries are unchanged.

- [ ] **Step 5: Run Home Assistant configuration check and restart**

In Home Assistant:

1. Open **Developer tools → YAML**.
2. Run **Check configuration**.
3. Do not restart if validation reports an error.
4. If valid, restart Home Assistant.

After restart, verify two temporary switch entities exist by display names `AC-1` and `AC-2`. The first entity ID may retain an older registry ID; display name and logger instance name are the acceptance criteria for capture.

- [ ] **Step 6: Verify capture isolation before the full session**

Perform one direct panel power change on AC-1.

Expected log behavior:

- at least one `buspro.ac_capture` record appears;
- record instance is `AC-1`;
- the matching channel is reported;
- no raw datagram, UDP source, address, or unrelated BusPro telegram appears;
- AC-2 state does not change.

If no record appears, stop and debug capture before collecting the mapping session.

---

### Task 3: Collect the controlled two-AC protocol capture

**Files:**
- Produce locally, do not commit: downloaded Home Assistant log
- Produce locally, do not commit: action timeline notes

**Interfaces:**
- Consumes: targeted `buspro.ac_capture` records from Task 2.
- Produces: a timestamped action-to-payload evidence set sufficient to map power, mode, cooling/heating target temperature, current temperature, fan, swing, and optional status reads.

- [ ] **Step 1: Record AC-1 starting state**

Record:

- approximate local time;
- power;
- selected mode;
- current temperature;
- cooling target;
- heating target;
- fan level;
- swing state.

- [ ] **Step 2: Verify whether AC status capture contains current temperature**

Run the setup-tool refresh/read action for AC-1 and compare the E3DB
payload with the displayed current temperature.

If E3DB contains the value, continue to Step 3 without changing code.

If E3DB does not contain the value, pause the capture and add a narrow
parsed-temperature fallback:

1. In `tests/test_panel_ac_device.py`, add a table-driven failing test
   that sends each of these operation codes from the configured panel and
   asserts a `buspro.ac_capture` record without a device-state callback:

```python
temperature_operate_codes = (
    OperateCode.ReadSensorStatusResponse,
    OperateCode.BroadcastSensorStatusResponse,
    OperateCode.BroadcastSensorStatusAutoResponse,
    OperateCode.BroadcastTemperatureResponse,
)
```

2. Extend `_CAPTURE_OPERATE_CODES` in `panel_ac.py` with exactly those
   four parsed response codes.
3. Do not assign an AC channel or mutate AC state from these responses.
4. Run:

```powershell
$env:PYTHONPATH='<path-to-test-dependencies>'
python tests\test_panel_ac_device.py -v
python tests\test_panel_ac_capture_scope.py -v
python -m compileall -q .
```

5. Commit only if the fallback was required:

```powershell
git add pybuspro/devices/panel_ac.py tests/test_panel_ac_device.py
git commit -m "feat: capture panel temperature status"
```

6. Restart Home Assistant and repeat the AC-1 refresh before continuing.

The fallback remains panel-scoped and parsed-only. It must not restore
global BusPro debug or raw UDP logging.

- [ ] **Step 3: Capture AC-1 actions with five-second gaps**

Perform exactly this sequence, recording each action time:

1. setup-tool refresh/read status;
2. power off → on;
3. power on → off;
4. cooling → heating;
5. heating → cooling;
6. cooling target +1 °C;
7. cooling target −1 °C;
8. heating target +1 °C;
9. heating target −1 °C;
10. fan low → medium;
11. fan medium → high;
12. fan high → low;
13. swing off → on;
14. swing on → off.

Do not perform unrelated panel or Home Assistant actions during the sequence.

- [ ] **Step 4: Record temperature limits without guessing**

Inspect the setup tool or IR-code configuration for minimum and maximum cooling and heating targets.

If limits are displayed, record them without sweeping commands. If they are not displayed, use a safe boundary test while recording every action. Do not infer limits from Home Assistant defaults.

- [ ] **Step 5: Record AC-2 starting state**

Record the same starting-state fields as AC-1.

- [ ] **Step 6: Capture abbreviated AC-2 confirmation**

With five-second gaps, record:

1. setup-tool refresh/read status;
2. one power change;
3. one active-mode target temperature change;
4. one fan-level change;
5. one swing change.

- [ ] **Step 7: Download and hand off the capture**

Download the Home Assistant log and provide:

- the log file;
- ordered action list;
- approximate timestamp for each action;
- starting state for each AC;
- observed temperature limits;
- any action whose visible result differed from expectation.

Do not paste secrets, network configuration, or unrelated logs into the protocol map.

---

### Task 4: Produce the evidence-backed protocol map

**Files:**
- Create after capture: `docs/superpowers/specs/2026-07-29-buspro-panel-ac-protocol-map.md`

**Interfaces:**
- Consumes: targeted log and timeline from Task 3.
- Produces: exact operation-code, field, value, payload, channel, range, and response contracts required by the full Climate implementation plan.

- [ ] **Step 1: Correlate each action with isolated records**

For each timeline action:

1. select records inside its timestamp window;
2. compare the pre-action and post-action payloads;
3. identify bytes that changed;
4. verify the inferred field against the inverse action;
5. verify the same format on AC-2 where captured.

An inference is confirmed only when both forward and inverse actions agree, or when a setup-tool status response independently confirms it.

- [ ] **Step 2: Write the protocol-map document**

The document must contain completed evidence tables for:

- power off/on;
- selected cooling/heating mode;
- cooling target encoding and limits;
- heating target encoding and limits;
- current temperature encoding;
- fan low/medium/high;
- swing off/vertical;
- AC channel location;
- command opcode and response opcode;
- status-read request/response, if observed.

Each row includes:

```text
feature | action | opcode | field | encoded value | channel position |
full payload shape | confirming timestamp(s) | confirmed on AC-2
```

Do not leave unresolved mapping cells, guessed values, or private addresses. If a feature is not proven, mark it explicitly as `not confirmed` and exclude it from the subsequent implementation scope.

- [ ] **Step 3: Document command sequencing**

Using capture evidence, specify:

- whether selecting cool/heat while off also powers on;
- whether turn-on preserves the selected mode;
- whether temperature, fan, and swing can be changed safely while off;
- whether one field command or multiple ordered commands are needed;
- which response confirms each command.

- [ ] **Step 4: Self-review the protocol map**

Verify:

- every supported feature has forward and inverse evidence;
- AC-1 and AC-2 channel isolation is proven;
- temperature limits are explicit;
- current temperature source is explicit;
- no private address or raw UDP data is included;
- unsupported or unconfirmed controls are excluded rather than guessed.

- [ ] **Step 5: Commit the protocol map**

```powershell
git add docs/superpowers/specs/2026-07-29-buspro-panel-ac-protocol-map.md
git commit -m "docs: map Enviro panel AC climate protocol"
```

- [ ] **Step 6: Write the Stage B implementation plan**

Invoke `superpowers:writing-plans` again using:

- `docs/superpowers/specs/2026-07-28-buspro-panel-ac-climate-design.md`;
- the committed protocol map;
- exact field/value contracts from the capture.

The Stage B plan must contain literal command payloads and tests for both AC channels. It must not begin until all advertised Climate controls have confirmed mappings.
