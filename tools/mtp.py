"""Read and write Holtek .MTP firmware containers (as used by ISPTool / HASM).

Layout: a short fixed header, then records `tag(1) length(2, LE) payload`.
The last payload byte of every record is a checksum that makes the byte sum of
the whole record equal to zero (mod 256).

Record 0x65 with sub-address 0 holds the program memory: a 4 byte prefix
followed by 16-bit little-endian words.
"""

from dataclasses import dataclass

PROGRAM_TAG = 0x65
PROGRAM_PREFIX_LEN = 4
RECORD_HEADER_LEN = 3


@dataclass
class Record:
    offset: int
    tag: int
    payload: bytes  # includes the trailing checksum byte


def record_checksum(tag: int, body: bytes) -> int:
    """Checksum for a record whose payload (without checksum) is `body`."""
    length = len(body) + 1
    total = tag + (length & 0xFF) + (length >> 8) + sum(body)
    return (-total) & 0xFF


def find_program_record(data: bytes) -> Record:
    """Locate the program record by scanning for a self-consistent 0x65 record."""
    for offset in range(len(data) - RECORD_HEADER_LEN):
        if data[offset] != PROGRAM_TAG:
            continue
        length = data[offset + 1] | data[offset + 2] << 8
        end = offset + RECORD_HEADER_LEN + length
        if length < PROGRAM_PREFIX_LEN + 3 or end > len(data):
            continue
        payload = data[offset + RECORD_HEADER_LEN:end]
        if payload[:PROGRAM_PREFIX_LEN] != bytes(PROGRAM_PREFIX_LEN):
            continue
        if record_checksum(PROGRAM_TAG, payload[:-1]) == payload[-1]:
            return Record(offset, PROGRAM_TAG, payload)
    raise ValueError("no valid program record found")


def read_program(data: bytes) -> list[int]:
    """Return program memory as a list of 16-bit words."""
    payload = find_program_record(data).payload
    raw = payload[PROGRAM_PREFIX_LEN:-1]
    return [raw[i] | raw[i + 1] << 8 for i in range(0, len(raw), 2)]


def write_program(data: bytes, words: list[int]) -> bytes:
    """Return a copy of `data` with program memory replaced and checksum fixed."""
    record = find_program_record(data)
    old_word_count = (len(record.payload) - PROGRAM_PREFIX_LEN - 1) // 2
    if len(words) != old_word_count:
        raise ValueError(f"expected {old_word_count} words, got {len(words)}")
    body = bytearray(record.payload[:PROGRAM_PREFIX_LEN])
    for word in words:
        if not 0 <= word <= 0xFFFF:
            raise ValueError(f"word out of range: {word:#x}")
        body += bytes((word & 0xFF, word >> 8))
    payload = bytes(body) + bytes((record_checksum(PROGRAM_TAG, bytes(body)),))
    start = record.offset + RECORD_HEADER_LEN
    return data[:start] + payload + data[start + len(payload):]
