"""Tests for the flasher protocol, run against a model of the bootloader.

The model follows the bootloader code found in the firmware image: commands
are 8 byte feature reports, data arrives in 64 byte packets, and every address
is shifted by the bootloader size before it reaches the flash.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mtp  # noqa: E402
import va21m_flash as flash  # noqa: E402

FIRMWARE = ROOT / "ZHH-KB-20161115-BE40H.MTP"
BOOTLOADER_WORDS = 0x200
FLASH_WORDS = 0x4000
PAGE_WORDS = 0x40
SIGNATURE = b"iap-hw"


class FakeBootloader:
    """Transport that behaves like the HT68FB560 IAP bootloader."""

    def __init__(self, flash_words):
        self.flash = list(flash_words)
        self.flag = 0x64  # what the keyboard program leaves on entry
        self.address = 0
        self.unlocked = False
        self.mode = None
        self.mismatch = False
        self.stay = False
        self.restarted = False
        self.log = []

    def get_feature(self) -> bytes:
        status = (self.flag & 0x7F) | (0x80 if self.mismatch else 0)
        self.mismatch = False  # the real device clears it on every read
        return bytes((0x40, status)) + b"iap-" + bytes(
            (self.address & 0xFF, self.address >> 8))

    def set_feature(self, report: bytes) -> None:
        assert len(report) == 8
        self.log.append(bytes(report))
        command = report[0] & 7
        self.mode = None
        self.mismatch = False
        if command == 0 and report[0] & 8:
            self.restarted = True
        elif command in (1, 2, 3):
            assert report[5] == 0, "raw addressing must never be used"
            self.address = (report[1] | report[2] << 8) + BOOTLOADER_WORDS
            self._start(command)
        elif command == 4:
            self.unlocked = bool(report[1] & 1)
        elif command == 6 and report[2:] == SIGNATURE:
            self.flag = report[1]
        elif command == 7:
            self.stay = True

    def _start(self, command: int) -> None:
        if command == 1:
            if self.unlocked:
                page = self.address & ~(PAGE_WORDS - 1)
                self.flash[page:page + PAGE_WORDS] = [0] * PAGE_WORDS
        else:
            self.mode = "verify" if command == 2 else "write"

    def write(self, packet: bytes) -> None:
        assert len(packet) == 64
        words = [packet[i] | packet[i + 1] << 8 for i in range(0, 64, 2)]
        for word in words:
            if self.mode == "verify":
                self.mismatch |= self.flash[self.address] != word
            elif self.mode == "write" and self.unlocked:
                # Flash can only be programmed after an erase.
                self.flash[self.address] |= word
            # As on the real device, the counter runs on to 0x4000.
            self.address += 1


def firmware_words():
    return mtp.read_program(FIRMWARE.read_bytes())


class ImageTest(unittest.TestCase):
    def test_image_is_application_part_only(self):
        words = firmware_words()
        image = flash.application_image(words)
        self.assertEqual(len(image), (FLASH_WORDS - BOOTLOADER_WORDS) * 2)
        self.assertEqual(image[:2], bytes((0x04, 0x1F)))  # CLR BP

    def test_wrong_program_size_is_rejected(self):
        with self.assertRaises(ValueError):
            flash.application_image([0] * 0x2000)


class CommandTest(unittest.TestCase):
    """Byte layouts taken from the vendor tool."""

    def test_enter_bootloader_report(self):
        self.assertEqual(flash.ENTER_BOOTLOADER,
                         bytes.fromhex("0faa55cc33bb99ee"))
        self.assertEqual(flash.CONFIRM_BOOTLOADER,
                         bytes.fromhex("8f00000000000000"))

    def test_set_address_layout(self):
        self.assertEqual(flash.address_command(3, 0x1240, 128),
                         bytes.fromhex("0340128000000000"))

    def test_address_beyond_application_is_rejected(self):
        # 0x3E00 would wrap around into the bootloader.
        with self.assertRaises(ValueError):
            flash.address_command(3, 0x3E00, 128)
        with self.assertRaises(ValueError):
            flash.address_command(1, 0x3DC1, 128)

    def test_boot_flag_command(self):
        self.assertEqual(flash.boot_flag_command(0x66),
                         bytes((6, 0x66)) + SIGNATURE)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.words = firmware_words()
        self.image = flash.application_image(self.words)
        self.device = FakeBootloader(self.words)
        self.session = flash.Bootloader(self.device, poll_delay=0)

    def test_connect_checks_signature_and_holds_bootloader(self):
        self.session.connect()
        self.assertTrue(self.device.stay)

    def test_connect_rejects_other_devices(self):
        self.device.get_feature = lambda: bytes(8)
        with self.assertRaises(flash.ProtocolError):
            self.session.connect()

    def test_verify_matches_identical_flash(self):
        self.assertEqual(self.session.verify(self.image), [])

    def test_verify_does_not_modify_or_unlock(self):
        before = list(self.device.flash)
        self.session.verify(self.image)
        self.assertEqual(self.device.flash, before)
        self.assertFalse(self.device.unlocked)
        self.assertFalse(any(report[0] & 7 in (1, 3, 4, 6)
                             for report in self.device.log))

    def test_verify_reports_differing_pages(self):
        self.device.flash[BOOTLOADER_WORDS + 0x1455] ^= 1
        self.device.flash[BOOTLOADER_WORDS + 0x03AE] ^= 1
        # One difference in the first packet of a page, one in the second.
        self.assertEqual(self.session.verify(self.image), [0x0380, 0x1440])

    def test_flash_writes_application_and_keeps_bootloader(self):
        new_words = list(self.words)
        new_words[BOOTLOADER_WORDS + 0x1455] = 0x7C09
        new_words[0x3FFF] = 0x1234
        self.session.flash(flash.application_image(new_words))
        self.assertEqual(self.device.flash, new_words)
        self.assertFalse(self.device.unlocked)

    def test_flash_marks_application_invalid_until_finished(self):
        self.device.flag = 0x66
        self.session.flash(self.image)
        # Still pointing at the bootloader: finish() has not run yet.
        self.assertEqual(self.device.flag & 0x03, 0)

    def test_flash_detects_failed_write(self):
        self.device.write = lambda packet: None  # packets vanish
        with self.assertRaises(flash.ProtocolError):
            self.session.flash(self.image)

    def test_finish_selects_application_and_restarts(self):
        self.session.finish()
        self.assertEqual(self.device.flag, 0x66)
        self.assertTrue(self.device.restarted)
        self.assertEqual(self.device.log[-1], bytes((8, 0, 0, 0, 0, 0, 0, 0)))

    def test_finish_tolerates_device_vanishing_on_restart(self):
        accept = self.device.set_feature

        def vanish_on_restart(report):
            accept(report)
            if report[0] == 8:
                raise BrokenPipeError()

        self.device.set_feature = vanish_on_restart
        self.session.finish()
        self.assertTrue(self.device.restarted)


if __name__ == "__main__":
    unittest.main()
