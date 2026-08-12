# HDL Buspro for Home Assistant

A community Home Assistant integration for controlling and monitoring HDL
Buspro installations over a local UDP network.

This project is a fork of
[eyesoft/home_assistant_buspro](https://github.com/eyesoft/home_assistant_buspro)
with additional device support and protocol work.

## Features

- lights, including dimmable channels
- switches and relay outputs
- temperature, humidity, illuminance, motion, and contact sensors
- covers and blinds
- floor-heating climate devices
- supported Enviro panel AC channels
- Buspro scenes, universal switches, and low-level message services

The integration communicates directly with one HDL Buspro gateway. It does not
use a cloud service.

## Reference installation

This integration is developed and tested against the following residential HDL
Buspro installation. This inventory documents the reference system; inclusion
does not imply that every feature of every listed device is exposed by the
integration.

### Climate control

An `HDL-MPTLC43.46-A` Enviro 4.3-inch touch panel provides the user interface
for both floor heating and air conditioning.

- Floor heating has six independent channels controlled by an
  `HDL-MFH06.432`. Enviro operates as the slave control panel for the heating
  system.
- Air-conditioning modes and setpoints are selected through Enviro. Commands
  are delivered to the physical air-conditioning units over infrared by two
  `SB-IR-EM` transmitters with current detection.

### Hardware inventory

Site-specific room names, Buspro addresses, and network addresses are omitted.

| Quantity | Model | Installed role |
| ---: | --- | --- |
| 1 | `HDL-MRCU.433` | Home control unit |
| 1 | `HDL-MDLED0605.432` | 6-channel, 5 A LED dimming module |
| 2 | `HDL-MR0810.432` | 8-channel, 10 A relay module |
| 1 | `HDL-MFH06.432` | 6-channel floor-heating controller |
| 2 | `HDL-MSD04T.40` | 4-zone dry-contact module with temperature sensor |
| 1 | `HDL-MS24.232` (`SB-DN-DRY-24Z`) | 24-zone dry-contact module |
| 5 | `HDL-MW02.431` | 2-channel curtain controller |
| 2 | `SB-IR-EM` | Smart IR transmitter with current detection |
| 3 | `HDL-MSP07M.4C` | PIR, temperature, and illuminance sensor |
| 1 | `HDL-MSOUT.4W` | Microwave motion sensor |
| 1 | `HDL-MPTLC43.46-A` | Enviro 4.3-inch touch panel |
| 1 | `HDL-MCLog.431` | Logic and timer module |
| 1 | Model not recorded | Buspro IP gateway/interface |

## Installation

This repository uses an integration-in-repository-root layout supported by its
`hacs.json` metadata.

### HACS custom repository

1. In HACS, open **Integrations** and add
   `https://github.com/Wlada/home-assistant-buspro` as a custom repository with
   category **Integration**.
2. Install **HDL Buspro**.
3. Restart Home Assistant.
4. In **Settings > Devices & services**, add **HDL Buspro** and enter the local
   IP address and UDP port of the gateway.

### Manual installation

1. Download or clone the repository.
2. Copy the integration contents to `/config/custom_components/buspro`.
3. Confirm that the installed manifest is located at
   `/config/custom_components/buspro/manifest.json`.
4. Restart Home Assistant.
5. In **Settings > Devices & services**, add **HDL Buspro** and enter the local
   IP address and UDP port of the gateway.

The config entry starts the shared gateway connection. Entities are currently
declared with YAML platform configuration. For example:

```yaml
light:
  - platform: buspro
    devices:
      "1.89.1":
        name: Living Room Light
      "1.89.2":
        name: Front Door Light
        dimmable: false

switch:
  - platform: buspro
    devices:
      "1.90.1":
        name: Hall Relay

sensor:
  - platform: buspro
    devices:
      - address: "1.74"
        name: Living Room Temperature
        type: temperature
        device: dlp
        unit_of_measurement: "°C"
        device_class: temperature

binary_sensor:
  - platform: buspro
    devices:
      - address: "1.75"
        name: Hall Motion
        type: motion
        device: pir
        device_class: motion
```

As an alternative to the UI config entry, the gateway can be configured in
YAML. Do not configure a second, different gateway through the UI at the same
time.

```yaml
buspro:
  host: !secret buspro_gateway_host
  port: 6000
```

Buspro device addresses use the protocol's `subnet.device` or
`subnet.device.channel` form; they are not IP addresses.

## Services

The integration registers these Home Assistant services:

- `buspro.activate_scene`
- `buspro.send_message`
- `buspro.set_universal_switch`
- `buspro.set_panel_ac`

`buspro.send_message` is a low-level service that can transmit supported raw
Buspro commands. Prefer entity controls or the narrower services whenever
possible.

## Security and privacy

HDL Buspro UDP traffic is local and is not authenticated or encrypted by this
integration. Keep the gateway and Home Assistant on a trusted or isolated LAN,
and do not expose the Buspro UDP port to the internet. Anyone able to inject
valid Buspro traffic on that network may be able to observe or control devices.

Only trusted Home Assistant users and automations should be allowed to call the
Buspro services, especially `buspro.send_message`.

The integration creates bounded diagnostic files in the Home Assistant
configuration directory:

- `/config/buspro_sensor_capture.jsonl`
- `/config/buspro_floor_heating_capture.jsonl`

They omit raw Buspro addresses, but may contain entity names, sensor values,
timestamps, and protocol payloads. Treat them as private runtime data. Do not
commit, publish, or attach them to issues without reviewing and redacting them.
Debug logs may contain similar operational details.

Keep gateway addresses and all other site-specific configuration outside this
repository. Use Home Assistant secrets where appropriate.

## Development

After changing the integration:

1. Run the automated tests.
2. Validate the Home Assistant configuration.
3. Deploy only the integration files.
4. Restart Home Assistant so Python changes are loaded.
5. Check the Home Assistant logs and exercise the changed behavior.

Keep entity unique IDs stable so existing device and entity registry entries
remain valid.

## License and attribution

This fork is based on the original
[Home Assistant Buspro integration by eyesoft](https://github.com/eyesoft/home_assistant_buspro),
which is published under the MIT License. Preserve the original copyright and
license notice when redistributing this work. The full notice is included in
[`LICENSE`](LICENSE).
