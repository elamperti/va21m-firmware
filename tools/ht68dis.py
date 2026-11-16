#!/usr/bin/env python3
"""Disassembler for the 16-bit Holtek HT68FB560 instruction encoding.

The encoding was derived from the firmware itself (no public opcode table):
the classic 14-bit Holtek layout, with bit 14 used as bit 7 of the data memory
address and bits 14/15 as bits 11/12 of JMP/CALL targets.

The image holds a 0x200 word IAP bootloader followed by the application,
which is linked at address 0. Pass base=200 to list the application with its
own addresses.

Usage: ht68dis.py firmware.MTP [base [start [end]]]  (hex, file word offsets)
"""

import sys

from mtp import read_program

SFR = {
    0x00: "IAR0", 0x01: "MP0", 0x02: "IAR1", 0x03: "MP1", 0x04: "BP",
    0x05: "ACC", 0x06: "PCL", 0x07: "TBLP", 0x08: "TBLH", 0x09: "TBHP",
    0x0A: "STATUS", 0x0B: "SMOD", 0x0C: "LVDC", 0x0D: "INTEG", 0x0E: "WDTC",
    0x10: "INTC0", 0x11: "INTC1", 0x12: "INTC2", 0x14: "MFI0", 0x15: "MFI1",
    0x18: "PAWU", 0x19: "PAPU", 0x1A: "PA", 0x1B: "PAC", 0x1C: "PADIR",
    0x1F: "PXWU", 0x20: "PXPU", 0x22: "PB", 0x23: "PBC", 0x24: "PC",
    0x25: "PCC", 0x26: "PD", 0x27: "PDC", 0x28: "PE", 0x29: "PEC",
    0x37: "I2CTOC", 0x38: "SIMC0", 0x39: "SIMC1", 0x3A: "SIMD", 0x3B: "SIMA",
    0x3C: "SPIAC0", 0x3D: "SPIAC1", 0x3E: "SPIAD", 0x3F: "SBSC",
    0x40: "FRCR", 0x41: "FCR", 0x42: "FARL", 0x43: "FD0L", 0x44: "FD1L",
    0x45: "FD2L", 0x46: "FD3L", 0x47: "TMPC0", 0x48: "TMPC1", 0x49: "TM0C0",
    0x4A: "TM0C1", 0x4C: "TM0DL", 0x4D: "TM0DH", 0x4E: "TM0AL", 0x4F: "TM0AH",
    0x50: "TM0RP", 0x52: "TM1C0", 0x53: "TM1C1", 0x54: "TM1DL", 0x55: "TM1DH",
    0x56: "TM1AL", 0x57: "TM1AH", 0x58: "TM2C0", 0x59: "TM2C1", 0x5A: "TM2DL",
    0x5B: "TM2DH", 0x5C: "TM2AL", 0x5D: "TM2AH", 0x5E: "TM3C0", 0x5F: "TM3C1",
    0x60: "TM3DL", 0x61: "TM3DH", 0x62: "TM3AL", 0x63: "TM3AH",
    0x64: "USB_STAT", 0x65: "UINT", 0x66: "USC", 0x67: "USR", 0x68: "UCC",
    0x69: "AWR", 0x6A: "STLI", 0x6B: "STLO", 0x6C: "SIES", 0x6D: "MISC",
    0x6E: "UFIEN", 0x6F: "UFOEN", 0x70: "UFC0", 0x71: "UFC1", 0x72: "FIFO0",
    0x73: "FIFO1", 0x74: "FIFO2", 0x75: "FIFO3", 0x76: "FIFO4", 0x77: "FIFO5",
    0x78: "FIFO6", 0x79: "FIFO7", 0x7A: "CTRL", 0x7B: "LVRC", 0x7C: "PDPS",
    0x7D: "PAPS0", 0x7E: "PAPS1", 0x7F: "SYSC",
}

IMPLIED = {0: "NOP", 1: "CLR WDT1", 2: "HALT", 3: "RET", 4: "RETI", 5: "CLR WDT2"}
IMMEDIATE = {0x09: "RET A,", 0x0A: "SUB A,", 0x0B: "ADD A,", 0x0C: "XOR A,",
             0x0D: "OR A,", 0x0E: "AND A,", 0x0F: "MOV A,"}
# (opcode high bits, dest flag) -> template
MEMORY = {
    (0x00, 1): "MOV {m},A", (0x01, 0): "CPLA {m}", (0x01, 1): "CPL {m}",
    (0x02, 0): "SUB A,{m}", (0x02, 1): "SUBM A,{m}",
    (0x03, 0): "ADD A,{m}", (0x03, 1): "ADDM A,{m}",
    (0x04, 0): "XOR A,{m}", (0x04, 1): "XORM A,{m}",
    (0x05, 0): "OR A,{m}", (0x05, 1): "ORM A,{m}",
    (0x06, 0): "AND A,{m}", (0x06, 1): "ANDM A,{m}",
    (0x07, 0): "MOV A,{m}",
    (0x10, 0): "SZA {m}", (0x10, 1): "SZ {m}",
    (0x11, 0): "SWAPA {m}", (0x11, 1): "SWAP {m}",
    (0x12, 0): "SBC A,{m}", (0x12, 1): "SBCM A,{m}",
    (0x13, 0): "ADC A,{m}", (0x13, 1): "ADCM A,{m}",
    (0x14, 0): "INCA {m}", (0x14, 1): "INC {m}",
    (0x15, 0): "DECA {m}", (0x15, 1): "DEC {m}",
    (0x16, 0): "SIZA {m}", (0x16, 1): "SIZ {m}",
    (0x17, 0): "SDZA {m}", (0x17, 1): "SDZ {m}",
    (0x18, 0): "RLA {m}", (0x18, 1): "RL {m}",
    (0x19, 0): "RRA {m}", (0x19, 1): "RR {m}",
    (0x1A, 0): "RLCA {m}", (0x1A, 1): "RLC {m}",
    (0x1B, 0): "RRCA {m}", (0x1B, 1): "RRC {m}",
    (0x1D, 0): "TABRD {m}", (0x1D, 1): "TABRDL {m}",
    (0x1E, 1): "DAA {m}",
    (0x1F, 0): "CLR {m}", (0x1F, 1): "SET {m}",
}
BIT_OPS = ("SET", "CLR", "SNZ", "SZ")
SKIPS = ("SZ", "SNZ", "SIZ", "SDZ")


def mem_name(address: int) -> str:
    return SFR.get(address, f"[{address:02X}h]")


def branch_target(word: int) -> int:
    return (word & 0x7FF) | ((word >> 14) & 1) << 11 | ((word >> 15) & 1) << 12


def disassemble(word: int) -> str:
    """Return the mnemonic for one instruction word, or `DW` if unknown."""
    high = word >> 8
    if (word & 0x3000) == 0x2000:
        name = "JMP" if word & 0x0800 else "CALL"
        return f"{name} {branch_target(word):04X}"
    if word & 0x8000:
        return f"DW {word:04X}"
    mem = (word & 0x7F) | ((word >> 14) & 1) << 7
    if (word & 0x3000) == 0x3000:
        return f"{BIT_OPS[(word >> 10) & 3]} {mem_name(mem)}.{(word >> 7) & 7}"
    if word in IMPLIED:
        return IMPLIED[word]
    if high in IMMEDIATE:
        return f"{IMMEDIATE[high]}{word & 0xFF:02X}h"
    template = MEMORY.get((high & 0x3F, (word >> 7) & 1))
    if template is None:
        return f"DW {word:04X}"
    return template.format(m=mem_name(mem))


def is_skip(text: str) -> bool:
    return text.split()[0].rstrip("A") in SKIPS or text.split()[0] in ("SZA", "SIZA", "SDZA")


def listing(words: list[int], start: int, end: int, base: int = 0) -> list[str]:
    """List file words [start, end); addresses are shown relative to `base`."""
    code = words[base:]
    targets = {branch_target(w) for w in code if (w & 0x3000) == 0x2000}
    lines = []
    for index in range(start, end):
        address = index - base
        word = words[index]
        label = "L" if address in targets else " "
        lines.append(f"{label}{address:04X}  {word:04X}  {disassemble(word)}")
    return lines


def main() -> None:
    with open(sys.argv[1], "rb") as handle:
        words = read_program(handle.read())
    base = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0
    start = int(sys.argv[3], 16) if len(sys.argv) > 3 else base
    end = int(sys.argv[4], 16) if len(sys.argv) > 4 else len(words)
    print("\n".join(listing(words, start, end, base)))


if __name__ == "__main__":
    main()
