"""Tests for the MTP container, the disassembler and the VA21M patch."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ht68dis  # noqa: E402
import mtp  # noqa: E402
import patch_va21m  # noqa: E402

FIRMWARE = ROOT / "ZHH-KB-20161115-BE40H.MTP"
APP_BASE = 0x200
KEYMAP_POINTERS = 0x18D2  # one keymap base address per layer
LED_BYTE = 0x89  # RAM copy of the host LED report
NUM_LOCK_KEY = 0x90
HID_NUM_LOCK = 0x53

# Matrix index of every numpad key and the HID usage it must send.
EXPECTED_KEYS = {
    0x01: 0x29, 0x02: 0x2B, 0x03: 0x2E, 0x04: 0x2A,  # Esc Tab = Backspace
    0x90: 0x53, 0x98: 0x54, 0x94: 0x55, 0x9C: 0x56,  # NumLock / * -
    0x91: 0x5F, 0x99: 0x60, 0x9B: 0x61, 0x8C: 0x57,  # 7 8 9 +
    0x92: 0x5C, 0x9A: 0x5D, 0x93: 0x5E,              # 4 5 6
    0x7B: 0x59, 0x83: 0x5A, 0x8B: 0x5B, 0x84: 0x58,  # 1 2 3 Enter
    0x95: 0x62, 0x9D: 0x63,                          # 0 .
}


def keymap_entry(words: list[int], layer: int, key: int) -> tuple[int, int]:
    """Look a key up the way the firmware does: pointer table, then 2 words."""
    base = words[APP_BASE + KEYMAP_POINTERS + layer]
    entry = APP_BASE + base + 2 * key
    return words[entry], words[entry + 1]


class MtpContainerTest(unittest.TestCase):
    def setUp(self):
        self.data = FIRMWARE.read_bytes()

    def test_program_is_16k_words(self):
        self.assertEqual(len(mtp.read_program(self.data)), 0x4000)

    def test_application_reset_vector(self):
        words = mtp.read_program(self.data)
        self.assertEqual(words[APP_BASE], 0x1F04)  # CLR BP

    def test_rewriting_same_program_is_identity(self):
        words = mtp.read_program(self.data)
        self.assertEqual(mtp.write_program(self.data, words), self.data)

    def test_changed_program_keeps_record_sum_zero(self):
        words = mtp.read_program(self.data)
        words[0x3FFF] ^= 0x1234
        out = mtp.write_program(self.data, words)
        record = mtp.find_program_record(out)
        header = out[record.offset:record.offset + mtp.RECORD_HEADER_LEN]
        self.assertEqual((sum(header) + sum(record.payload)) & 0xFF, 0)
        self.assertEqual(mtp.read_program(out), words)
        self.assertEqual(len(out), len(self.data))

    def test_wrong_word_count_is_rejected(self):
        with self.assertRaises(ValueError):
            mtp.write_program(self.data, [0] * 16)

    def test_corrupt_file_is_rejected(self):
        broken = bytearray(self.data)
        broken[0x1000] ^= 0xFF
        with self.assertRaises(ValueError):
            mtp.read_program(bytes(broken))


class DisassemblerTest(unittest.TestCase):
    def test_known_encodings(self):
        cases = {
            0x0003: "RET", 0x0F5A: "MOV A,5Ah", 0x4089: "MOV [89h],A",
            0x0707: "MOV A,TBLP", 0x7C09: "SZ [89h].0", 0x50D9: "SZ [D9h]",
            0x3423: "CLR PBC.0", 0x2BC5: "JMP 03C5", 0xA455: "CALL 1455",
            0x6FCC: "JMP 0FCC", 0x1D07: "TABRD TBLP",
        }
        for word, text in cases.items():
            self.assertEqual(ht68dis.disassemble(word), text)


class PatchTest(unittest.TestCase):
    def setUp(self):
        self.original = mtp.read_program(FIRMWARE.read_bytes())
        self.patched = patch_va21m.patch_words(self.original)

    def test_original_layers_differ_and_toggle_locally(self):
        # Guards the test's own model of the firmware.
        self.assertEqual(keymap_entry(self.original, 1, 0x91), (0, 0x5F))
        self.assertEqual(keymap_entry(self.original, 0, 0x91), (0, 0x4A))
        self.assertEqual(keymap_entry(self.original, 1, NUM_LOCK_KEY)[0], 7)

    def test_every_key_sends_keypad_usage_on_both_layers(self):
        for layer in (0, 1):
            for key, usage in EXPECTED_KEYS.items():
                self.assertEqual(keymap_entry(self.patched, layer, key), (0, usage),
                                 f"layer {layer} key {key:#x}")

    def test_num_lock_key_is_a_plain_hid_key(self):
        for layer in (0, 1):
            self.assertEqual(keymap_entry(self.patched, layer, NUM_LOCK_KEY),
                             (0, HID_NUM_LOCK))

    def test_led_routine_tests_host_num_lock_bit(self):
        led_routine = APP_BASE + 0x1455
        self.assertEqual(ht68dis.disassemble(self.patched[led_routine]),
                         f"SZ [{LED_BYTE:02X}h].0")
        # The rest of the routine (drive PB0) must be untouched.
        self.assertEqual(self.patched[led_routine + 1:led_routine + 8],
                         self.original[led_routine + 1:led_routine + 8])

    def test_host_num_lock_forcing_is_bypassed(self):
        # Keypad digits must take the same path as any other key.
        branch = self.patched[APP_BASE + 0x03AE]
        self.assertEqual(ht68dis.disassemble(branch), "JMP 03C5")

    def test_bootloader_is_untouched(self):
        self.assertEqual(self.patched[:APP_BASE], self.original[:APP_BASE])

    def test_only_expected_words_change(self):
        changed = {i - APP_BASE for i, (a, b)
                   in enumerate(zip(self.original, self.patched)) if a != b}
        allowed = {0x03AE, 0x1455}
        for base in (0x1A00, 0x1C80):
            for key in EXPECTED_KEYS:
                allowed |= {base + 2 * key, base + 2 * key + 1}
        self.assertTrue(changed)
        self.assertLessEqual(changed, allowed)

    def test_keypad_equals_option(self):
        patched = patch_va21m.patch_words(self.original, keypad_equals=True)
        for layer in (0, 1):
            self.assertEqual(keymap_entry(patched, layer, 0x03), (0, 0x67))

    def test_patching_twice_is_rejected(self):
        with self.assertRaises(ValueError):
            patch_va21m.patch_words(self.patched)

    def test_unknown_firmware_is_rejected(self):
        other = list(self.original)
        other[APP_BASE + 0x1455] = 0x0000
        with self.assertRaises(ValueError):
            patch_va21m.patch_words(other)

    def test_patched_file_round_trips(self):
        data = FIRMWARE.read_bytes()
        out = patch_va21m.patch_file(data)
        self.assertEqual(mtp.read_program(out), self.patched)
        self.assertEqual(len(out), len(data))
        record = mtp.find_program_record(data)
        self.assertEqual(out[:record.offset], data[:record.offset])
        end = record.offset + mtp.RECORD_HEADER_LEN + len(record.payload)
        self.assertEqual(out[end:], data[end:])


if __name__ == "__main__":
    unittest.main()
