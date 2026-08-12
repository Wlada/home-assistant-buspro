# BusPro Enviro Panel AC Climate Stage B Plan

## Goal

Replace the two temporary panel-AC power switches with two independent Home
Assistant climate entities backed by confirmed Enviro state. The first release
supports power, cool/heat mode, active-mode target temperature, shared room
temperature, and low/medium/high fan. Swing and `hvac_action` are excluded.

## Preconditions

Before target-temperature controls are enabled, obtain explicit installed-IR
profile limits and configure `min_temp` and `max_temp`. Do not use Home
Assistant defaults or a typical consumer-AC range.

Before a cool/heat selection is allowed to power on an off AC, physically
confirm one safe sequence:

1. power the AC off;
2. change the selected mode on the Enviro panel;
3. confirm whether the AC remains off;
4. power it on and confirm which mode is resumed.

Until this is confirmed, `turn_on` may resume the last stored mode, but setting
`cool` or `heat` from confirmed `off` is not implemented speculatively.

## Fixed protocol contracts

All AC writes use `OperateCode.ControlPanelAC` (`E3D8`) with:

```text
power off       [3, 0, channel]
power on        [3, 1, channel]
cool mode       [6, 0, channel]
heat mode       [6, 1, channel]
cool target T   [4, T, channel]
heat target T   [7, T, channel]
fan low         [5, 1, channel]
fan medium      [5, 2, channel]
fan high        [5, 3, channel]
```

Every write is non-optimistic and waits for matching `E3D9
[field, value, channel]` state. Stored status uses `E3DA
[field, channel, channel]` requests and `E3DB [field, value, channel]`
responses. Status fields are exactly `3, 4, 5, 6, 7`.

Current room temperature is shared by both climate entities. One protocol
`Sensor` uses the configured Enviro temperature channel and existing
`E3E7 [temperature_channel]` / `E3E8 [temperature_channel, temperature, ...]`
support. The climate platform must create one shared temperature device per
panel setup, not one poller per AC slot.

## Task 1: Extend the reusable panel AC state model

Files:

- modify `pybuspro/devices/panel_ac.py`;
- modify `tests/test_panel_ac_device.py`.

Add confirmed properties:

```text
is_on
selected_mode       # "cool" | "heat" | None
cool_target_temperature
heat_target_temperature
target_temperature  # target for selected mode
fan_mode             # "low" | "medium" | "high" | None
device_identifier
```

Use table-driven RED tests for `E3D9` and `E3DB` field responses. Each test
must prove:

- matching channel updates only the addressed property;
- the same value does not schedule a redundant callback;
- a changed value schedules exactly one callback;
- another channel, malformed payload, unknown field, invalid power/mode/fan,
  and unsupported fan value 0 do not corrupt supported state;
- separate AC-1 and AC-2 instances never change each other's state.

Field 5 value 0 may be retained as an unsupported raw state for diagnostics,
but `fan_mode` remains `None` and auto is not advertised.

Run:

```powershell
python tests\test_panel_ac_device.py -v
```

## Task 2: Add typed writes and status reads

Files:

- modify `pybuspro/devices/panel_ac.py`;
- modify `tests/test_panel_ac_device.py`.

Add async protocol methods:

```text
set_on()
set_off()
set_mode("cool" | "heat")
set_target_temperature(integer_degrees)
set_fan_mode("low" | "medium" | "high")
read_status()
```

`set_target_temperature` requires a confirmed selected mode. It chooses field
4 for cool and field 7 for heat. It rejects booleans, non-integral values, and
values outside the configured entity limits before transport. No command
changes cached state.

`read_status()` sends five `E3DA` requests in field order 3, 4, 5, 6, 7, each
with `[field, channel, channel]`. Tests assert exact opcode objects and payloads
for both channel 1 and channel 2.

Mode, target, and fan writes are allowed only while confirmed on until the
off-state storage behavior is physically verified. Power writes remain
available from unknown or off state.

Run targeted protocol, switch, and service regressions:

```powershell
python tests\test_panel_ac_device.py -v
python tests\test_panel_ac_switch.py -v
python tests\test_panel_ac_service.py -v
```

## Task 3: Add panel AC climate configuration and adapter

Files:

- modify `climate.py`;
- create `tests/test_panel_ac_climate.py`.

Extend each climate device entry with:

```yaml
device_type: panel_ac
address: "<panel>.<ac-channel>"
temperature_channel: 1
min_temp: <confirmed installed-profile minimum>
max_temp: <confirmed installed-profile maximum>
```

Missing `device_type` defaults to the existing floor-heating implementation.
Panel AC entries require exactly three positive address components, a positive
temperature channel, integer Celsius limits, and `min_temp < max_temp`.
Floor-heating-only options retain their existing behavior.

For panel AC entries sharing a panel and temperature channel, create one
shared `Sensor(device="temperature_channel")`. Construct one
`PanelACDevice` and one `BusproPanelACClimate` per AC channel.

The adapter exposes:

```text
hvac_modes: off, cool, heat
fan_modes: low, medium, high
temperature unit: Celsius
target step: 1
supported features: TARGET_TEMPERATURE, FAN_MODE, TURN_ON, TURN_OFF
should_poll: false
unique_id: PanelACDevice.device_identifier
current_temperature: shared Sensor.temperature
```

It does not expose swing, presets, or `hvac_action`.

Adapter commands delegate without writing HA state optimistically:

- off → `set_off()`;
- turn on → `set_on()` and preserve stored mode;
- cool/heat while on → `set_mode()`;
- target → validate current active-mode limits, then
  `set_target_temperature()`;
- fan → `set_fan_mode()`.

Protocol and shared-temperature callbacks call `async_write_ha_state()`.
`async_added_to_hass` registers callbacks and requests each AC's stored status.
The shared Sensor supplies its existing single delayed temperature read.

Climate tests cover schema compatibility, invalid YAML, two-channel
construction, shared temperature instance reuse, exact advertised features,
property delegation, callback writes, command delegation, no optimistic
writes, and distinct stable IDs.

Run:

```powershell
python tests\test_panel_ac_climate.py -v
python tests\test_panel_ac_device.py -v
python tests\test_panel_ac_switch.py -v
python tests\test_panel_ac_service.py -v
python tests\test_sensor_device.py -v
python tests\test_temperature_channel_control.py -v
```

## Task 4: Full regression and review

Run:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q pybuspro tests climate.py
git diff --check
```

Review the diff for these invariants:

- existing floor-heating setup and entity code remains behaviorally unchanged;
- no private address is committed;
- no state is optimistic;
- every response is channel-scoped;
- target limits originate in explicit local configuration;
- no swing capability or command exists;
