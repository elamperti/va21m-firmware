# Custom Varmilo VA21M firmware

![Works in my keyboard](https://img.shields.io/badge/in%20my%20keyboard-works-green)

Patched firmware for the Varmilo VA21M numpad that makes it behave like a regular numeric keypad: NumLock is handled by the computer, not by the keyboard.

> **This is not official firmware.** Flash it at your own risk.

## What it changes

The stock firmware (`ZHH-KB-20161115-BE40H.MTP`, as supplied by the manufacturer) kept its own NumLock state. The NumLock key only switched between two internal layers (digits and navigation keys) and was never sent to the computer. When the computer has NumLock off, the keyboard pressed NumLock by itself before sending a digit (yikes) and the LED used to show only the internal layer.

This firmware (`VA21M-hostnumlock.MTP`) changes that:

- Every key always sends its standard keypad code (Keypad 0-9, `.`, `/`, `*`, `-`, `+`, Enter).
- The NumLock key sends a normal NumLock keypress to the computer.
- The keyboard no longer presses NumLock on its own.
- The NumLock LED follows the state reported by the computer.

With NumLock off, the computer treats the keys as Home, End, arrows and so on, exactly as it does with any other numpad. Esc, Tab, `=` and Backspace keys are unchanged.

## Files

| File | Description |
| --- | --- |
| `VA21M-hostnumlock.MTP` | The patched firmware. |
| `ZHH-KB-20161115-BE40H.MTP` | The original firmware, needed to go back. |
| `tools/va21m_flash.py` | Linux flasher (see below). |
| `tools/mtp.py` | Reader for the `.MTP` firmware container, used by the flasher. |

## Flashing

Use Holtek's ISPTool on Windows and load `VA21M-hostnumlock.MTP` instead of the original file. To go back, flash the official firmware `ZHH-KB-20161115-BE40H.MTP` in the same way.

The patch does not touch the bootloader (the part of the chip that receives new firmware).

### From Linux

`tools/va21m_flash.py` talks to the keyboard's bootloader directly through `/dev/hidraw`. It has been used to flash one real VA21M successfully! It requires Python 3.9 or newer with no extra packages, and needs read and write access to the keyboard's hidraw devices (run it as root or add a udev rule for USB IDs `04d9:a002` and `04d9:8022`).

```bash
python3 tools/va21m_flash.py verify ZHH-KB-20161115-BE40H.MTP
```

```bash
python3 tools/va21m_flash.py flash VA21M-hostnumlock.MTP
```

`verify` only compares the keyboard's contents with a file. `flash` erases, programs and verifies, and restarts the keyboard only if the verification passes. While it works the keyboard shows up as USB device `04d9:8022`; `exit` switches it back to normal.

## How it works

The firmware file holds a small bootloader followed by the keyboard program. The keyboard program looks every key up in a table, one table per layer. The patch rewrites the keypad entries so both layers are identical and turns the NumLock entry into an ordinary key. It also changes two instructions: one skips the automatic NumLock press, the other makes the LED routine read the NumLock bit sent by the computer instead of the active layer. In total only 21 of the 16384 program words differ from the original.
