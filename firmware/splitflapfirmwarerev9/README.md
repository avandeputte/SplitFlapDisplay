# Split-Flap Display Firmware

Firmware for a multi-module split-flap display where each character cell is an independent ATtiny1616 microcontroller connected to a shared RS-485 bus. A Raspberry Pi (or any serial host) sends commands over the bus to drive individual cells or the whole display at once.

---

## Table of Contents

- [Hardware Overview](#hardware-overview)
- [Repository Contents](#repository-contents)
- [Wiring](#wiring)
- [Flashing the Firmware](#flashing-the-firmware)
- [How It Works](#how-it-works)
  - [Module Identity and Provisioning](#module-identity-and-provisioning)
  - [Boot Sequence](#boot-sequence)
  - [EEPROM Layout](#eeprom-layout)
- [RS-485 Message Protocol](#rs-485-message-protocol)
  - [Message Format](#message-format)
  - [Addressing](#addressing)
- [Command Reference](#command-reference)
  - [Display Commands](#display-commands)
  - [Calibration Commands](#calibration-commands)
  - [Configuration Commands](#configuration-commands)
  - [Provisioning Commands](#provisioning-commands)
  - [De-provisioning Command](#de-provisioning-command)
- [Flap Character Map](#flap-character-map)
- [Provisioning Script (`provision.py`)](#provisioning-script-provisionpy)
  - [Requirements](#requirements)
  - [Running the Script](#running-the-script)
  - [Provisioning a New Module](#provisioning-a-new-module)
  - [De-provisioning Modules](#de-provisioning-modules)
- [Calibration Workflow](#calibration-workflow)
- [Firmware Change Log](#firmware-change-log)

---

## Hardware Overview

Each module in the display consists of:

| Component | Part |
|---|---|
| Microcontroller | ATtiny1616 |
| Stepper motor | 28BYJ-48 (or equivalent 4-wire unipolar) |
| Motor driver | ULN2003 driver board |
| Position sensor | Hall effect sensor (active LOW) |
| Bus transceiver | RS-485 half-duplex module (e.g. MAX485) |

Multiple modules are daisy-chained on a single RS-485 pair. A single Raspberry Pi with a USB-to-RS-485 adapter controls the entire display.

---

## Repository Contents

```
splitflapfirmwarev9.ino   — ATtiny1616 firmware (Arduino sketch)
provision.py              — Raspberry Pi provisioning and management tool
README.md                 — This file
```

---

## Wiring

### ATtiny1616 Pin Assignments

| Pin | Function |
|---|---|
| 1 (PA4) | RS-485 RX (SoftwareSerial) |
| 2 (PA5) | RS-485 DE — Driver Enable (HIGH = transmit) |
| 3 (PA6) | RS-485 TX (SoftwareSerial) |
| 4 (PB3) | Hall effect sensor (active LOW, internal pull-up enabled) |
| 6 (PC0) | Stepper IN4 |
| 7 (PC1) | Stepper IN3 |
| 8 (PC2) | Stepper IN2 |
| 9 (PC3) | Stepper IN1 |

> **Note:** The DE pin of the RS-485 transceiver must be driven HIGH before transmitting and LOW to return to receive mode. The firmware manages this automatically.

### RS-485 Bus

- Connect all modules' A/B lines in parallel along the bus.
- Terminate the far end of the bus with a 120 Ω resistor across A and B.
- Connect the Raspberry Pi's USB-to-RS-485 adapter to the same A/B lines.

---

## Flashing the Firmware

The firmware is a standard Arduino sketch targeting the ATtiny1616 via the [megaTinyCore](https://github.com/SpenceKonde/megaTinyCore) board package.

1. Install **megaTinyCore** in the Arduino IDE via Boards Manager.
2. Select **ATtiny1616** as the target board.
3. Set the programmer to **jtag2updi** (or whichever UPDI programmer you are using).
4. Open `splitflapfirmwarev9.ino` and click **Upload**.

Every module runs the same firmware binary. IDs are assigned at runtime via the provisioning tool — there is nothing to change before flashing.

---

## How It Works

### Module Identity and Provisioning

Each ATtiny1616 contains a factory-programmed 10-byte unique serial number in its signature row (SIGROW, bytes `0x1103–0x110C`). The firmware reads this at boot and formats it as a 20-character uppercase hex string (e.g. `A3F24C0018E7D29B3F01`).

On first boot, or after a de-provisioning reset, a module has no assigned bus ID (`moduleId = 255`). In this state it:

1. Does **not** respond to numeric ID commands.
2. Broadcasts its serial number on the RS-485 bus every **10 seconds** so the provisioning tool can discover it.

Once a numeric bus ID (0–254) is assigned via the provisioning tool, the module stores it in EEPROM and begins responding to that ID on all future commands. It stops advertising.

### Boot Sequence

```
Power on
  │
  ├─ Read ATtiny serial number from SIGROW
  ├─ Load config from EEPROM (or write defaults on first boot)
  │
  ├─ Staggered startup delay
  │     Provisioned   → delay = moduleId × 150 ms
  │     Unprovisioned → delay = serialNumber[last byte] × 40 ms
  │     (prevents all motors from surging current simultaneously)
  │
  └─ Home or restore position
        autoHome = 1 → spin to Hall sensor, advance to flap 0
        autoHome = 0 → restore last saved step position from EEPROM
```

### EEPROM Layout

| Address | Size | Contents |
|---|---|---|
| 0x00 | 1 byte | Init magic (`0x5E`) — detects valid EEPROM data |
| 0x01 | 2 bytes | Home offset — steps from Hall trigger to flap 0 |
| 0x03 | 2 bytes | Total steps per revolution |
| 0x05 | 1 byte | Module bus ID (255 = unprovisioned) |
| 0x06 | 1 byte | Auto-home flag (1 = home on boot, 0 = restore) |
| 0x07 | 2 bytes | Saved step position (used when auto-home is off) |
| 0x09 | 1 byte | Saved flap index (used when auto-home is off) |
| 0x0C | 128 bytes | Fine-calibrated step positions for all 64 flaps (2 bytes each, `0xFFFF` = uncalibrated) |

> Changing the init magic value in the firmware source forces all modules to re-initialise their EEPROM on the next boot, which is useful after a breaking schema change.

---

## RS-485 Message Protocol

### Message Format

```
m  <ID>  <CMD>  [data]  \n
│    │     │      │
│    │     │      └── Command payload (digits, a single char, or hex string)
│    │     └───────── Command letter (see Command Reference below)
│    └─────────────── Module address (numeric ID, *, or X)
└──────────────────── Literal 'm' — start-of-message marker
```

Every message begins with a lowercase `m` and ends with a newline (`\n`). The command parser is a state machine that processes one byte at a time; messages without an explicit terminator are flushed after a 50 ms idle timeout.

### Addressing

| ID field | Meaning |
|---|---|
| `1` – `254` | Address a single provisioned module by its numeric ID |
| `*` or `**` | Broadcast — all provisioned modules respond |
| `X` | Provisioning address — all modules respond (provisioned or not); used for serial-number-targeted commands |

---

## Command Reference

In all examples, `<ID>` is the numeric module ID (e.g. `38`), `*` broadcasts to all provisioned modules, and `X` addresses all modules regardless of provisioning state.

### Display Commands

#### `-` — Show character

Move the reel to display a specific character from the flap set.

```
m<ID>-<char>\n
```

| Part | Description |
|---|---|
| `<char>` | Exactly one character from the [flap character map](#flap-character-map) |

**Examples:**
```
m38-B\n       → module 38 shows 'B'
m5-7\n        → module 5 shows the character '7' (not index 7)
m*- \n        → all modules show blank (space = flap 0)
```

---

#### `+` — Show flap by index

Move the reel to a specific flap by its zero-based numeric index (0–63).

```
m<ID>+<index>\n
```

| Part | Description |
|---|---|
| `<index>` | Integer 0–63 matching a position in the flap character map |

**Examples:**
```
m38+0\n       → module 38 shows flap 0 (blank)
m38+7\n       → module 38 shows flap 7 ('F')
m38+12\n      → module 38 shows flap 12 ('K')
```

---

### Calibration Commands

#### `h` — Home

Spin the reel until the Hall sensor fires, then advance the calibrated offset to land on flap 0 (blank). Resets the internal step counter to zero.

```
m<ID>h\n
m*h\n         (broadcast)
```

---

#### `c` — Calibrate revolution

Measures the exact number of half-steps in one full revolution by timing two passes of the Hall sensor. Saves the result to EEPROM and reports it over RS-485:

```
m<ID>:<measuredSteps>\n
```

Run this once during physical installation, or after swapping a reel. The module automatically re-homes after calibrating.

```
m38c\n
```

---

#### `o` — Set home offset

Sets the number of half-steps to travel past the Hall sensor trigger before the reel reaches flap 0. Persisted to EEPROM immediately.

```
m<ID>o<steps>\n
```

**Example:**
```
m38o2832\n    → set home offset to 2832 steps
```

---

#### `s` — Nudge

Advance the reel by `N` additional half-steps from its current position and add that distance to the stored home offset. Use this for fine-tuning the home position during physical alignment without running a full recalibration.

```
m<ID>s<steps>\n
```

**Example:**
```
m38s16\n      → nudge 16 steps forward and update home offset
```

---

#### `t` — Set total steps

Override the total half-steps per revolution. Normally set automatically by the `c` (calibrate) command, but can be set manually. Persisted to EEPROM.

```
m<ID>t<steps>\n
```

**Example:**
```
m38t4096\n
```

---

#### `g` — Go to raw step position

Move the reel to an absolute step position (0 to `totalStepsPerRev − 1`). Sets the current flap index to `-2` (unknown/manual). Useful for diagnostics and manual calibration.

```
m<ID>g<stepPosition>\n
```

**Example:**
```
m38g512\n
```

---

#### `w` — Write calibrated flap position

Store a fine-calibrated step position for a specific flap index into the EEPROM map. When this entry is present, `moveToIndex` uses it instead of the evenly-spaced estimate.

```
m<ID>w<index>:<stepPosition>\n
```

**Example:**
```
m38w7:342\n   → record that flap 7 is at step 342
```

---

#### `e` — Erase position map

Clears all 64 fine-calibrated flap positions from EEPROM, resetting them to `0xFFFF` (uncalibrated). The module falls back to evenly-spaced step estimates until re-calibrated.

```
m<ID>e\n
m*e\n         (broadcast)
```

---

### Configuration Commands

#### `i` — Set module ID

Assign a new numeric bus ID and persist it to EEPROM. Takes effect immediately; the module begins responding to the new ID for all subsequent commands.

```
m<ID>i<newId>\n
```

**Example:**
```
m38i42\n      → reassign module 38 to ID 42
```

> For first-time ID assignment on an unprovisioned module, use the `I` provisioning command instead (see below).

---

#### `a` — Set auto-home mode

Control whether the module runs `homeModule()` on every boot.

```
m<ID>a1\n     → home on boot (default)
m<ID>a0\n     → restore last saved position on boot (no motor movement)
```

When auto-home is disabled, the current step position and flap index are saved to EEPROM whenever the reel moves, allowing the module to resume without homing after a power cycle.

---

#### `d` — Dump EEPROM

Returns the module's full configuration over RS-485 in one line:

```
m<ID>d:<homeOffset>:<totalSteps>:<idx>=<pos>,<idx>=<pos>,...\n
```

Only calibrated flap positions (those not equal to `0xFFFF`) are included in the map section.

**Example response:**
```
m38d:2832:4096:0=0,7=342,12=683\n
```

---

### Provisioning Commands

These commands use the `X` address field and are processed by **every module on the bus** regardless of provisioning state. Each module performs its own serial-number check internally.

---

#### `mXH` — Home by serial number

Spin a specific module (identified by serial number) to its home position. Use this to physically identify an unprovisioned module before assigning it an ID.

```
mXH<serialNumber>\n
```

| Part | Description |
|---|---|
| `<serialNumber>` | 20-character uppercase hex string from the ATtiny SIGROW |

**Example:**
```
mXHA3F24C0018E7D29B3F01\n
```

Only the module whose serial number matches will move. All other modules silently discard the command.

---

#### `mXI` — Assign ID by serial number

Assign a permanent bus ID to a specific module identified by its serial number. The module writes the new ID to EEPROM, stops advertising, and sends an acknowledgement:

```
mXack:<serialNumber>:<assignedId>\n
```

**Command format:**
```
mXI<serialNumber>:<newId>\n
```

**Example:**
```
mXIA3F24C0018E7D29B3F01:38\n
```

**Acknowledgement:**
```
mXackA3F24C0018E7D29B3F01:38\n
```

---

### De-provisioning Command

#### `R` — Reset provisioning

Erase the stored bus ID from EEPROM and return the module to the unprovisioned state. All calibration data (home offset, total steps, flap map, auto-home flag) is preserved. The module immediately begins advertising its serial number again.

```
m<ID>R\n      → de-provision one module by its current ID
m*R\n         → broadcast: de-provision all modules on the bus
```

**Examples:**
```
m38R\n        → de-provision module 38
m*R\n         → de-provision every module simultaneously
```

> After a broadcast reset, each module will re-advertise within 10 seconds. The provisioning tool automatically re-opens the provisioning dialog for each one as it appears.

---

## Flap Character Map

The reel holds 64 physical flaps. Position 0 is always blank (the home position). The string below defines the character at each index — the index is the position in the string, zero-based.

```
Index:  0123456789...
Chars: " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;q:%'.,/?*roygbpw"
```

| Index range | Characters |
|---|---|
| 0 | ` ` (blank / home) |
| 1–26 | `A` – `Z` |
| 27–36 | `0` – `9` |
| 37–50 | `! @ # $ & ( ) - + = ; q : %` |
| 51–57 | `' . , / ? * ` |
| 58–63 | `r o y g b p w` (colour flaps) |

The lowercase letters `q`, `r`, `o`, `y`, `g`, `b`, `p`, `w` at the end of the string represent physical colour flaps. They are addressed by character using the `-` command (e.g. `m38-r\n` shows the red flap).

---

## Provisioning Script (`provision.py`)

`provision.py` is a Raspberry Pi terminal tool that automates the discovery and management of modules on the RS-485 bus.

### Requirements

- Python 3.9 or later
- [pyserial](https://pypi.org/project/pyserial/)

```bash
pip install pyserial
```

### Running the Script

```bash
python3 provision.py [--port PORT] [--baud BAUD]
```

| Argument | Default | Description |
|---|---|---|
| `--port` | `/dev/ttyUSB0` | Serial port for the USB-to-RS-485 adapter |
| `--baud` | `9600` | Baud rate — must match the firmware |

**Example:**
```bash
python3 provision.py --port /dev/ttyACM0
```

Once running, the script listens continuously for module advertisements. While idle it accepts two hotkeys:

| Key | Action |
|---|---|
| `d` | Open the de-provision menu |
| `q` | Quit |

No Enter key is required for hotkeys — the terminal is in raw mode while idle.

---

### Provisioning a New Module

When a module advertises for the first time, the script opens an interactive dialog:

```
────────────────────────────────────────────────────────────
  New module detected
  Serial : A3F24C0018E7D29B3F01
────────────────────────────────────────────────────────────
  Home this module now to identify it? [y/N]
```

**Step 1 — Identify (optional)**

Entering `y` sends `mXH<serialNumber>` to the bus. That module's reel will spin to the blank/home position so you can physically see which tile it is. All other modules ignore the command.

**Step 2 — Assign an ID**

```
  Enter bus ID to assign (0–254), or 's' to skip:
```

- Enter a number between 0 and 254 to assign that ID.
- Enter `s` to skip — the module will continue advertising every 10 seconds and the dialog will re-open next time.
- The script warns if you enter an ID already used in the current session.

**Step 3 — Confirm and acknowledge**

After confirmation the script sends `mXI<serialNumber>:<id>` and waits up to 2 seconds for the module's `mXack` response. A success or warning message is displayed, then the script returns to listening mode.

---

### De-provisioning Modules

Press `d` at any time while the script is running to open the de-provision menu:

```
────────────────────────────────────────────────────────────
  De-provision modules
────────────────────────────────────────────────────────────
  IDs provisioned this session:
     38  →  A3F24C0018E7D29B3F01
     42  →  B10055FFA3C2918D7E44

  Options:
    Enter a module ID (0–254) to de-provision that module
    Enter 'all' to de-provision every module on the bus
    Enter 's' to go back
```

**De-provision one module**

Enter a numeric ID. If that ID was provisioned in the current session, its serial number is shown for confirmation. You can also enter IDs from previous sessions that are not in the list — the `R` command will still be sent.

After confirmation the script sends `m<id>R\n`, removes the module from the session registry, and clears its serial from the seen list so its provisioning dialog will reopen when it starts advertising again.

**De-provision all modules**

Enter `all`. A warning prompt confirms before the script sends `m*R\n`. The entire session registry is cleared. Every module will erase its ID, begin advertising, and get a fresh provisioning dialog as each advertisement arrives.

---

## Calibration Workflow

Follow these steps when setting up a module for the first time or after a mechanical change:

1. **Flash** the firmware and power up the module.

2. **Provision** the module using `provision.py` to assign it a bus ID (e.g. `38`).

3. **Calibrate the revolution** — measures the exact steps per revolution and saves it to EEPROM:
   ```
   m38c\n
   ```
   The module reports the measured step count and then homes automatically.

4. **Check the home position** — if flap 0 (blank) is not perfectly aligned after homing, nudge it into place:
   ```
   m38s8\n     → advance 8 more steps and update the home offset
   ```
   Repeat with small values until the blank flap is centred in the window.

5. **Test character display:**
   ```
   m38-A\n
   m38-0\n
   m38- \n     → back to blank
   ```

6. **(Optional) Fine-calibrate individual flaps** — if specific characters land slightly off, use the `g` command to find the exact step position and then write it to the EEPROM map:
   ```
   m38g340\n         → manually drive to step 340
   m38w7:340\n       → save step 340 as the calibrated position for flap index 7
   ```

7. **Verify the full map:**
   ```
   m38d\n            → dump EEPROM including all saved flap positions
   ```

---

## Firmware Change Log

### v9
- New `R` command: reset provisioning. Erases the stored module ID (sets it back to 255) without touching calibration data, then immediately resumes advertising. Works with a numeric ID (`m38R\n`) or a broadcast (`m*R\n`).

### v8
- Removed `HARDCODED_ID` constant. First boot no longer writes a fixed ID — modules start unprovisioned.
- Unprovisioned modules broadcast their ATtiny1616 serial number every 10 seconds (`mXadv:<sn>\n`).
- New `H` provisioning command: home a module by serial number (`mXH<sn>\n`).
- New `I` provisioning command: assign an ID by serial number (`mXI<sn>:<id>\n`). Module sends `mXack` on success.
- Both provisioning commands use the `X` address field so they work regardless of whether a numeric ID has been assigned.
- Staggered startup delay uses the low byte of the serial number when unprovisioned, so multiple fresh modules spread their motor starts across up to ~10 seconds.

### v7
- Module IDs are now 1–3 digits; ID parsing uses digit accumulation so `m5`, `m38`, and `m138` all work correctly.
- New `+` command: move to a flap by numeric index (0–63). The existing `-` command continues to accept a single character byte.
- `moveToChar()` refactored to call the new `moveToIndex()` core function.
