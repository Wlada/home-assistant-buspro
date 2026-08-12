# Buspro PIR runtime recovery design

Date: 2026-07-29

## Goal

Restore Home Assistant motion updates from the three installed indoor PIR
sensors, correct the outdoor Sensor-in-One temperature encoding, and collect
enough targeted runtime evidence to finish indoor temperature and illuminance
support without guessing the installed devices' measurement protocol.

The local HDL logic that switches lights remains untouched.

## Confirmed runtime findings

- The indoor PIR devices still detect motion because their local HDL logic
  switches the configured light.
- Home Assistant motion history stopped updating after the integration
  restart.
- The recent multisensor change replaced the indoor PIR motion-status request
  (`0xDB00`) with the legacy full-sensor request (`0x1645`).
- The installed indoor devices do not currently produce a Home Assistant
  update in response to that changed polling path.
- The outdoor Sensor-in-One device returns temperature using the Buspro
  offset encoding, but the current parser exposes the encoded byte directly.

## Approach

Use a two-stage recovery.

### Stage 1: restore known behavior

For indoor motion entities, restore the previously working motion-status
request (`0xDB00`) and keep decoding its response (`0xDB01`).

Do not make the generic `device: pir` profile silently choose one request for
all measurements. Motion polling and measurement polling have different
protocol requirements on the installed devices.

Decode Buspro sensor temperatures in the protocol layer by applying the
documented `raw - 20` conversion exactly once. Remove matching YAML offsets
where they would otherwise apply the conversion a second time.

### Stage 2: capture indoor measurement evidence

Add narrowly scoped diagnostic logging for recognized sensor-status opcodes
and payload bytes from the three indoor sensor addresses. Do not log gateway
addresses, UDP endpoints, credentials, unrelated devices, or full raw
datagrams.

After restart:

1. confirm each indoor motion entity changes from clear to detected and back;
2. observe which recognized status opcode and payload layout the indoor
   devices actually emit;
3. add the smallest decoder and polling-profile change supported by that
   evidence;
4. remove or reduce temporary diagnostic logging after the measurement path
   is verified.

## Entity behavior

- A motion entity is unavailable until it has received at least one valid
  motion response after startup. It must not present its default `False`
  value as fresh data.
- Temperature and illuminance remain unavailable until their respective
  values have been decoded from a valid telegram.
- A valid zero-lux value remains available and is exposed as `0`.
- Existing entity names and unique IDs remain stable.

## Testing

Use test-driven development:

1. a failing regression test proves an indoor motion profile sends `0xDB00`;
2. a failing entity test proves motion is unavailable before its first valid
   response;
3. a failing temperature test proves an encoded Sensor-in-One byte is
   converted with `raw - 20`;
4. a configuration-oriented test or focused assertion prevents a second
   YAML offset from being applied;
5. existing parser, platform, and full Buspro tests remain green.

Compile all changed Python files and inspect the focused diff before asking
for a Home Assistant restart.

## Deployment

Stage 1 requires one user-approved Home Assistant restart after offline tests
pass. Runtime verification covers all three indoor motion entities and the
outdoor temperature.

Indoor temperature and illuminance are completed only after the targeted
capture identifies the real opcode and payload. No local HDL device settings,
lighting logic, automations, or unrelated integrations are changed.

