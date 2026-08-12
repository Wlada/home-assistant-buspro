# Buspro Unknown Sensor Telegram Capture Design

## Goal

Identify the exact Buspro operation codes and payload layouts emitted by the
three indoor `HDL-MSP07M.4C` PIR devices when HDL Setup Pro simulates known
motion, illuminance, and temperature values.

This is a diagnostic-only change. It must not change Home Assistant entity
state, polling, decoding, or availability.

## Evidence

- Indoor motion polling currently sends `DB00` without the required Logic No,
  and the devices return a valid `DB01` payload whose movement byte remains
  zero even while local HDL logic turns the light on.
- Indoor measurement polling sends `1645`, but the devices do not return the
  expected `1646` measurement response.
- The active enum already hints at several related response families, while
  its archived list contains separate current-brightness and temperature
  operation codes. Their payload formats must be observed on the installed
  firmware before production decoding is implemented.
- Outdoor Sensor-in-One polling and lux decoding work, so it remains outside
  this diagnostic change.

## Approaches Considered

1. **Capture unmapped indoor sensor telegrams (selected).**
   Record the operation-code bytes and payload for telegrams whose operation
   code is not currently recognized. This observes the real installed
   firmware without guessing its decoder.
2. Activate suspected operation codes and guess their payload layouts.
   This is faster only if every assumption is correct and risks repeated
   restarts or false Home Assistant state.
3. Capture full network traffic from HDL Setup Pro.
   This is precise but unnecessarily broad and would include network and
   device metadata that are not needed for this diagnosis.

## Design

`SensorDiagnosticCapture` gains a dedicated raw-response record method. A
`Sensor` with device profile `pir` and diagnostic role `temperature` records
an incoming telegram only when its parsed operation code is unknown.

The operation code is derived from the two operation-code bytes already
present in the parsed telegram's datagram. The record contains only:

- timestamp and `raw_response` direction;
- configured entity name, device profile, and diagnostic role;
- operation code as four hexadecimal characters;
- payload length and payload bytes.

It must not contain Buspro source/target addresses, gateway/UDP addresses,
raw datagrams, IP addresses, or Home Assistant entity/device identifiers.
Only the temperature-role instance records unknown responses, preventing
duplicate records from the motion and lux entities for the same physical
sensor. The existing 500-record bounded file remains in use.

Unknown telegrams remain ignored by production state decoding. Malformed or
too-short datagrams are ignored by the diagnostic hook.

## Test and Runtime Procedure

Automated tests first prove that:

1. a valid unknown PIR telegram creates one privacy-scoped raw response;
2. known telegrams do not create duplicate raw records;
3. malformed datagrams are ignored;
4. no address or raw-network fields are written.

After the full local suite passes, deploy the diagnostic change and restart
Home Assistant once. In HDL Setup Pro, simulate one distinctive value at a
time on Basement:

1. motion off, then on;
2. illuminance at a distinctive value such as 123 lux;
3. temperature at a distinctive value such as 25 degrees Celsius.

Read the bounded capture, correlate each changed payload byte with the known
simulated value, and then implement the production polling/decoder fix in a
separate test-driven change. Remove the raw diagnostic hook after the final
fix is verified.
