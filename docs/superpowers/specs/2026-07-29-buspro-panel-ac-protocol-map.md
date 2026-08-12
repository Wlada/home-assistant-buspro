# BusPro Enviro Panel AC Protocol Map

## Scope and evidence

This map records only parsed, address-free telegram evidence collected from
the two configured Enviro AC slots. AC-1 supplied the controlled forward and
inverse transitions. AC-2 supplied an independent channel-2 status sequence,
and its power control was physically verified through Home Assistant.

Swing is intentionally excluded from the first climate implementation at the
user's request. Temperature limits and mode changes while powered off were not
observed and are not guessed.

## Operation-code contracts

| Purpose | Opcode | Payload |
| --- | --- | --- |
| Write one AC field | `E3D8` (`ControlPanelAC`) | `[field, value, ac_channel]` |
| Confirm one written or panel-originated field | `E3D9` (`ControlPanelACResponse`) | `[field, value, ac_channel]` |
| Read one stored AC field | `E3DA` (`ReadPanelAC`) | `[field, ac_channel, ac_channel]` |
| Return one stored AC field | `E3DB` (`ReadPanelACResponse`) | `[field, value, ac_channel]` |
| Read one temperature channel | `E3E7` (`ReadTemperature`) | `[temperature_channel]` |
| Return one temperature channel | `E3E8` (`ReadTemperatureResponse`) | `[temperature_channel, signed_temperature, ...]` |

`E3D8` write and `E3D9` confirmation are physically proven end-to-end for
power. The same field/value response shape is proven for mode, target, and fan
through direct panel changes. Their first Home Assistant writes remain a
physical-acceptance step.

## Confirmed field and value map

| Feature | Action | Opcode | Field | Encoded value | Channel position | Full payload shape | Evidence (local time) | AC-2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Power | off | `E3D9` | `3` | `0` | index 2 | `[3, 0, channel]` | 02:03:47.787 | write physically verified |
| Power | on | `E3D9` | `3` | `1` | index 2 | `[3, 1, channel]` | 02:03:38.968, 02:03:50.447 | status and write verified |
| HVAC mode | cool | `E3D9` | `6` | `0` | index 2 | `[6, 0, channel]` | 02:03:54.288 and inverse samples | status shape verified |
| HVAC mode | heat | `E3D9` | `6` | `1` | index 2 | `[6, 1, channel]` | 02:03:56.348 and inverse samples | status shape verified |
| Cooling target | setpoint | `E3D9` | `4` | direct integer degrees Celsius | index 2 | `[4, degrees, channel]` | 26→27→26 and 26→25→26 at 02:04:10–02:04:50 | status shape verified |
| Heating target | setpoint | `E3D9` | `7` | direct integer degrees Celsius | index 2 | `[7, degrees, channel]` | 24→25→24 at 02:04:17–02:04:18 | status shape verified |
| Fan | auto | `E3D9` | `5` | `0` | index 2 | `[5, 0, channel]` | 02:04:24.248 | parsed only; not advertised |
| Fan | low | `E3D9` | `5` | `1` | index 2 | `[5, 1, channel]` | 02:04:27.428 | status shape verified |
| Fan | medium | `E3D9` | `5` | `2` | index 2 | `[5, 2, channel]` | 02:04:26.408 and 02:04:32.787 | status shape verified |
| Fan | high | `E3D9` | `5` | `3` | index 2 | `[5, 3, channel]` | repeated before and after fan sequence | status shape verified |
| Current room temperature | channel read | `E3E8` | temperature channel `1` | signed direct integer degrees Celsius | index 0 | `[1, degrees, ...]` | repeated value `27` at 02:25–02:27 | shared channel confirmed |
| Swing | any | — | — | — | — | — | not captured | excluded |

The user independently confirmed the fan labels for values 0 through 3. Only
low, medium, and high are advertised in the first climate entity; auto remains
a parseable but unsupported panel state.

The earlier displayed room value of 28 °C disproved E3DB field 8 as current
temperature. A later direct E3E8 response repeatedly returned 27 °C for
temperature channel 1, establishing E3E8 as the current-temperature source.

## Status reads

The setup/status reader requests each stored field separately. For AC channel
1 it emitted `[field, 1, 1]`; for AC channel 2 it emitted `[field, 2, 2]`.
The panel answered with `E3DB [field, value, channel]`.

The first climate implementation needs fields 3, 4, 5, 6, and 7. Fields 8 and
19 were stable in all captures but have no confirmed climate semantics and
must not mutate entity state.

A status response is accepted only when its channel equals the configured AC
slot. This isolation was observed continuously: AC-1 records used channel 1
and AC-2 records used channel 2, without cross-instance status records after
the channel-scoping fix.

## AC-2 abbreviated confirmation

The independent AC-2 status repeatedly returned:

- power `1`;
- cooling target `26`;
- fan `3` (high);
- mode `0` (cool);
- heating target `26`;
- channel `2` on every response.

This confirms the shared field layout and channel position. Non-power writes
on AC-2 remain part of physical acceptance rather than capture mapping.

## Command and state rules

- State is non-optimistic. An E3D8 send never changes cached state.
- E3D9 and E3DB update only the addressed field for the matching AC channel.
- One valid changed field schedules one device callback.
- Malformed payloads, another channel, unknown fields, and unsupported values
  are ignored without clearing the last confirmed value.
- Power-on preserves the panel's stored mode and setpoints in observed status,
  but the exact mode-selection sequence from a confirmed-off state was not
  tested.
- Temperature, fan, and mode writes while off were not tested and must not be
  claimed safe.

## Explicitly unresolved

- Minimum and maximum cooling target: not confirmed.
- Minimum and maximum heating target: not confirmed.
- Selecting cool or heat while power is off: not confirmed.
- Writing temperature or fan while power is off: not confirmed.
- Swing field and values: excluded from the first implementation.
- Compressor/running state: not available; no `hvac_action` is exposed.
