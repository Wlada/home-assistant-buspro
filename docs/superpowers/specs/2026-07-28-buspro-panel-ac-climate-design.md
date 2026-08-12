# BusPro Panel AC Climate Design

## Goal

Add two independent Home Assistant climate entities, `AC-1` and `AC-2`,
for two air conditioners controlled through separate AC slots on the same
Enviro panel.

The implementation must first replace broad BusPro debugging with a
targeted AC capture logger, use controlled panel actions to map the
remaining protocol fields, and then extend the existing reusable
`PanelACDevice` model.

## Confirmed system behavior

- Both air conditioners are configured as separate AC slots on the same
  Enviro panel.
- Each panel slot targets its own IR/HVAC device.
- The slots use AC channel 1 and AC channel 2 respectively.
- Both devices use the same Enviro AC protocol version and support an
  HVAC number.
- Home Assistant power commands have physically turned the first air
  conditioner on and off successfully.
- Power changes made directly on the Enviro panel are reflected in Home
  Assistant.
- Power control uses `OperateCode.ControlPanelAC` (`E3D8`).
- Confirmed power responses use `ControlPanelACResponse` (`E3D9`) with
  `[field, value, channel]`, where power uses field `3`.
- The Enviro panel is the source of truth. State must never change
  optimistically.

## Supported scope

Each climate entity will support:

- power on and off;
- HVAC modes `cool` and `heat`;
- the target temperature for the active HVAC mode;
- current room temperature;
- fan modes `low`, `medium`, and `high`;
- vertical swing `off` and `vertical`;
- push-based, bidirectional state synchronization.

The Enviro panel stores separate target temperatures for cooling and
heating. The entity exposes the target for the active mode and preserves
both confirmed values internally.

## Non-goals

- Auto, dry, and fan-only HVAC modes.
- Horizontal swing or fixed louver positions.
- Turbo, sleep, eco, or presets.
- Guessing protocol values, temperature limits, or read-status payloads.
- Refactoring the existing floor-heating protocol model.
- Changing existing floor-heating entity behavior.
- Permanent raw UDP capture.
- Creating separate implementations for the two air conditioners.

Unsupported fields may be parsed and retained later, but they will not be
advertised as Home Assistant capabilities in this phase.

## Selected approach

Use a targeted capture path inside the reusable panel AC protocol layer.

Rejected alternatives:

- Keeping broad raw BusPro logging would preserve excessive unrelated
  traffic and make action-to-telegram correlation difficult.
- Building a permanent diagnostic service would add infrastructure that
  is not required after the protocol is mapped.
- Guessing field values from other HDL integrations would risk sending
  invalid full-state IR commands.

## Phase structure

This phase has two implementation stages.

### Stage A: targeted protocol capture

1. Configure `PanelACDevice` instances for AC channel 1 and channel 2.
2. Add the dedicated logger `buspro.ac_capture`.
3. Log only parsed panel AC-family telegrams associated with those
   configured instances.
4. Disable broad BusPro debug loggers.
5. Remove the temporary hard-coded Enviro raw-datagram capture.
6. Capture controlled state changes from the Enviro panel.
7. Document the confirmed field/value mapping before adding controls.

Temporary switch entities may host the two protocol instances during
capture. They are removed from YAML after both climate entities pass
physical validation.

### Stage B: full climate entities

1. Extend `PanelACDevice` with confirmed full-state fields and commands.
2. Add a dedicated `BusproPanelACClimate` adapter.
3. Configure `AC-1` and `AC-2` as separate climate entities.
4. Validate every supported command and panel-originated update.
5. Remove the temporary AC switch entries and disable capture logging.

## Targeted capture logger

The logger name is:

```text
buspro.ac_capture
```

Home Assistant logging enables this logger alone:

```yaml
logger:
  logs:
    buspro.ac_capture: debug
```

Each record contains only non-secret protocol diagnostics:

- instance name (`AC-1` or `AC-2`);
- direction;
- operation code;
- field;
- value;
- AC channel;
- the parsed AC payload when additional bytes are present.

The logger does not emit:

- UDP source information;
- raw UDP datagrams;
- unrelated BusPro telegrams;
- network credentials;
- user identifiers.

Incoming records are selected by the registered panel address and AC
channel. If both instances share the same panel address, channel matching
prevents state crossover and duplicate state updates.

The first capture pass includes the known panel AC operation-code family
(`E3D8` through `E3DB`). If a confirmed status response does not contain
current temperature, a second pass may temporarily include only parsed
temperature/status telegrams from the same configured panel. It must not
fall back to global BusPro or raw UDP logging.

## Controlled capture procedure

Wait approximately five seconds between actions and record the
approximate action time.

### Full mapping on AC-1

1. Request or refresh the current AC status from the setup tool.
2. Power off to on.
3. Power on to off.
4. Cooling to heating.
5. Heating to cooling.
6. Increase cooling target temperature by one degree.
7. Decrease cooling target temperature by one degree.
8. Increase heating target temperature by one degree.
9. Decrease heating target temperature by one degree.
10. Fan low to medium.
11. Fan medium to high.
12. Fan high to low.
13. Swing off to on.
14. Swing on to off.
15. Determine the configured minimum and maximum cooling target from the
    setup tool or IR-code configuration.
16. Determine the configured minimum and maximum heating target from the
    setup tool or IR-code configuration.

Before capture, record the confirmed power, mode, cooling target, heating
target, fan, swing, and current temperature.

Temperature limits must be read from configuration or observed through a
safe panel/setup-tool boundary test. They must not be inferred from Home
Assistant defaults. Repeated boundary commands are not required if the
setup tool exposes the configured IR-code ranges.

### Protocol confirmation on AC-2

1. Request or refresh current AC status.
2. Change power once.
3. Change target temperature once.
4. Change fan mode once.
5. Change swing once.

AC-2 must use the same field/value mapping while reporting its own AC
channel.

## Protocol state model

Each `PanelACDevice` owns an independent confirmed state:

```text
power: bool | None
selected_mode: cool | heat | None
current_temperature: float | None
cool_target_temperature: float | None
heat_target_temperature: float | None
fan_mode: low | medium | high | None
swing_mode: off | vertical | None
last_confirmed_update: timestamp | None
```

The Home Assistant `hvac_mode` is derived:

- `off` when confirmed power is off;
- `cool` when power is on and the confirmed selected mode is cooling;
- `heat` when power is on and the confirmed selected mode is heating;
- unknown when the required confirmed fields are unavailable.

The selected mode is retained while power is off because the Enviro
configuration resumes the last status when powered on.

The active target temperature is:

- `cool_target_temperature` when the selected mode is cooling;
- `heat_target_temperature` when the selected mode is heating;
- the last confirmed active-mode target while power is off, if the panel
  reports enough information to determine it.

All mappings from field and value bytes to these properties must be
derived from captured telegrams and recorded in the implementation plan.

## Command data flow

```text
Home Assistant service
  -> BusproPanelACClimate async method
  -> PanelACDevice typed field command
  -> Generic / BusPro transport
  -> Enviro panel
  -> IR/HVAC device
  -> confirmed panel response
  -> PanelACDevice state update
  -> registered device callback
  -> async_write_ha_state()
```

Commands do not modify cached state. Only a valid response for the same
configured AC channel updates state.

Command semantics are:

- turn off requests confirmed power off;
- turn on requests power on while preserving the panel's last selected
  mode and other stored settings;
- selecting `cool` or `heat` ensures the device is powered on using the
  smallest command sequence proven by capture;
- temperature, fan, and swing commands issued while power is off are
  supported only if capture proves that the panel stores them safely.

If changing HVAC mode requires more than one field command, the exact
ordering and response expectations must be documented in the
implementation plan before code is written.

## Response handling

The protocol layer:

1. accepts only confirmed AC-family response operation codes;
2. validates payload type and minimum length before indexing;
3. identifies the response field, value, and channel;
4. rejects another AC channel;
5. maps only captured and documented values;
6. updates only fields present in the response;
7. invokes the device callback exactly once per valid state change;
8. ignores malformed or unsupported fields without raising.

An unsupported field may be logged through `buspro.ac_capture` during the
mapping stage but must not alter entity state.

## Startup state and status reads

Initial properties remain unknown until confirmed by the panel.

The capture stage will test the setup tool's refresh/status operation and
map `ReadPanelAC` (`E3DA`) and `ReadPanelACResponse` (`E3DB`) only if the
observed traffic confirms their payload contracts.

If confirmed, each device requests status after startup using the proven
command. If not confirmed, no speculative read command is sent and the
entity waits for the first valid panel response.

## Home Assistant configuration

The existing climate platform remains list-based. Add an optional
`device_type` with a default that preserves floor-heating behavior.

Conceptual configuration:

```yaml
climate:
  - platform: buspro
    devices:
      - address: "<enviro-panel>.<ac-channel-1>"
        name: AC-1
        device_type: panel_ac

      - address: "<enviro-panel>.<ac-channel-2>"
        name: AC-2
        device_type: panel_ac
```

Existing devices without `device_type` continue to construct the current
floor-heating `Climate` model and `BusproClimate` entity.

Panel AC addresses must contain exactly subnet, device, and positive AC
channel components. Exact private addresses remain in local Home
Assistant configuration and are not duplicated in the repository
specification.

## Home Assistant climate adapter

Create `BusproPanelACClimate` as a separate `ClimateEntity`. It must not
inherit floor-heating-specific preset or relay behavior.

The entities use English display names `AC-1` and `AC-2`. Home Assistant
normalizes their entity IDs to `climate.ac_1` and `climate.ac_2`.

The entity exposes:

- `hvac_modes`: `off`, `cool`, `heat`;
- current `hvac_mode`;
- `current_temperature`;
- target temperature and one-degree step, subject to captured limits;
- fan modes `low`, `medium`, `high`;
- swing modes `off`, `vertical`;
- Celsius temperature unit;
- `ClimateEntityFeature.TARGET_TEMPERATURE`;
- `ClimateEntityFeature.FAN_MODE`;
- `ClimateEntityFeature.SWING_MODE`;
- `ClimateEntityFeature.TURN_ON` and `TURN_OFF`;
- no polling;
- availability based on the existing BusPro connection state;
- a stable unique ID derived from panel address and AC channel.

The entity does not infer `hvac_action` from the selected mode. It exposes
an action only if capture proves a distinct, reliable running-state field;
otherwise `hvac_action` remains unavailable. Selected cooling or heating
mode alone is not proof that the compressor is actively running.

It implements the current Home Assistant asynchronous methods for:

- setting HVAC mode;
- setting target temperature;
- setting fan mode;
- setting swing mode;
- turning on and off.

Entity properties perform no I/O and only return confirmed in-memory
state. Device callbacks schedule `async_write_ha_state()`.

## Two-instance isolation

`AC-1` and `AC-2` share implementation code but not mutable state.

Every incoming response and every callback is channel-scoped. Automated
tests must prove that:

- an AC-1 response cannot change AC-2;
- an AC-2 response cannot change AC-1;
- commands contain the selected entity's channel;
- each entity has a distinct stable unique ID.

## Backward compatibility

- Missing climate `device_type` retains current floor-heating behavior.
- Existing presets, relay sensor support, and floor-heating commands are
  unchanged.
- The current AC power switch continues to work during development.
- Switch configuration is removed only after both climate entities pass
  physical validation.
- Existing service commands remain available.

## Error handling

- Invalid climate YAML fails during schema validation.
- Unsupported modes, fan values, swing values, and out-of-range
  temperatures are rejected before transport.
- Transport errors propagate through the existing BusPro path.
- Invalid or mismatched responses do not overwrite the last confirmed
  state.
- No optimistic fallback hides a missing panel response.
- Capture logging is debug-level and disabled after mapping.

## Automated verification

Protocol tests cover:

- every captured field/value mapping;
- literal typed operation codes and payloads for every command;
- no optimistic state changes;
- malformed payloads;
- unsupported values;
- channel isolation;
- status-read behavior if confirmed;
- callback count and stable identifiers.

Climate adapter tests cover:

- schema defaults preserving floor heating;
- construction of two panel AC instances;
- advertised modes and features;
- property delegation;
- async command delegation;
- push callback state writes;
- distinct unique IDs.

Regression verification includes:

- existing floor-heating tests or equivalent adapter tests;
- existing AC power switch tests;
- existing dedicated AC service tests;
- compilation of the component.

## Physical acceptance

Both `AC-1` and `AC-2` must independently pass:

1. startup status or documented unknown startup behavior;
2. power on and off from Home Assistant;
3. cooling and heating selection;
4. target temperature changes in both modes;
5. low, medium, and high fan selection;
6. swing off and vertical;
7. state changes initiated directly on the Enviro panel;
8. no state crossover between the two entities.

The temporary switch entities and capture logger are removed only after
all acceptance checks pass. The branch is not merged or tagged before
physical acceptance is complete.
