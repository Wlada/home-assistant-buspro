# BusPro Enviro AC Power Switch MVP Design

## Goal

Create a Home Assistant `SwitchEntity` for one Enviro panel AC channel. The
entity sends the already verified panel power command and treats the Enviro
panel response as the only source of truth.

The MVP must provide a safe foundation for a later full Home Assistant climate
entity without modifying the existing floor-heating climate behavior.

## Confirmed protocol behavior

- The target is an Enviro panel plus a positive AC channel number.
- `OperateCode.ControlPanelAC` is `E3D8`.
- ON payload is `[3, 1, channel]`.
- OFF payload is `[3, 0, channel]`.
- The Enviro panel changes its state and sends the corresponding full-state IR
  command to the configured emitter.
- The successful manual ON test changed both the physical AC and the Enviro
  panel state.
- `ControlPanelACResponse` is `E3D9` and carries the confirmed power-state
  response used by the MVP.

## Non-goals

- Target or current temperature.
- HVAC mode selection.
- Fan speed, swing, turbo, sleep, or eco controls.
- An AC `ClimateEntity` in this phase.
- An unverified `ReadPanelAC` request.
- Optimistic state.
- Changes to the existing floor-heating `ClimateEntity` or protocol classes.
- Direct universal-switch commands to an IR emitter.

## Configuration

Extend the existing BusPro switch platform with an optional `device_type` per
device:

```yaml
switch:
  - platform: buspro
    devices:
      "1.49.1":
        name: Klima
        device_type: panel_ac
```

The address format remains `subnet.device.channel`.

- Missing `device_type` defaults to `relay` and preserves current behavior.
- `device_type: relay` explicitly selects the existing relay switch.
- `device_type: panel_ac` selects the new Enviro AC power switch.
- Unknown device types, malformed addresses, and non-positive channels are
  rejected during Home Assistant schema validation.

## Architecture

### Protocol device

Add `pybuspro/devices/panel_ac.py` with `PanelACDevice`, derived from the
existing `Device` base class.

`PanelACDevice` owns:

- the Enviro panel address;
- the AC channel;
- confirmed power state as `True`, `False`, or `None`;
- registration for telegram callbacks from the panel;
- creation and sending of typed `ControlPanelAC` telegrams;
- parsing and filtering of `ControlPanelACResponse` telegrams;
- notification of registered Home Assistant device-update callbacks.

Its public MVP interface is:

- `async set_on()`;
- `async set_off()`;
- `is_on -> bool | None`;
- `device_identifier -> str`.

The identifier is prefixed by the physical type and derived from the panel and
channel, for example `panel-ac-1-49-1`. This prevents collisions with a relay
switch configured at the same numeric address.

The class name and boundary deliberately describe the physical panel AC device,
not the MVP widget. Later climate support extends this class with verified
state fields and commands instead of replacing the transport layer.

### Home Assistant entity

Add `BusproPanelACSwitch` to the existing `switch.py` platform. It is a thin
adapter around `PanelACDevice`:

- `available` follows the existing BusPro connection state;
- `is_on` returns the confirmed device state, including `None` while unknown;
- `async_turn_on` and `async_turn_off` delegate to the device;
- a device callback calls `async_write_ha_state()`;
- `unique_id` delegates to the stable device identifier;
- polling remains disabled.

The existing `BusproSwitch` class and relay device construction remain
unchanged for existing configurations.

## Command data flow

ON:

```text
Home Assistant toggle
  -> BusproPanelACSwitch.async_turn_on
  -> PanelACDevice.set_on
  -> E3D8 payload [3, 1, channel]
  -> Enviro panel and IR emitter
  -> E3D9 response
  -> validate panel, field, power value, and channel
  -> confirmed state True
  -> async_write_ha_state
```

OFF uses the same flow with payload `[3, 0, channel]` and confirmed state
`False`.

Calling `set_on` or `set_off` does not modify the cached state. The state changes
only after a valid response is received.

## Response parsing

A telegram updates the entity only when all conditions are satisfied:

- opcode is `OperateCode.ControlPanelACResponse`;
- source or target routing already matches the configured panel through the
  existing device callback registration;
- payload contains at least three bytes;
- payload field byte is `3`;
- payload power byte is exactly `0` or `1`;
- payload channel equals the configured AC channel.

Other channels, other fields, unknown power values, and short payloads are
ignored. Malformed matching responses are logged at debug level without raising
from the receive callback.

## Startup and availability

The MVP does not send an unverified status-read request. After Home Assistant
starts, the entity is available when the BusPro connection is available but its
state is `unknown` until the first valid matching `E3D9` response.

A user command may produce that first response. A direct change on the Enviro
panel also updates Home Assistant when the panel broadcasts the same confirmed
response format.

## Error handling and logging

- YAML schema validation rejects invalid device configuration before entity
  construction.
- Transport exceptions propagate through the existing BusPro send path.
- No optimistic fallback masks a transport or panel failure.
- Outbound debug logs contain the panel address, channel, and requested power.
- Valid response debug logs contain the channel and confirmed power.
- Malformed responses log only the reason and non-sensitive protocol metadata.
- Existing temporary raw-datagram warning logging is independent of this MVP
  and may later be lowered to debug level.

## Automated tests

Tests must verify:

1. Existing switch configuration without `device_type` still constructs the
   existing relay device and entity.
2. Explicit `device_type: relay` behaves identically.
3. `device_type: panel_ac` constructs `PanelACDevice` and
   `BusproPanelACSwitch` with the parsed panel address and channel.
4. Invalid device type, malformed address, zero channel, and negative channel
   are rejected.
5. ON sends typed opcode `E3D8` with literal payload `[3, 1, channel]`.
6. OFF sends typed opcode `E3D8` with literal payload `[3, 0, channel]`.
7. State remains `None` immediately after a command without a response.
8. Valid ON and OFF `E3D9` responses update state and notify callbacks.
9. Responses for another channel do not update state.
10. Short payloads, wrong fields, and invalid power values do not update state.
11. Device identifiers are stable and do not collide with relay identifiers.
12. Existing panel AC service tests and floor-heating behavior remain unchanged.

## Manual verification

1. Restart Home Assistant and confirm the entity loads without BusPro errors.
2. Confirm its initial state is unknown before a matching response.
3. Toggle ON and verify the physical AC and Enviro panel turn on.
4. Confirm Home Assistant changes to ON only after the Enviro response.
5. Toggle OFF and confirm all three states change to OFF.
6. Change power directly from the Enviro panel and confirm Home Assistant
   follows it.
7. Confirm existing relay switches and floor-heating entities still operate.

## Evolution to a full climate widget

`PanelACDevice` remains the shared physical-device model. A later
`BusproPanelACClimate` consumes the same callbacks and state while the MVP power
switch remains an optional auxiliary entity, potentially disabled by default.

The full climate entity must not be implemented until logs establish each
field independently. Required captures are listed below.

### Initial-state query

Capture the request and response used by the Enviro panel or HDL Setup Tool to
read one AC channel. Establish the exact `E3DA/E3DB` payload layout, channel
position, and whether separate fields or one full-state response are used.

### Power

Capture OFF to ON and ON to OFF, including a change initiated directly on the
panel. Confirm power and channel positions in every response variant.

### HVAC modes

Capture one isolated transition for every supported mode: cool, heat, auto,
dry, and fan-only. Record modes not offered by the installed AC as unsupported.

### Temperature

Capture target-temperature changes by exactly one degree and at low and high
allowed values. Separately identify target temperature, panel/current
temperature, units, encoding, valid range, and broadcast-temperature routing.

### Fan

Capture isolated changes for auto, low, medium, and high fan speeds, limited to
speeds actually offered by the panel.

### Optional capabilities

Capture swing, turbo, sleep, and eco independently if exposed by the panel.
Unsupported capabilities must not be advertised by Home Assistant.

### Capture procedure

For each capture, record:

- approximate action time;
- AC channel;
- panel state before the action;
- exactly one user action;
- panel state after the action;
- whether the action originated in the panel or the setup tool.

The log supplies telegram direction, opcode, and payload. Network addresses,
credentials, and unrelated Home Assistant state must not be copied into design
notes or issues.

## Completion criteria

The MVP is complete when automated tests pass, Home Assistant loads the entity,
ON and OFF are physically verified, panel-originated power changes update the
entity, existing relay and heating behavior is unchanged, and the implementation
is committed on the dedicated feature branch.