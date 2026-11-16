#!/usr/bin/env python3
"""Flash a Varmilo VA21M from Linux through its Holtek IAP bootloader.

Usage: va21m_flash.py verify firmware.MTP   compare the keyboard with a file
       va21m_flash.py flash firmware.MTP    erase, program and verify
       va21m_flash.py enter | exit          switch to the bootloader / back

The keyboard program accepts a vendor command that restarts it into the
bootloader (USB 04d9:8022). The bootloader takes 8 byte feature reports as
commands and 64 byte output reports as data. Addresses are relative to the
application: the bootloader adds its own size, so it cannot be overwritten as
long as addresses stay below APPLICATION_WORDS.
"""

import argparse
import fcntl
import glob
import os
import sys
import time

from mtp import read_program

VENDOR_ID = 0x04D9
KEYBOARD_PRODUCT_ID = 0xA002
BOOTLOADER_PRODUCT_ID = 0x8022
VENDOR_USAGE_PAGE = bytes((0x06, 0x01, 0xFF))  # start of the report descriptor

FLASH_WORDS = 0x4000
BOOTLOADER_WORDS = 0x200
APPLICATION_WORDS = FLASH_WORDS - BOOTLOADER_WORDS
PAGE_WORDS = 0x40
PAGE_BYTES = PAGE_WORDS * 2
PACKET_BYTES = 64

# Keyboard program: arm the jump, then read the result back to trigger it.
ENTER_BOOTLOADER = bytes.fromhex("0faa55cc33bb99ee")
CONFIRM_BOOTLOADER = bytes.fromhex("8f00000000000000")
CONFIRM_ACCEPTED = bytes.fromhex("8fff")
SIGNATURE = b"iap-hw"
CMD_RESTART = 0x08
CMD_ERASE_PAGE = 1
CMD_VERIFY = 2
CMD_WRITE = 3
CMD_WRITE_ENABLE = 4
CMD_BOOT_FLAG = 6
CMD_STAY = 7
BOOT_APPLICATION = 0x06  # low nibble of the boot flag: start the application
STATUS_MISMATCH = 0x80
STATUS_POLLS = 50


class ProtocolError(Exception):
    pass


def application_image(words: list[int]) -> bytes:
    """Bytes of the application part of a full program image."""
    if len(words) != FLASH_WORDS:
        raise ValueError(f"expected {FLASH_WORDS} program words, got {len(words)}")
    image = bytearray()
    for word in words[BOOTLOADER_WORDS:]:
        image += bytes((word & 0xFF, word >> 8))
    return bytes(image)


def address_command(command: int, address: int, length: int) -> bytes:
    """Erase/verify/write command for a word address inside the application."""
    last_word = address + length // 2 - 1
    if address < 0 or address % PAGE_WORDS or last_word >= APPLICATION_WORDS:
        raise ValueError(f"address {address:#x} is outside the application")
    return bytes((command, address & 0xFF, address >> 8,
                  length & 0xFF, length >> 8, 0, 0, 0))


def boot_flag_command(flag: int) -> bytes:
    return bytes((CMD_BOOT_FLAG, flag)) + SIGNATURE


def simple_command(command: int, argument: int = 0) -> bytes:
    return bytes((command, argument, 0, 0, 0, 0, 0, 0))


def pages(image: bytes):
    """Yield (word address, page bytes) for every page of the application."""
    if len(image) != APPLICATION_WORDS * 2:
        raise ValueError("image does not cover the whole application")
    for offset in range(0, len(image), PAGE_BYTES):
        yield offset // 2, image[offset:offset + PAGE_BYTES]


class Bootloader:
    """Bootloader session over a transport with get_feature/set_feature/write."""

    def __init__(self, transport, poll_delay: float = 0.005, progress=None):
        self.transport = transport
        self.poll_delay = poll_delay
        self.progress = progress or (lambda done, total: None)

    def status(self) -> tuple[int, int]:
        """Return (status byte, current physical flash address)."""
        report = self.transport.get_feature()
        if len(report) != 8 or report[2:6] != SIGNATURE[:4]:
            raise ProtocolError(f"not a bootloader status report: {report.hex()}")
        return report[1], report[6] | report[7] << 8

    def connect(self) -> int:
        """Check the device and stop it from timing out; return the boot flag."""
        status, _ = self.status()
        self.transport.set_feature(simple_command(CMD_STAY))
        return status & 0x7F

    def _send_page(self, command: int, address: int, data: bytes) -> int:
        """Send one page for writing or verifying; return the final status."""
        self.transport.set_feature(address_command(command, address, len(data)))
        expected = address + BOOTLOADER_WORDS
        status = 0
        for offset in range(0, len(data), PACKET_BYTES):
            self.transport.write(data[offset:offset + PACKET_BYTES])
            expected += PACKET_BYTES // 2
            status |= self._wait_for_address(expected)
        return status

    def _wait_for_address(self, expected: int) -> int:
        """Poll until the address counter matches; return all status bits seen.

        Reading the status clears the mismatch bit, so it must be collected
        from every read.
        """
        seen = 0
        for _ in range(STATUS_POLLS):
            status, address = self.status()
            seen |= status
            if address == expected:  # the counter is not wrapped at 0x4000
                return seen
            time.sleep(self.poll_delay)
        raise ProtocolError(f"device did not reach address {expected:#06x}")

    def verify(self, image: bytes) -> list[int]:
        """Compare flash with `image`; return addresses of differing pages."""
        different = []
        all_pages = list(pages(image))
        for index, (address, data) in enumerate(all_pages):
            if self._send_page(CMD_VERIFY, address, data) & STATUS_MISMATCH:
                different.append(address)
            self.progress(index + 1, len(all_pages))
        return different

    def flash(self, image: bytes) -> None:
        """Erase and program the whole application. Call finish() afterwards."""
        all_pages = list(pages(image))
        flag, _ = self.status()
        # Until finish(), a restart must land in the bootloader again.
        self.transport.set_feature(boot_flag_command(flag & 0x7C))
        self.transport.set_feature(simple_command(CMD_WRITE_ENABLE, 1))
        try:
            for index, (address, data) in enumerate(all_pages):
                self.transport.set_feature(
                    address_command(CMD_ERASE_PAGE, address, PAGE_BYTES))
                self._send_page(CMD_WRITE, address, data)
                self.progress(index + 1, len(all_pages))
        finally:
            self.transport.set_feature(simple_command(CMD_WRITE_ENABLE, 0))

    def finish(self) -> None:
        """Mark the application as bootable and restart into it."""
        flag, _ = self.status()
        self.transport.set_feature(
            boot_flag_command((flag & 0x70) | BOOT_APPLICATION))
        try:
            self.transport.set_feature(simple_command(CMD_RESTART))
        except OSError:
            pass  # the device restarts before it acknowledges the command


class HidrawTransport:
    """Feature and output reports on a /dev/hidraw node (no report IDs)."""

    HIDIOCSFEATURE_9 = 0xC0094806
    HIDIOCGFEATURE_9 = 0xC0094807

    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR)

    def set_feature(self, report: bytes) -> None:
        fcntl.ioctl(self.fd, self.HIDIOCSFEATURE_9, b"\x00" + bytes(report))

    def get_feature(self) -> bytes:
        buffer = bytearray(9)
        fcntl.ioctl(self.fd, self.HIDIOCGFEATURE_9, buffer)
        return bytes(buffer[1:])

    def write(self, packet: bytes) -> None:
        os.write(self.fd, b"\x00" + bytes(packet))

    def close(self) -> None:
        os.close(self.fd)


def find_hidraw(product_id: int, descriptor_prefix: bytes = b"") -> str | None:
    """Find the hidraw node of a Holtek device by product and descriptor."""
    wanted = f"HID_ID=0003:{VENDOR_ID:08X}:{product_id:08X}"
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(f"{node}/device/uevent") as handle:
                identity = handle.read()
            with open(f"{node}/device/report_descriptor", "rb") as handle:
                descriptor = handle.read()
        except OSError:
            continue  # node vanished while the device re-enumerated
        if wanted in identity and descriptor.startswith(descriptor_prefix):
            return "/dev/" + os.path.basename(node)
    return None


def wait_for_hidraw(product_id: int, prefix: bytes = b"", timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = find_hidraw(product_id, prefix)
        if path and os.access(path, os.R_OK | os.W_OK):
            return path
        time.sleep(0.2)
    raise ProtocolError(f"device 04d9:{product_id:04x} not found or not accessible")


def enter_bootloader() -> str:
    """Make sure the device is in the bootloader; return its hidraw path."""
    path = find_hidraw(BOOTLOADER_PRODUCT_ID)
    if path:
        return path
    keyboard = wait_for_hidraw(KEYBOARD_PRODUCT_ID, VENDOR_USAGE_PAGE, timeout=2)
    transport = HidrawTransport(keyboard)
    try:
        transport.set_feature(ENTER_BOOTLOADER)
        transport.set_feature(CONFIRM_BOOTLOADER)
        reply = transport.get_feature()
    finally:
        transport.close()
    if reply[:2] != CONFIRM_ACCEPTED:
        raise ProtocolError(f"keyboard refused to enter the bootloader: {reply.hex()}")
    return wait_for_hidraw(BOOTLOADER_PRODUCT_ID)


def show_progress(done: int, total: int) -> None:
    print(f"\r  page {done}/{total}", end="" if done < total else "\n", flush=True)


def load_image(path: str) -> bytes:
    with open(path, "rb") as handle:
        return application_image(read_program(handle.read()))


def run(action: str, firmware: str | None) -> int:
    image = load_image(firmware) if firmware else None
    transport = HidrawTransport(enter_bootloader())
    session = Bootloader(transport, progress=show_progress)
    flag = session.connect()
    print(f"bootloader connected, boot flag {flag:#04x}")
    result = 0
    if action == "flash":
        print("erasing and programming")
        session.flash(image)
    if action in ("verify", "flash"):
        print("verifying")
        different = session.verify(image)
        result = 1 if different else 0
        print("flash matches the file" if not different else
              "pages that differ: " + " ".join(f"{a:04X}" for a in different))
    flash_failed = action == "flash" and result != 0
    if action != "enter" and not flash_failed:
        session.finish()
        print("restarted into the keyboard program")
    transport.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("enter", "verify", "flash", "exit"))
    parser.add_argument("firmware", nargs="?")
    args = parser.parse_args()
    if args.action in ("verify", "flash") and not args.firmware:
        parser.error(f"{args.action} needs a firmware file")
    try:
        return run(args.action, args.firmware)
    except (ProtocolError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
