# Buspro multisensor readings design

Date: 2026-07-29

## Goal

Restore reliable Home Assistant readings from the installed HDL multisensors
without changing the lighting logic that runs locally on the HDL Buspro
devices.

The supported measurements are:

- `HDL-MSOUT.4W`: temperature, illuminance, humidity, and wave/motion.
- `HDL-MSP07M.4C`: temperature, illuminance, and PIR motion.

Dry contacts are not connected and will not be exposed. Existing local
brightness-and-wave entrance-light logic remains unchanged.

## Root cause

The current parser has several related defects:

- `ReadSensorsInOneStatusResponse` reads temperature and movement but skips
  the two-byte illuminance field and humidity.
- Other sensor response paths calculate illuminance by adding the high and low
  bytes instead of decoding an unsigned 16-bit big-endian value.
- The success marker in the legacy sensor response is compared as an enum
  object even though telegram payload values are integers.
- Sensor-in-One broadcast opcode `0x1630` is not represented in `OperateCode`,
  so those broadcasts cannot reach a device parser.
- A missing illuminance response is surfaced as `0 lux`, which makes missing
  data indistinguishable from a real darkness reading.

The HDL protocol defines Sensor-in-One illuminance as a two-byte value with a
range of 0-5000 lux. The installed integration's recent panel-AC changes do
not alter sensor traffic, and the upstream project has no recent sensor
change that explains the regression.

## Approach

Keep the existing YAML-platform architecture and make the smallest
model-aware extension inside the parser and entity platform.

### Protocol decoding

Add one private unsigned 16-bit big-endian decoder and use it consistently for
all illuminance fields:

```text
lux = (high_byte << 8) | low_byte
```

Decode the legacy PIR response family (`0x1644`, `0x1646`, and `0x1647`) using
its existing layout. Correct the success-marker comparison for `0x1646`.

Decode the Sensor-in-One response (`0x1605`) and broadcast (`0x1630`) using
the protocol layout:

| Field | Payload position |
| --- | ---: |
| success/type marker | 0 |
| temperature | 1 |
| illuminance high/low | 2-3 |
| humidity | 4 |
| movement/wave | 7 |

Only update a value when the payload contains all bytes required for that
value. A short or malformed telegram must be ignored safely and must not
erase the last valid state.

### Device profiles

Continue using the existing configuration values as protocol selectors:

- `device: pir` uses the legacy PIR status request/response family.
- `device: sensors_in_one` uses the Sensor-in-One request/response family.

Composite sensor measurements use the base HDL device address. UV numbers
such as 252-255 identify internal virtual-switch functions and are not
measurement channels. They will not be used to select temperature,
illuminance, humidity, or motion fields.

Existing genuinely channel-based temperature modules remain unchanged.

### Home Assistant entities

Retain the existing temperature, illuminance, and motion entities and their
entity IDs where possible.

Add `humidity` to the Buspro sensor platform with:

- Home Assistant humidity device class;
- `%` unit of measurement;
- measurement state class;
- availability only after a valid humidity value is received.

Illuminance remains unavailable until a valid telegram is received. A real
zero-lux measurement remains valid and is reported as `0`.

Update YAML only where an installed composite sensor currently uses a UV
number as if it were a measurement channel or uses the wrong protocol
profile. Do not change unrelated modules, entity names, automation entity
IDs, or local HDL logic.

## Testing

Use test-driven development with focused parser tests:

1. Sensor-in-One read response decodes temperature, lux above 510, humidity,
   and movement.
2. Sensor-in-One broadcast response decodes the same values.
3. Legacy PIR read response decodes 16-bit lux and recognizes the integer
   success marker.
4. Legacy PIR broadcasts decode 16-bit lux.
5. Zero lux remains a valid reading.
6. Short payloads do not raise and do not overwrite the last valid reading.
7. A humidity entity exposes the correct unit, device class, state class, and
   availability behavior.

Run the new tests, the existing Buspro test suite, Python compilation, and a
Home Assistant configuration check before requesting a restart.

## Deployment and verification

No live HDL device settings or local lighting logic are changed by this work.
After all offline checks pass:

1. Present the exact code and YAML diff.
2. Restart Home Assistant only after user confirmation.
3. Verify that outdoor temperature, lux, humidity, and motion update.
4. Verify that each indoor PIR reports temperature, lux, and motion.
5. Confirm that the existing local entrance-light logic still operates
   independently of Home Assistant.

## Out of scope

- Replacing the YAML integration with config entries or auto-discovery.
- Exposing unused dry contacts, UV switches, gas, or air-quality fields.
- Modifying local HDL brightness/wave lighting logic.
- Refactoring unrelated Buspro device classes.
