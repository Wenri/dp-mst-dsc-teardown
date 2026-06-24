# SPDX-License-Identifier: WTFPL
"""Recursive-descent follower over 8051 code: turn linear disasm into a reachable
code map + function list, so two firmware builds can be compared by structure
rather than by raw bytes (their code is not address-aligned between builds).

Follows LJMP/AJMP/SJMP, conditional jumps, and L/ACALL (call targets become
functions). Stops at RET/RETI and records JMP @A+DPTR as an unresolved jump
table. Code is the 8051 image with code address 0 at ``code[0]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import i8051

# interrupt vector table (8051): reset + the standard sources
VECTORS = (0x00, 0x03, 0x0B, 0x13, 0x1B, 0x23, 0x2B, 0x33, 0x3B, 0x43, 0x4B, 0x53)
_UNCOND = {"LJMP", "AJMP", "SJMP"}
_CALL = {"LCALL", "ACALL"}
_STOP = {"RET", "RETI"}


def entry_points(code: bytes) -> list[int]:
    """Resolve the LJMP/AJMP targets sitting in the interrupt vector table."""
    eps = []
    for v in VECTORS:
        if v + 2 >= len(code):
            break
        ins = i8051.decode_one(code, v, 0)
        if ins.text.split()[0] in _UNCOND and ins.target is not None:
            eps.append(ins.target)
    return eps


@dataclass
class Trace:
    insns: dict           # addr -> Insn
    funcs: set            # call-target addresses (function entries)
    indirect: list        # addresses of JMP @A+DPTR (jump tables)
    entries: list

    @property
    def reachable_bytes(self) -> int:
        return sum(i.length for i in self.insns.values())


def follow(code: bytes, extra_entries=()) -> Trace:
    eps = entry_points(code) + list(extra_entries)
    insns: dict = {}
    funcs: set = set()
    indirect: list = []
    work = list(eps)
    while work:
        a = work.pop()
        while a is not None and 0 <= a < len(code) and a not in insns:
            ins = i8051.decode_one(code, a, 0)
            insns[a] = ins
            mn = ins.text.split()[0]
            nxt = a + ins.length
            if mn in _STOP:
                a = None
            elif mn in _UNCOND:
                a = ins.target if ins.target is not None else None
            elif mn in _CALL:
                if ins.target is not None:
                    funcs.add(ins.target)
                    work.append(ins.target)
                a = nxt
            elif mn == "JMP":              # JMP @A+DPTR -> jump table, unresolved
                indirect.append(a)
                a = None
            elif ins.target is not None:   # conditional branch: take both edges
                work.append(ins.target)
                a = nxt
            else:
                a = nxt
    return Trace(insns, funcs, indirect, eps)


def fn_fingerprint(code: bytes, entry: int, maxins: int = 96) -> tuple:
    """Opcode-only signature of the routine at ``entry`` (operands/addresses
    dropped), so the same logic matches across builds despite relocation."""
    ops = []
    a = entry
    seen = set()
    while 0 <= a < len(code) and a not in seen and len(ops) < maxins:
        seen.add(a)
        op = code[a]
        ops.append(op)
        ins = i8051.decode_one(code, a, 0)
        mn = ins.text.split()[0]
        if mn in _STOP:
            break
        if mn in _UNCOND:
            if ins.target is not None and ins.target < len(code):
                a = ins.target
                continue
            break
        a += ins.length
    return tuple(ops)


def fingerprints(code: bytes, tr: Trace) -> set:
    """The set of function fingerprints for a build (entries + call targets)."""
    return {fn_fingerprint(code, e) for e in (set(tr.entries) | tr.funcs)}
