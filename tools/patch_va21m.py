#!/usr/bin/env python3
"""Patch the Varmilo VA21M firmware (ZHH-KB-20161115-BE40H) for host NumLock.

Usage: patch_va21m.py input.MTP output.MTP [--keypad-equals]
"""

import argparse

from mtp import read_program, write_program

APP_BASE = 0x200  # the application follows the 0x200 word bootloader
KEYMAP_BASES = (0x1A00, 0x1C80)  # layer 0 (navigation), layer 1 (keypad)
PLAIN_KEY = 0

# Matrix index -> HID usage, identical on both layers after patching.
KEYMAP = {
    0x01: 0x29, 0x02: 0x2B, 0x03: 0x2E, 0x04: 0x2A,  # Esc Tab = Backspace
    0x90: 0x53, 0x98: 0x54, 0x94: 0x55, 0x9C: 0x56,  # NumLock / * -
    0x91: 0x5F, 0x99: 0x60, 0x9B: 0x61, 0x8C: 0x57,  # 7 8 9 +
    0x92: 0x5C, 0x9A: 0x5D, 0x93: 0x5E,              # 4 5 6
    0x7B: 0x59, 0x83: 0x5A, 0x8B: 0x5B, 0x84: 0x58,  # 1 2 3 Enter
    0x95: 0x62, 0x9D: 0x63,                          # 0 .
}
EQUALS_KEY = 0x03
HID_KEYPAD_EQUALS = 0x67

# address -> (original word, new word)
CODE_PATCHES = {
    # Keypad digits no longer inject a NumLock press when the host has it off.
    0x03AE: (0x2BB0, 0x2BC5),  # JMP 03B0 -> JMP 03C5
    # NumLock LED follows the host LED report instead of the local layer.
    0x1455: (0x50D9, 0x7C09),  # SZ [D9h] -> SZ [89h].0
}

# Entries the stock firmware holds at the patched keymap slots, per layer.
STOCK_SPECIAL = (
    {0x7B: (0, 0x4D), 0x83: (0x0D, 0x21), 0x8B: (0, 0x4E), 0x90: (7, 0x0103),
     0x91: (0, 0x4A), 0x92: (0, 0x50), 0x93: (0x0D, 0x23), 0x95: (0, 0x49),
     0x99: (0x0D, 0x20), 0x9A: (0x0F, 0), 0x9B: (0, 0x4B), 0x9D: (0, 0x4C)},
    {0x90: (7, 0x0003)},
)


def stock_entry(layer: int, key: int) -> tuple[int, int]:
    return STOCK_SPECIAL[layer].get(key, (PLAIN_KEY, KEYMAP[key]))


def check_stock(words: list[int]) -> None:
    """Refuse anything that is not the exact firmware this patch was made for."""
    for address, (original, _) in CODE_PATCHES.items():
        if words[APP_BASE + address] != original:
            raise ValueError(f"unexpected code at {address:04X}; wrong firmware "
                             "or already patched")
    for layer, base in enumerate(KEYMAP_BASES):
        for key in KEYMAP:
            slot = APP_BASE + base + 2 * key
            if tuple(words[slot:slot + 2]) != stock_entry(layer, key):
                raise ValueError(f"unexpected keymap entry {key:#x} on layer "
                                 f"{layer}; wrong firmware or already patched")


def patch_words(words: list[int], keypad_equals: bool = False) -> list[int]:
    """Return patched program words; `words` is left untouched."""
    check_stock(words)
    keymap = dict(KEYMAP)
    if keypad_equals:
        keymap[EQUALS_KEY] = HID_KEYPAD_EQUALS
    patched = list(words)
    for address, (_, new) in CODE_PATCHES.items():
        patched[APP_BASE + address] = new
    for base in KEYMAP_BASES:
        for key, usage in keymap.items():
            slot = APP_BASE + base + 2 * key
            patched[slot:slot + 2] = [PLAIN_KEY, usage]
    return patched


def patch_file(data: bytes, keypad_equals: bool = False) -> bytes:
    return write_program(data, patch_words(read_program(data), keypad_equals))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--keypad-equals", action="store_true",
                        help="send Keypad = (0x67) instead of the regular = key")
    args = parser.parse_args()
    with open(args.input, "rb") as handle:
        data = handle.read()
    with open(args.output, "wb") as handle:
        handle.write(patch_file(data, args.keypad_equals))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
