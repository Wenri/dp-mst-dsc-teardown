# SPDX-License-Identifier: WTFPL
"""Minimal MCS-51 (8051) disassembler -- stdlib only.

The VL822/VL817 hubs and the VL103 PD controller all run 8051-core firmware, and
no 8051 disassembler ships on a typical system (capstone has no 8051 core). This
is a compact, complete decoder of the standard 8051 opcode map -- enough to read
the VL822 flash image's reset/interrupt vectors and entry code.

Standard MCS-51 SFR names are applied to direct/bit operands; vendor SFRs and all
XDATA stay numeric (this chip's extra SFRs need VIA's NDA manual to name).
"""
from __future__ import annotations

from dataclasses import dataclass

# Standard 8051 SFR names (direct addresses 0x80..0xFF that are defined in MCS-51)
SFR = {
    0x80: "P0", 0x81: "SP", 0x82: "DPL", 0x83: "DPH", 0x87: "PCON",
    0x88: "TCON", 0x89: "TMOD", 0x8A: "TL0", 0x8B: "TL1", 0x8C: "TH0",
    0x8D: "TH1", 0x90: "P1", 0x98: "SCON", 0x99: "SBUF", 0xA0: "P2",
    0xA8: "IE", 0xB0: "P3", 0xB8: "IP", 0xD0: "PSW", 0xE0: "ACC", 0xF0: "B",
}


def _d(a: int) -> str:
    return SFR.get(a, f"0x{a:02X}")


def _bit(a: int) -> str:
    if a < 0x80:
        return f"0x{0x20 + (a >> 3):02X}.{a & 7}"      # bit-addressable RAM
    return f"{_d(a & 0xF8)}.{a & 7}"                    # bit-addressable SFR


# opcode table: index -> (mnemonic_template, kind). {d}=direct {i}=imm8 {b}=bit
# {r}=rel-target {a}=addr (11/16) {p}=imm16. Register/@Ri operands are baked in.
_T: dict[int, tuple[str, str]] = {}


def _build() -> None:
    g = _T
    g[0x00] = ("NOP", "")
    g[0x02] = ("LJMP {a}", "A16"); g[0x12] = ("LCALL {a}", "A16")
    g[0x03] = ("RR A", ""); g[0x13] = ("RRC A", ""); g[0x23] = ("RL A", "")
    g[0x33] = ("RLC A", ""); g[0x04] = ("INC A", ""); g[0x14] = ("DEC A", "")
    g[0x05] = ("INC {d}", "D"); g[0x15] = ("DEC {d}", "D")
    g[0x06] = ("INC @R0", ""); g[0x07] = ("INC @R1", "")
    g[0x16] = ("DEC @R0", ""); g[0x17] = ("DEC @R1", "")
    g[0x10] = ("JBC {b},{r}", "BR"); g[0x20] = ("JB {b},{r}", "BR")
    g[0x30] = ("JNB {b},{r}", "BR")
    g[0x22] = ("RET", ""); g[0x32] = ("RETI", "")
    g[0x24] = ("ADD A,#{i}", "I"); g[0x25] = ("ADD A,{d}", "D")
    g[0x26] = ("ADD A,@R0", ""); g[0x27] = ("ADD A,@R1", "")
    g[0x34] = ("ADDC A,#{i}", "I"); g[0x35] = ("ADDC A,{d}", "D")
    g[0x36] = ("ADDC A,@R0", ""); g[0x37] = ("ADDC A,@R1", "")
    g[0x40] = ("JC {r}", "R"); g[0x50] = ("JNC {r}", "R")
    g[0x60] = ("JZ {r}", "R"); g[0x70] = ("JNZ {r}", "R")
    g[0x80] = ("SJMP {r}", "R")
    g[0x42] = ("ORL {d},A", "D"); g[0x43] = ("ORL {d},#{i}", "DI")
    g[0x44] = ("ORL A,#{i}", "I"); g[0x45] = ("ORL A,{d}", "D")
    g[0x46] = ("ORL A,@R0", ""); g[0x47] = ("ORL A,@R1", "")
    g[0x52] = ("ANL {d},A", "D"); g[0x53] = ("ANL {d},#{i}", "DI")
    g[0x54] = ("ANL A,#{i}", "I"); g[0x55] = ("ANL A,{d}", "D")
    g[0x56] = ("ANL A,@R0", ""); g[0x57] = ("ANL A,@R1", "")
    g[0x62] = ("XRL {d},A", "D"); g[0x63] = ("XRL {d},#{i}", "DI")
    g[0x64] = ("XRL A,#{i}", "I"); g[0x65] = ("XRL A,{d}", "D")
    g[0x66] = ("XRL A,@R0", ""); g[0x67] = ("XRL A,@R1", "")
    g[0x72] = ("ORL C,{b}", "B"); g[0x73] = ("JMP @A+DPTR", "")
    g[0x74] = ("MOV A,#{i}", "I"); g[0x75] = ("MOV {d},#{i}", "DI")
    g[0x76] = ("MOV @R0,#{i}", "I"); g[0x77] = ("MOV @R1,#{i}", "I")
    g[0x82] = ("ANL C,{b}", "B"); g[0x83] = ("MOVC A,@A+PC", "")
    g[0x84] = ("DIV AB", ""); g[0x85] = ("MOV {d2},{d1}", "DD")
    g[0x86] = ("MOV {d},@R0", "D"); g[0x87] = ("MOV {d},@R1", "D")
    g[0x90] = ("MOV DPTR,#{p}", "I16"); g[0x92] = ("MOV {b},C", "B")
    g[0x93] = ("MOVC A,@A+DPTR", "")
    g[0x94] = ("SUBB A,#{i}", "I"); g[0x95] = ("SUBB A,{d}", "D")
    g[0x96] = ("SUBB A,@R0", ""); g[0x97] = ("SUBB A,@R1", "")
    g[0xA0] = ("ORL C,/{b}", "B"); g[0xA2] = ("MOV C,{b}", "B")
    g[0xA3] = ("INC DPTR", ""); g[0xA4] = ("MUL AB", "")
    g[0xA5] = ("?", ""); g[0xA6] = ("MOV @R0,{d}", "D"); g[0xA7] = ("MOV @R1,{d}", "D")
    g[0xB0] = ("ANL C,/{b}", "B"); g[0xB2] = ("CPL {b}", "B"); g[0xB3] = ("CPL C", "")
    g[0xB4] = ("CJNE A,#{i},{r}", "IR"); g[0xB5] = ("CJNE A,{d},{r}", "DR")
    g[0xB6] = ("CJNE @R0,#{i},{r}", "IR"); g[0xB7] = ("CJNE @R1,#{i},{r}", "IR")
    g[0xC0] = ("PUSH {d}", "D"); g[0xD0] = ("POP {d}", "D")
    g[0xC2] = ("CLR {b}", "B"); g[0xC3] = ("CLR C", ""); g[0xC4] = ("SWAP A", "")
    g[0xC5] = ("XCH A,{d}", "D"); g[0xC6] = ("XCH A,@R0", ""); g[0xC7] = ("XCH A,@R1", "")
    g[0xD2] = ("SETB {b}", "B"); g[0xD3] = ("SETB C", ""); g[0xD4] = ("DA A", "")
    g[0xD5] = ("DJNZ {d},{r}", "DR"); g[0xD6] = ("XCHD A,@R0", ""); g[0xD7] = ("XCHD A,@R1", "")
    g[0xE0] = ("MOVX A,@DPTR", ""); g[0xE2] = ("MOVX A,@R0", ""); g[0xE3] = ("MOVX A,@R1", "")
    g[0xE4] = ("CLR A", ""); g[0xE5] = ("MOV A,{d}", "D")
    g[0xE6] = ("MOV A,@R0", ""); g[0xE7] = ("MOV A,@R1", "")
    g[0xF0] = ("MOVX @DPTR,A", ""); g[0xF2] = ("MOVX @R0,A", ""); g[0xF3] = ("MOVX @R1,A", "")
    g[0xF4] = ("CPL A", ""); g[0xF5] = ("MOV {d},A", "D")
    g[0xF6] = ("MOV @R0,A", ""); g[0xF7] = ("MOV @R1,A", "")
    # Rn families (8 regs)
    for n in range(8):
        g[0x08 + n] = (f"INC R{n}", ""); g[0x18 + n] = (f"DEC R{n}", "")
        g[0x28 + n] = (f"ADD A,R{n}", ""); g[0x38 + n] = (f"ADDC A,R{n}", "")
        g[0x48 + n] = (f"ORL A,R{n}", ""); g[0x58 + n] = (f"ANL A,R{n}", "")
        g[0x68 + n] = (f"XRL A,R{n}", ""); g[0x78 + n] = (f"MOV R{n},#{{i}}", "I")
        g[0x88 + n] = (f"MOV {{d}},R{n}", "D"); g[0x98 + n] = (f"SUBB A,R{n}", "")
        g[0xA8 + n] = (f"MOV R{n},{{d}}", "D"); g[0xB8 + n] = (f"CJNE R{n},#{{i}},{{r}}", "IR")
        g[0xC8 + n] = (f"XCH A,R{n}", ""); g[0xD8 + n] = (f"DJNZ R{n},{{r}}", "R")
        g[0xE8 + n] = (f"MOV A,R{n}", ""); g[0xF8 + n] = (f"MOV R{n},A", "")
    # AJMP/ACALL on the x1/x1 columns (addr11; high bits from opcode)
    for hi in range(8):
        g[(hi << 5) | 0x01] = ("AJMP {a}", "A11")
        g[(hi << 5) | 0x11] = ("ACALL {a}", "A11")


_build()


@dataclass
class Insn:
    addr: int
    length: int
    raw: bytes
    text: str
    target: int | None = None   # branch/call target, if any


def decode_one(buf: bytes, pos: int, base: int = 0) -> Insn:
    """Decode one instruction. ``base`` is the 8051 address of ``buf[0]``."""
    op = buf[pos]
    mnem, kind = _T.get(op, ("DB 0x%02X" % op, ""))
    pc = base + pos
    nb = {"": 0, "D": 1, "I": 1, "B": 1, "R": 1, "A11": 1,
          "A16": 2, "I16": 2, "DI": 2, "DD": 2, "IR": 2, "DR": 2, "BR": 2}[kind]
    ops = buf[pos + 1: pos + 1 + nb]
    length = 1 + nb
    tgt = None
    if kind == "A16":
        tgt = (ops[0] << 8) | ops[1]; text = mnem.format(a=f"0x{tgt:04X}")
    elif kind == "A11":
        a11 = ((op & 0xE0) << 3) | ops[0]; tgt = ((pc + 2) & 0xF800) | a11
        text = mnem.format(a=f"0x{tgt:04X}")
    elif kind == "I":
        text = mnem.format(i=f"0x{ops[0]:02X}")
    elif kind == "I16":
        text = mnem.format(p=f"0x{(ops[0] << 8) | ops[1]:04X}")
    elif kind == "D":
        text = mnem.format(d=_d(ops[0]))
    elif kind == "B":
        text = mnem.format(b=_bit(ops[0]))
    elif kind == "R":
        tgt = (pc + length + ((ops[0] ^ 0x80) - 0x80)) & 0xFFFF
        text = mnem.format(r=f"0x{tgt:04X}")
    elif kind == "DI":
        text = mnem.format(d=_d(ops[0]), i=f"0x{ops[1]:02X}")
    elif kind == "DD":  # 85 src dst  -> MOV dst,src
        text = mnem.format(d1=_d(ops[0]), d2=_d(ops[1]))
    elif kind == "IR":
        tgt = (pc + length + ((ops[1] ^ 0x80) - 0x80)) & 0xFFFF
        text = mnem.format(i=f"0x{ops[0]:02X}", r=f"0x{tgt:04X}")
    elif kind == "DR":
        tgt = (pc + length + ((ops[1] ^ 0x80) - 0x80)) & 0xFFFF
        text = mnem.format(d=_d(ops[0]), r=f"0x{tgt:04X}")
    elif kind == "BR":
        tgt = (pc + length + ((ops[1] ^ 0x80) - 0x80)) & 0xFFFF
        text = mnem.format(b=_bit(ops[0]), r=f"0x{tgt:04X}")
    else:
        text = mnem
    return Insn(pc, length, bytes(buf[pos:pos + length]), text, tgt)


def disasm(buf: bytes, base: int = 0, start: int = 0, count: int | None = None):
    """Yield ``Insn`` linearly from ``start`` (index) until end or ``count``."""
    pos = start
    n = 0
    while pos < len(buf) and (count is None or n < count):
        ins = decode_one(buf, pos, base)
        yield ins
        pos += ins.length
        n += 1
