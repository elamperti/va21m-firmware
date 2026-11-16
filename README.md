# Varmilo VA21M: host-controlled NumLock firmware

A patched firmware for the Varmilo VA21M numpad that makes it behave like a regular numeric keypad: NumLock is handled by the computer, not by the keyboard.

> **Unofficial and untested on hardware so far.** This is not a Varmilo release. Flash it at your own risk.

## What it changes

The stock firmware (`ZHH-KB-20161115-BE40H.MTP`, as supplied by Varmilo support) keeps its own NumLock state. The NumLock key only switches between two internal layers (digits and navigation keys) and is never sent to the computer. When the computer has NumLock off, the keyboard presses NumLock by itself before sending a digit. The LED shows the internal layer.

`VA21M-hostnumlock.MTP` changes that:

- Every key always sends its standard keypad code (Keypad 0-9, `.`, `/`, `*`, `-`, `+`, Enter).
- The NumLock key sends a normal NumLock keypress to the computer.
- The keyboard no longer presses NumLock on its own.
- The NumLock LED follows the state reported by the computer.

With NumLock off, the computer treats the keys as Home, End, arrows and so on, exactly as it does with any other numpad. Esc, Tab, `=` and Backspace are unchanged.

## Files

| File | Description |
| --- | --- |
| `VA21M-hostnumlock.MTP` | The patched firmware. |
| `ZHH-KB-20161115-BE40H.MTP` | The original firmware, needed to rebuild the patch or to go back. |
| `tools/patch_va21m.py` | Produces the patched firmware from the original. |
| `tools/ht68dis.py` | Disassembler for the keyboard's Holtek HT68FB560 microcontroller. |
| `tools/mtp.py` | Reader and writer for the `.MTP` firmware container. |
| `tests/` | Tests for the tools and the patch. |

## Flashing

Use Holtek's ISPTool on Windows (Varmilo support can provide it together with the upgrade instructions) and load `VA21M-hostnumlock.MTP` instead of the original file. To go back, flash `ZHH-KB-20161115-BE40H.MTP` the same way.

The patch does not touch the bootloader, which is the part of the chip that receives new firmware.

## Rebuilding

Requires Python 3.9 or newer, no extra packages.

```bash
python3 tools/patch_va21m.py ZHH-KB-20161115-BE40H.MTP VA21M-hostnumlock.MTP
```

Add `--keypad-equals` to make the `=` key send Keypad `=` instead of the regular `=`.

```bash
python3 -m unittest discover -s tests
```

## How it works

The firmware file holds a small bootloader followed by the keyboard program. The keyboard program looks every key up in a table, one table per layer. The patch rewrites the keypad entries so both layers are identical and turns the NumLock entry into an ordinary key. It also changes two instructions: one skips the automatic NumLock press, the other makes the LED routine read the NumLock bit sent by the computer instead of the active layer. In total 21 of the 16384 program words differ from the original.

The patcher only accepts the exact original firmware and refuses anything else, including a file that is already patched.
