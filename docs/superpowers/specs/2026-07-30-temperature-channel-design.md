# Buspro Temperature Channel Design

## Goal

Restore reliable Home Assistant temperature readings for:

- the channel-based probes connected to the two HDL dry-contact temperature modules;
- the built-in temperature sensor in the HDL Enviro panel.

The change must not alter the working PIR/Outdoor `sensors_in_one` path or enable
temperature broadcasting on any HDL device.

## Confirmed Protocol

Bus Monitor evidence confirms that both affected device families use the same
designated-address temperature protocol:

- `E3E7` reads one temperature channel and carries the channel number as its
  payload;
- `E3E8` returns the channel number followed by a signed whole-degree
  temperature;
- some dry-contact module responses append an optional four-byte
  little-endian float. The whole-degree field is present in both the
  dry-contact and Enviro responses and is the canonical value for this change.

The Enviro built-in sensor uses temperature channel 1. Existing dry-contact
probe addresses already include their channel number.

## Architecture

Add a dedicated Buspro sensor profile named `temperature_channel`.

The profile is deliberately separate from `sensors_in_one`, `pir`, and `dlp`.
When read, it sends `E3E7` to the configured two-part device address with the
third address component as the payload channel. It accepts only `E3E8`
responses from the configured source device whose returned channel matches the
configured channel.

The existing `E3E5` broadcast handler must not update
`temperature_channel` entities. This profile is polling-only.

## Configuration

- Change the six dry-contact probe entities from `sensors_in_one` to
  `temperature_channel`; preserve their existing three-part addresses and scan
  intervals.
- Change the Enviro temperature entity from `dlp` to
  `temperature_channel`, append channel 1 to its existing two-part address, and
  remove its legacy `-20` offset.
- Do not change PIR, Outdoor, motion, lux, climate, or HDL Setup settings.

## Data Flow

1. Home Assistant schedules the sensor update at its existing scan interval.
2. The sensor builds an `E3E7` telegram for the configured device and includes
   the configured temperature channel in the payload.
3. The module returns `E3E8`.
4. The integration rejects malformed payloads and responses for other
   channels.
5. The signed temperature byte is decoded and the matching Home Assistant
   entity is updated.

## Error Handling

- A missing channel prevents a `temperature_channel` read from being sent.
- An `E3E8` payload shorter than two bytes is ignored.
- A response for another channel is ignored.
- Broadcast temperature messages are ignored by this profile.
- Existing behavior for every other sensor profile remains unchanged.

## Verification

Add tests for:

- the `E3E7` opcode and channel payload;
- dispatch from `temperature_channel` to the new read control;
- positive and negative `E3E8` temperatures;
- optional trailing float bytes;
- malformed and wrong-channel responses;
- ignored `E3E5` broadcasts for this profile;
- unchanged `sensors_in_one` request and response behavior.

Run the complete component test suite, deploy the same verified source state to
the active Home Assistant component, restart Home Assistant, and confirm at
least one simulated dry-contact probe value plus the Enviro value before any
merge, push, or working-state tag.
