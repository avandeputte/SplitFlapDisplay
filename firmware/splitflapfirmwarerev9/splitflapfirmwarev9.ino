/ ==============================================================================
// Split-Flap Display Module Firmware — v9
// ==============================================================================
// Each Arduino runs this firmware to control one character cell of a split-flap
// display. The cell is driven by a 28BYJ-48 stepper motor (or similar) which
// rotates a reel of physical flaps past a viewing window. A Hall effect sensor
// detects a magnet on the reel to find the "home" (zero) position.
//
// Multiple modules are wired together on an RS-485 serial bus and addressed by
// a unique numeric ID. A Raspberry Pi (or other controller) sends commands over
// the bus; each module listens for its own ID and responds accordingly.
//
// Message format:  m<ID><CMD>[data]\n
//   <ID> is 1–3 digits (e.g. 5, 38, 138) or one or more '*' for broadcast.
//   Examples:
//     "m38-B"    →  module /38: show character 'B'     ('-' always takes one char)
//     "m38+7"    →  module 38: show flap at index 7   ('+' takes a numeric index)
//     "m38+12"   →  module 38: show flap at index 12
//     "m138-A"   →  module 138: show character 'A'    (3-digit ID)
//     "m*h"      →  broadcast: home all modules
//     "m**h"     →  broadcast: home all modules (legacy two-star format still works)
//
// Serial-number provisioning commands (for unprovisioned modules):
//   "mXH<serialNumber>\n"  →  home the module whose ATtiny serial number matches
//                              <serialNumber> (20 hex digits).
//                              'X' here is a literal placeholder — this command
//                              uses ID field 'X' and is intercepted before normal
//                              ID matching.  In practice send:  "mXH<sn>\n"
//   "mXI<serialNumber>:<newId>\n"  →  assign <newId> to the module with <serialNumber>.
//
//   NOTE: Both H and I provisioning commands use a dedicated broadcast address
//   "mX" so they do not collide with normal numeric IDs or '*' broadcasts.
//   Every module (provisioned or not) listens for the 'X' address.
//
// Reset command (provisioned modules):
//   "m<ID>R\n"  →  erase the module ID from EEPROM, set moduleId back to 255,
//                  and immediately begin advertising again.  All other EEPROM
//                  data (calibration, flap map, auto-home flag) is preserved.
//   "m*R\n"     →  broadcast reset — unprovisions every module on the bus.
//
// Advertisement (unprovisioned modules only):
//   When moduleId == 255 the module transmits every ADVERTISE_INTERVAL_MS:
//     "mXadv:<serialNumber>\n"
//   The Pi uses these advertisements to discover and then provision new modules.
//
// Change log v7 → v8:
//   - Removed HARDCODED_ID constant.  First-boot no longer writes a fixed ID.
//   - Unprovisioned modules (ID == 255) advertise their ATtiny serial number on
//     the RS-485 bus every 10 seconds.
//   - New 'H' provisioning command: home by serial number.
//   - New 'I' provisioning command: assign ID by serial number.
//   - Both provisioning commands use 'X' as the message address field so they
//     work regardless of whether the module has a numeric ID yet.
//   - Staggered startup delay now uses the low byte of the serial number instead
//     of the module ID when ID is unset, so multiple unprovisioned modules still
//     spread their motor starts.
//
// Change log v8 → v9:
//   - New 'R' command: reset provisioning.  Erases the stored module ID (sets it
//     back to 255) without touching any calibration data, then immediately resumes
//     advertising so the Pi can re-provision the module.  Works with a numeric ID
//     ("m38R\n") or a broadcast ("m*R\n").
// ==============================================================================

#include <SoftwareSerial.h>   // Software UART for RS-485 (frees hardware serial)
#include <EEPROM.h>           // Non-volatile storage for calibration & config

// ==========================================
//               CONFIGURATION
// ==========================================
// The ordered set of 64 characters this reel can display.
// Position 0 = blank space (the "home" flap).
// The index in this string corresponds to a physical flap on the reel.
// Lowercase letters at the end (q, r, o, y, g, b, p, w) represent color flaps.
const String FLAP_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;q:%'.,/?*roygbpw";

// How often an unprovisioned module advertises its serial number (milliseconds).
const unsigned long ADVERTISE_INTERVAL_MS = 10000UL;

// ---- EEPROM Address Map ----
// Each address stores a specific configuration value persistently.
const int ADDR_INIT        = 0;   // 1 byte  — Magic number to detect valid EEPROM data
const int ADDR_HOME_OFFSET = 1;   // 2 bytes — Steps past magnet trigger to reach flap 0
const int ADDR_TOTAL_STEPS = 3;   // 2 bytes — Total steps for one full reel revolution
const int ADDR_MODULE_ID   = 5;   // 1 byte  — This module's bus ID (0–254; 255 = unset)
const int ADDR_AUTO_HOME   = 6;   // 1 byte  — 1 = home on boot, 0 = restore saved position
const int ADDR_SAVED_POS   = 7;   // 2 bytes — Last known step position (for no-home mode)
const int ADDR_SAVED_INDEX = 9;   // 1 byte  — Last known flap index  (for no-home mode)
const int ADDR_MAP_START   = 12;  // 128 bytes — Fine-calibrated step positions for all 64 flaps
                                  //             Each entry is 2 bytes (uint16_t), 0xFFFF = uncalibrated

// Magic value written to ADDR_INIT to indicate EEPROM has been initialized.
// Changing this value forces all modules to reset to defaults on next boot.
const uint8_t INIT_VALUE = 0x5E;  // Bumped from 0x5D to force re-init on upgrade

// ==========================================
//           ATtiny1616 SERIAL NUMBER
// ==========================================
// The ATtiny1616 has a 10-byte unique serial number in the signature row.
// Addresses 0x1103–0x110C in the SIGROW address space.
// We read it at startup and format it as a 20-hex-digit string.
// (The AVR-LibC <avr/signature.h> gives SIGROW address; on ATtiny1616 the
//  device serial lives at SIGROW + 3, 10 bytes.)
//
// ATtiny1616 memory map (from datasheet §8):
//   SIGROW starts at 0x1100
//   Bytes 0x03–0x0C (offsets 3–12) = 10-byte unique serial number
//
// We read via the SIGROW peripheral registers using direct memory reads.

#define SIGROW_BASE   0x1100
#define SERIAL_OFFSET 3
#define SERIAL_LEN    10

// Holds the module's serial number as a null-terminated hex string (20 chars + NUL).
char serialStr[SERIAL_LEN * 2 + 1];

// Populate serialStr by reading the ATtiny1616 SIGROW serial bytes.
void readSerialNumber() {
  for (uint8_t i = 0; i < SERIAL_LEN; i++) {
    // The SIGROW registers are in the I/O space — use pgm_read_byte with the
    // absolute address.  On AVR-DA/DD/tiny the SIGROW is memory-mapped read-only.
    uint8_t b = _MMIO_BYTE(SIGROW_BASE + SERIAL_OFFSET + i);
    uint8_t hi = (b >> 4) & 0x0F;
    uint8_t lo =  b       & 0x0F;
    serialStr[i * 2]     = hi < 10 ? ('0' + hi) : ('A' + hi - 10);
    serialStr[i * 2 + 1] = lo < 10 ? ('0' + lo) : ('A' + lo - 10);
  }
  serialStr[SERIAL_LEN * 2] = '\0';
}

// Returns true if the null-terminated string `candidate` matches our serialStr.
// Comparison is case-insensitive so the Pi can send lowercase or uppercase.
bool serialMatches(const String &candidate) {
  if (candidate.length() != SERIAL_LEN * 2) return false;
  for (uint8_t i = 0; i < SERIAL_LEN * 2; i++) {
    char a = serialStr[i];
    char b = candidate[i];
    // Fold to uppercase for comparison
    if (a >= 'a') a -= 32;
    if (b >= 'a') b -= 32;
    if (a != b) return false;
  }
  return true;
}

// ---- Operating State ----
uint8_t moduleId        = 255;  // 255 = unassigned; loaded from EEPROM on boot
bool    autoHomeEnabled = true; // Whether to run homeModule() at startup

// Calibration values (loaded from EEPROM; can be updated via serial commands)
int stepsFromHallToZero = 2832;  // Steps from Hall sensor trigger edge → flap 0 position
int totalStepsPerRev    = 4096;  // Total half-steps in one full reel revolution

// ==========================================
//              PIN DEFINITIONS
// ==========================================
// RS-485 transceiver pins (half-duplex; DE pin selects TX vs RX mode)
const int RS485_RX = 3;   // Receive data from bus (SoftwareSerial RX)
const int RS485_TX = 1;   // Transmit data to bus  (SoftwareSerial TX)
const int RS485_DE = 2;   // Driver Enable: HIGH = transmit, LOW = receive

// Stepper motor coil drive pins (connected to ULN2003 or similar driver board)
#define IN1 9
#define IN2 8
#define IN3 7
#define IN4 6

// Hall effect sensor (active LOW — pulls LOW when magnet is present)
#define HALL_PIN 4

// ==========================================
//               GLOBALS
// ==========================================
// SoftwareSerial lets us use arbitrary pins for RS-485, keeping hardware serial
// free for debugging if needed.
SoftwareSerial rs485(RS485_RX, RS485_TX);

long currentStepPos   = 0;   // Current motor position in half-steps (0 = flap 0)
int  currentPhase     = 0;   // Current index into halfStepSequence[8]
int  parseState       = 0;   // State machine position for incoming serial parsing
int  currentFlapIndex = -1;  // Which flap is currently showing (-1 = unknown)
int  tempIndex        = -1;  // Scratch variable used while parsing 'w' (write map) command

const int stepDelay = 1;     // Milliseconds between each half-step pulse (controls speed)

String buffer     = "";      // Accumulates digit characters during command parsing
String idBuffer   = "";      // Accumulates digit characters of the incoming module ID
bool   idWildcard = false;   // True when '*' was seen in the ID field (broadcast match)
bool   idProvision = false;  // True when 'X' was seen in the ID field (provisioning command)

unsigned long lastSerialTime   = 0;   // Timestamp of last received byte (for timeout detection)
unsigned long lastAdvertiseTime = 0;  // Timestamp of last advertisement transmission

// Provisioning parse sub-states (used when idProvision == true and parseState == 1→ dispatched)
// State 13: 'H' command — accumulating serial number digits for home-by-SN
// State 14: 'I' command — accumulating serial number, then ':', then new ID digits
String snBuffer  = "";    // Accumulates the serial number for provisioning commands
bool   snColonSeen = false; // In state 14, true once we've passed the ':' separator

// Half-step sequence for 4-wire stepper motor.
// Each row is one phase: {IN1, IN2, IN3, IN4} — energize the coils in this order
// to step forward. Reversing the traversal order steps backward.
// Half-stepping (8 phases vs 4) gives smoother motion and finer resolution.
const uint8_t halfStepSequence[8][4] = {
  {1, 0, 0, 0},   // Phase 0: coil A only
  {1, 1, 0, 0},   // Phase 1: coils A+B
  {0, 1, 0, 0},   // Phase 2: coil B only
  {0, 1, 1, 0},   // Phase 3: coils B+C
  {0, 0, 1, 0},   // Phase 4: coil C only
  {0, 0, 1, 1},   // Phase 5: coils C+D
  {0, 0, 0, 1},   // Phase 6: coil D only
  {1, 0, 0, 1}    // Phase 7: coils D+A
};

// ==========================================
//             EEPROM FUNCTIONS
// ==========================================

// Persist the home offset (steps from Hall trigger to flap 0) to EEPROM.
void saveHomeOffset() { EEPROM.put(ADDR_HOME_OFFSET, stepsFromHallToZero); }

// Persist the total steps per revolution to EEPROM.
void saveTotalSteps()  { EEPROM.put(ADDR_TOTAL_STEPS, totalStepsPerRev); }

// Save current position and flap index so the module can resume without homing.
// Only saves if autoHome is disabled — if we're going to home on boot anyway,
// there's no point wasting EEPROM write cycles.
void saveState() {
  if (!autoHomeEnabled) {
    EEPROM.put(ADDR_SAVED_POS,   currentStepPos);
    EEPROM.put(ADDR_SAVED_INDEX, currentFlapIndex);
  }
}

// Print the module ID onto the RS-485 bus, zero-padded to at least 2 digits.
// Examples: 5 → "05",  38 → "38",  138 → "138"
void printModuleId() {
  if (moduleId < 10) rs485.print("0");
  rs485.print(moduleId);
}

// Send a full EEPROM config dump back to the Pi over RS-485.
// Output format:  m<ID>d:<homeOffset>:<totalSteps>:<idx>=<pos>,<idx>=<pos>,...\n
// Only non-empty map entries (pos != 0xFFFF) are included.
void dumpEeprom() {
  delay(50);
  digitalWrite(RS485_DE, HIGH);
  delay(10);

  rs485.print("m");
  printModuleId();
  rs485.print("d:");
  rs485.print(stepsFromHallToZero);
  rs485.print(":");
  rs485.print(totalStepsPerRev);
  rs485.print(":");

  bool first = true;
  for (int i = 0; i < 64; i++) {
    uint16_t pos = 0xFFFF;
    EEPROM.get(ADDR_MAP_START + (i * 2), pos);
    if (pos != 0xFFFF) {
      if (!first) rs485.print(",");
      rs485.print(i);
      rs485.print("=");
      rs485.print(pos);
      first = false;
    }
  }

  rs485.print("\n");
  delay(100);
  digitalWrite(RS485_DE, LOW);
}

// ==========================================
//        ADVERTISEMENT (unprovisioned)
// ==========================================
// Transmit a discovery beacon so the Pi can find unprovisioned modules.
// Format:  "mXadv:<serialNumber>\n"
// The Pi listens for "mXadv:" prefixed lines and records new serial numbers.
void sendAdvertisement() {
  delay(10);
  digitalWrite(RS485_DE, HIGH);
  delay(5);

  rs485.print("mXadv:");
  rs485.print(serialStr);
  rs485.print("\n");

  delay(50);
  digitalWrite(RS485_DE, LOW);
}

// ==========================================
//         PROVISIONING RESET
// ==========================================
// Erase the stored module ID and return to the unprovisioned/advertising state.
// All calibration data (home offset, total steps, flap map, auto-home flag) is
// intentionally preserved so re-provisioning does not require re-calibration.
void resetProvisioning() {
  moduleId = 255;
  EEPROM.write(ADDR_MODULE_ID, 255);

  // Reset the advertisement timer so the first beacon fires on the next loop
  // tick rather than waiting a full 10 seconds.
  lastAdvertiseTime = millis() - ADVERTISE_INTERVAL_MS;
}

// ==========================================
//             MOTOR FUNCTIONS
// ==========================================

// Drive the four stepper coil pins to match the given phase pattern.
void applyStep(const uint8_t *step) {
  digitalWrite(IN1, step[0]);
  digitalWrite(IN2, step[1]);
  digitalWrite(IN3, step[2]);
  digitalWrite(IN4, step[3]);
}

// Advance the reel by `steps` half-steps in the "backward" phase direction.
// Also tracks the Hall sensor edge for absolute position correction.
void stepBackward(int steps) {
  static bool lastHallState = false;

  for (int k = 0; k < steps; k++) {
    bool hallNow = hallActive();

    if (hallNow && !lastHallState) {
      currentStepPos = totalStepsPerRev - stepsFromHallToZero;
    }
    lastHallState = hallNow;

    currentPhase--;
    if (currentPhase < 0) currentPhase = 7;

    applyStep(halfStepSequence[currentPhase]);
    delay(stepDelay);

    currentStepPos++;
    if (currentStepPos >= totalStepsPerRev) currentStepPos = 0;
  }
}

// De-energize all stepper coils to prevent heat buildup when idle.
void releaseMotor() {
  digitalWrite(IN1, 0);
  digitalWrite(IN2, 0);
  digitalWrite(IN3, 0);
  digitalWrite(IN4, 0);
}

// Returns true if the Hall effect sensor detects the magnet (active LOW).
bool hallActive() {
  return (digitalRead(HALL_PIN) == LOW);
}

// ==========================================
//             LOGIC FUNCTIONS
// ==========================================

// Drive the reel to the physical zero position (flap 0 = blank space).
void homeModule() {
  long safety = 0;

  while (!hallActive() && safety < (totalStepsPerRev + 500)) {
    stepBackward(1);
    safety++;
  }

  stepBackward(stepsFromHallToZero);

  currentStepPos   = 0;
  currentFlapIndex = 0;
  releaseMotor();
}

// Measure the actual number of steps in one full revolution.
void calibrateModule() {
  long safety = 0;

  while (hallActive() && safety < 4000) {
    stepBackward(1);
    safety++;
    delay(5);
  }

  safety = 0;
  while (!hallActive() && safety < 5000) {
    stepBackward(1);
    safety++;
  }

  while (hallActive()) {
    stepBackward(1);
  }

  int measuredSteps = 0;
  while (!hallActive() && measuredSteps < 5000) {
    stepBackward(1);
    measuredSteps++;
  }
  while (hallActive()) {
    stepBackward(1);
    measuredSteps++;
  }

  delay(50);
  digitalWrite(RS485_DE, HIGH);
  delay(10);

  rs485.print("m");
  printModuleId();
  rs485.print(":");
  rs485.print(measuredSteps);
  rs485.print("\n");

  delay(100);
  digitalWrite(RS485_DE, LOW);

  totalStepsPerRev = measuredSteps;
  saveTotalSteps();
  homeModule();
  saveState();
}

// Move the reel to display the flap at the given index (0–63).
void moveToIndex(int targetIndex) {
  if (targetIndex < 0 || targetIndex >= (int)FLAP_CHARS.length()) return;
  if (currentFlapIndex == targetIndex) return;

  if (currentFlapIndex == -1) {
    homeModule();
  }

  uint16_t mappedPos = 0xFFFF;
  EEPROM.get(ADDR_MAP_START + (targetIndex * 2), mappedPos);

  long targetStepPos;
  if (mappedPos != 0xFFFF) {
    targetStepPos = mappedPos;
  } else {
    targetStepPos = ((long)targetIndex * (long)totalStepsPerRev) / 64;
  }

  long stepsToMove = targetStepPos - currentStepPos;
  if (stepsToMove < 0) {
    stepsToMove += totalStepsPerRev;
  }

  while (stepsToMove > 0) {
    stepBackward(1);
    stepsToMove--;
  }

  releaseMotor();
  currentFlapIndex = targetIndex;
  saveState();
}

// Move the reel to display a specific character.
void moveToChar(char targetChar) {
  int targetIndex = FLAP_CHARS.indexOf(targetChar);
  if (targetIndex == -1) return;
  moveToIndex(targetIndex);
}

// ==========================================
//              SETUP
// ==========================================
void setup() {
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  pinMode(HALL_PIN, INPUT_PULLUP);

  pinMode(RS485_DE, OUTPUT);
  digitalWrite(RS485_DE, LOW);

  // Read the ATtiny1616 unique serial number into serialStr.
  readSerialNumber();

  // ---- Load config from EEPROM ----
  if (EEPROM.read(ADDR_INIT) == INIT_VALUE) {
    EEPROM.get(ADDR_HOME_OFFSET, stepsFromHallToZero);
    EEPROM.get(ADDR_TOTAL_STEPS, totalStepsPerRev);
    moduleId        = EEPROM.read(ADDR_MODULE_ID);
    autoHomeEnabled = (EEPROM.read(ADDR_AUTO_HOME) == 1);
  } else {
    // First boot — write defaults; do NOT assign a module ID (leave as 255).
    EEPROM.write(ADDR_INIT, INIT_VALUE);
    saveHomeOffset();
    saveTotalSteps();

    // moduleId stays 255 (unprovisioned) until the Pi assigns one via 'I' command.
    EEPROM.write(ADDR_MODULE_ID, 255);

    EEPROM.write(ADDR_AUTO_HOME, 1);
    autoHomeEnabled = true;

    for (int i = 0; i < 64; i++) {
      uint16_t empty = 0xFFFF;
      EEPROM.put(ADDR_MAP_START + (i * 2), empty);
    }
  }

  rs485.begin(9600);

  // ---- Staggered startup ----
  // Use module ID for stagger if provisioned, otherwise use the low byte of the
  // serial number so multiple unprovisioned modules still spread their motor starts.
  if (moduleId != 255) {
    delay((unsigned long)moduleId * 150UL);
  } else {
    // Use byte 9 (last byte) of the serial number as a pseudo-random stagger value.
    uint8_t snLow = _MMIO_BYTE(SIGROW_BASE + SERIAL_OFFSET + SERIAL_LEN - 1);
    delay((unsigned long)snLow * 40UL); // 0–255 × 40ms = 0–10.2s spread
  }

  // ---- Home or restore position ----
  if (autoHomeEnabled) {
    homeModule();
    saveState();
  } else {
    EEPROM.get(ADDR_SAVED_POS, currentStepPos);
    if (currentStepPos >= totalStepsPerRev) currentStepPos = 0;
    currentFlapIndex = (int8_t)EEPROM.read(ADDR_SAVED_INDEX);
  }

  // Seed the advertisement timer so we don't blast immediately on power-up.
  lastAdvertiseTime = millis();
}

// ==========================================
//              MAIN LOOP
// ==========================================
// The main loop implements a state machine that parses incoming RS-485 messages
// one character at a time. A valid message has this structure:
//
//   m  <ID digits, '*', or 'X'>  <CMD>  [data]  \n
//   │         │                    │      │
//   │         │                    │      └─ Command-specific payload
//   │         │                    └──────── Command letter
//   │         └─────────────────────────── 1–3 digit numeric ID, '*' broadcast,
//   │                                       or 'X' for provisioning commands
//   └──────────────────────────────────── Literal 'm' — start-of-message marker
//
// parseState values:
//   0       = idle, waiting for 'm'
//   1       = reading ID field (digits, '*', or 'X'), dispatches command on first non-ID char
//   4       = '-' command: reading the single display character byte
//   5       = 'o' command: accumulating home-offset digits
//   6       = 't' command: accumulating total-steps digits
//   7       = 's' command: accumulating nudge-steps digits
//   8       = 'g' command: accumulating raw step-position digits
//   9       = 'w' command: accumulating index then ':' then position digits
//   10      = 'i' command: accumulating new module-ID digits
//   11      = 'a' command: accumulating auto-home toggle digit
//   12      = '+' command: accumulating numeric flap-index digits
//   13      = 'H' provisioning command: accumulating serial-number hex digits
//   14      = 'I' provisioning command: accumulating serial-number, ':', then new ID digits
//
void loop() {
  // ---- Advertisement heartbeat (unprovisioned modules only) ----
  // Send a beacon every ADVERTISE_INTERVAL_MS so the Pi can discover us.
  if (moduleId == 255) {
    unsigned long now = millis();
    if (now - lastAdvertiseTime >= ADVERTISE_INTERVAL_MS) {
      lastAdvertiseTime = now;
      sendAdvertisement();
    }
  }

  // ---- Process incoming serial bytes ----
  while (rs485.available()) {
    char c = rs485.read();
    lastSerialTime = millis();

    switch (parseState) {

      // State 0: Idle — watch for 'm' start-of-message marker.
      case 0:
        if (c == 'm') {
          idBuffer    = "";
          idWildcard  = false;
          idProvision = false;
          parseState  = 1;
        }
        break;

      // State 1: Accumulate the module ID field, then dispatch the command.
      //
      // Accepted ID field characters:
      //   '0'–'9'  — digit of a numeric ID
      //   '*'      — wildcard/broadcast (any number of stars)
      //   'X'      — provisioning address (responds to H and I commands regardless of moduleId)
      //
      // The first character that is not a digit, '*', or 'X' is the command letter.
      case 1:
        if (c == '*') {
          idWildcard = true;
        } else if (c == 'X') {
          idProvision = true;
        } else if (isDigit(c)) {
          if (!idWildcard && !idProvision) idBuffer += c;
        } else {
          // c is the command character — determine if this message is for us.
          bool match = idWildcard ||
                       (idBuffer.length() > 0 && (uint8_t)idBuffer.toInt() == moduleId);

          // Provisioning commands ('H' and 'I') are handled by every module
          // that sees the 'X' address; they perform their own serial-number check
          // internally rather than relying on the ID match above.
          if (idProvision) {
            if (c == 'H') {
              // Home-by-serial-number: accumulate SN in state 13
              snBuffer    = "";
              parseState  = 13;
            } else if (c == 'I') {
              // Assign-ID-by-serial-number: accumulate SN:newID in state 14
              snBuffer    = "";
              snColonSeen = false;
              buffer      = "";
              parseState  = 14;
            } else {
              parseState = 0; // Unknown provisioning command
            }
          } else if (match) {
            // Normal addressed/broadcast command dispatch
            if      (c == '-') { parseState = 4; }
            else if (c == '+') { buffer = "";                parseState = 12; }
            else if (c == 'h') { homeModule(); saveState();  parseState = 0; }
            else if (c == 'c') { calibrateModule();          parseState = 0; }
            else if (c == 'o') { buffer = "";                parseState = 5; }
            else if (c == 't') { buffer = "";                parseState = 6; }
            else if (c == 's') { buffer = "";                parseState = 7; }
            else if (c == 'g') { buffer = "";                parseState = 8; }
            else if (c == 'w') { buffer = ""; tempIndex = -1; parseState = 9; }
            else if (c == 'i') { buffer = "";                parseState = 10; }
            else if (c == 'a') { buffer = "";                parseState = 11; }
            else if (c == 'd') { dumpEeprom();               parseState = 0; }
            else if (c == 'R') { resetProvisioning();       parseState = 0; } // Unprovision (back to advertising)
            else if (c == 'e') {
              for (int i = 0; i < 64; i++) {
                uint16_t empty = 0xFFFF;
                EEPROM.put(ADDR_MAP_START + (i * 2), empty);
              }
              parseState = 0;
            }
            else parseState = 0;
          } else {
            parseState = 0;
          }

          idBuffer    = "";
          idWildcard  = false;
          idProvision = false;
        }
        break;

      // State 4: '-' command — the next single byte is the target display character.
      case 4:
        moveToChar(c);
        parseState = 0;
        break;

      // State 5: 'o' command — read digits for new home offset value.
      case 5:
        if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0) {
            stepsFromHallToZero = buffer.toInt();
            saveHomeOffset();
          }
          parseState = 0;
        }
        break;

      // State 6: 't' command — read digits for new total-steps-per-revolution value.
      case 6:
        if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0) {
            totalStepsPerRev = buffer.toInt();
            saveTotalSteps();
          }
          parseState = 0;
        }
        break;

      // State 7: 's' command — nudge the reel by N steps and accumulate into home offset.
      case 7:
        if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0) {
            int stepsToMove = buffer.toInt();
            stepBackward(stepsToMove);
            releaseMotor();
            stepsFromHallToZero += stepsToMove;
            saveHomeOffset();
          }
          parseState = 0;
        }
        break;

      // State 8: 'g' command — move to an absolute raw step position.
      case 8:
        if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0) {
            long targetStep  = buffer.toInt();
            long stepsToMove = targetStep - currentStepPos;
            if (stepsToMove < 0) stepsToMove += totalStepsPerRev;
            while (stepsToMove > 0) { stepBackward(1); stepsToMove--; }
            releaseMotor();
            currentFlapIndex = -2;
            saveState();
          }
          parseState = 0;
        }
        break;

      // State 9: 'w' command — write a calibrated position into the EEPROM map.
      case 9:
        if (c == ':') {
          tempIndex = buffer.toInt();
          buffer    = "";
        } else if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0 && tempIndex != -1) {
            uint16_t pos = buffer.toInt();
            EEPROM.put(ADDR_MAP_START + (tempIndex * 2), pos);
          }
          parseState = 0;
        }
        break;

      // State 10: 'i' command — assign a new module ID and persist it.
      case 10:
        if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0) {
            moduleId = buffer.toInt();
            EEPROM.write(ADDR_MODULE_ID, moduleId);
          }
          parseState = 0;
        }
        break;

      // State 11: 'a' command — enable (1) or disable (0) auto-home on boot.
      case 11:
        if (isDigit(c)) {
          buffer += c;
        } else {
          if (buffer.length() > 0) {
            autoHomeEnabled = (buffer.toInt() == 1);
            EEPROM.write(ADDR_AUTO_HOME, autoHomeEnabled ? 1 : 0);
            saveState();
          }
          parseState = 0;
        }
        break;

      // State 12: '+' command — accumulate numeric index digits, then move to that flap.
      case 12:
        if (isDigit(c)) {
          buffer += c;
        } else {
          moveToIndex(buffer.toInt());
          parseState = 0;
        }
        break;

      // -----------------------------------------------------------------------
      // State 13: 'H' provisioning command — home by serial number.
      //
      // Format:  "mXH<serialNumber>\n"
      // <serialNumber> is the 20-hex-character ATtiny serial (e.g. A3F2...0C).
      // All modules parse this state; only the one whose serialStr matches acts.
      // -----------------------------------------------------------------------
      case 13:
        if (c != '\n' && c != '\r') {
          snBuffer += c;
        } else {
          // Terminator received — check serial number and act if it's ours.
          if (serialMatches(snBuffer)) {
            homeModule();
            saveState();
          }
          snBuffer   = "";
          parseState = 0;
        }
        break;

      // -----------------------------------------------------------------------
      // State 14: 'I' provisioning command — assign ID by serial number.
      //
      // Format:  "mXI<serialNumber>:<newId>\n"
      // <serialNumber> is the 20-hex-character ATtiny serial.
      // <newId> is a 1–3 digit numeric ID (0–254).
      //
      // All modules parse this state.  Only the matching module updates its ID.
      // After assigning the ID the module stops advertising and uses the normal
      // numeric address scheme for all future communication.
      // -----------------------------------------------------------------------
      case 14:
        if (c == ':' && !snColonSeen) {
          // Colon separator: snBuffer now holds the serial number portion.
          snColonSeen = true;
          buffer      = "";
        } else if (c != '\n' && c != '\r') {
          if (!snColonSeen) {
            snBuffer += c;
          } else {
            if (isDigit(c)) buffer += c;
          }
        } else {
          // Terminator: commit if serial matches.
          if (snColonSeen && buffer.length() > 0 && serialMatches(snBuffer)) {
            moduleId = (uint8_t)buffer.toInt();
            EEPROM.write(ADDR_MODULE_ID, moduleId);
            // Optionally acknowledge the assignment back to the Pi.
            delay(20);
            digitalWrite(RS485_DE, HIGH);
            delay(5);
            rs485.print("mXack:");
            rs485.print(serialStr);
            rs485.print(":");
            rs485.print(moduleId);
            rs485.print("\n");
            delay(50);
            digitalWrite(RS485_DE, LOW);
          }
          snBuffer    = "";
          snColonSeen = false;
          buffer      = "";
          parseState  = 0;
        }
        break;
    }
  }

  // ---- Timeout: flush incomplete numeric commands ----
  if ((parseState >= 5 && parseState <= 12) && (millis() - lastSerialTime > 50)) {
    if (buffer.length() > 0) {
      if (parseState == 5) { stepsFromHallToZero = buffer.toInt(); saveHomeOffset(); }
      if (parseState == 6) { totalStepsPerRev = buffer.toInt(); saveTotalSteps(); }
      if (parseState == 7) {
        int stepsToMove = buffer.toInt();
        stepBackward(stepsToMove);
        releaseMotor();
        stepsFromHallToZero += stepsToMove;
        saveHomeOffset();
      }
      if (parseState == 8) {
        long targetStep  = buffer.toInt();
        long stepsToMove = targetStep - currentStepPos;
        if (stepsToMove < 0) stepsToMove += totalStepsPerRev;
        while (stepsToMove > 0) { stepBackward(1); stepsToMove--; }
        releaseMotor();
        currentFlapIndex = -2;
        saveState();
      }
      if (parseState == 9 && tempIndex != -1) {
        uint16_t pos = buffer.toInt();
        EEPROM.put(ADDR_MAP_START + (tempIndex * 2), pos);
      }
      if (parseState == 10) {
        moduleId = buffer.toInt();
        EEPROM.write(ADDR_MODULE_ID, moduleId);
      }
      if (parseState == 11) {
        autoHomeEnabled = (buffer.toInt() == 1);
        EEPROM.write(ADDR_AUTO_HOME, autoHomeEnabled ? 1 : 0);
        saveState();
      }
      if (parseState == 12) {
        moveToIndex(buffer.toInt());
      }
    }
    parseState = 0;
  }

  // Provisioning states (13, 14) also time out — abandon an incomplete frame.
  if ((parseState == 13 || parseState == 14) && (millis() - lastSerialTime > 200)) {
    snBuffer    = "";
    snColonSeen = false;
    buffer      = "";
    parseState  = 0;
  }
}
