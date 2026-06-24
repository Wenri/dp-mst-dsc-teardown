# SPDX-License-Identifier: WTFPL
"""Recursive-descent follower over 8051 code: turn linear disasm into a reachable
code map + function list, so two firmware builds can be compared by structure
rather than by raw bytes (their code is not address-aligned between builds).

Follows LJMP/AJMP/SJMP, conditional jumps, and L/ACALL (call targets become
functions). Stops at RET/RETI and records JMP @A+DPTR as an unresolved jump
table. Code is the 8051 image with code address 0 at ``code[0]``.
"""
from __future__ import annotations

import difflib
from collections import defaultdict
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


# --- function-level diff between two builds -------------------------------- #
# Normalized instruction tokens drop code addresses and collapse jump/call
# encodings (AJMP/LJMP/SJMP -> JMP, A/LCALL -> CALL) so a routine matches across
# builds despite relocation, but KEEP operands (immediates, SFR/RAM directs) so
# constant/register changes still show as differences.

def _norm_token(ins) -> str:
    mn = ins.text.split()[0]
    if mn in _UNCOND:
        return "JMP"
    if mn in _CALL:
        return "CALL"
    if ins.target is not None:                  # conditional branch: drop target
        return (ins.text.rsplit(",", 1)[0] + ",@") if "," in ins.text else mn
    return ins.text


def function_bodies(code: bytes, cap: int = 256) -> dict:
    """{entry: (norm_tokens, opcodes)} for each function, entry..first-RET."""
    tr = follow(code)
    ents = sorted(set(tr.entries) | tr.funcs)
    out: dict = {}
    for i, e in enumerate(ents):
        end = min(ents[i + 1] if i + 1 < len(ents) else len(code), e + cap)
        toks: list = []
        ops: list = []
        a = e
        while a < end:
            ins = i8051.decode_one(code, a, 0)
            ops.append(code[a])
            toks.append(_norm_token(ins))
            if ins.text.split()[0] in _STOP:
                break
            a += ins.length
        while toks and toks[-1] == "NOP":       # trim trailing padding
            toks.pop()
            ops.pop()
        if toks:
            out[e] = (tuple(toks), tuple(ops))
    return out


def diff_functions(code_a: bytes, code_b: bytes, thresh: float = 0.62) -> dict:
    """Classify A's functions vs B: identical / operand-only / structural /
    added (A-only) / removed (B-only). identical = same normalized tokens
    (logic + operands, only relocated). operand-only = fuzzy match with equal
    opcode sequence (constants/addresses changed). structural = fuzzy match,
    opcodes differ (real logic change)."""
    A = function_bodies(code_a)
    B = function_bodies(code_b)
    bx = defaultdict(list)
    for e, (t, _o) in B.items():
        bx[t].append(e)
    identical: list = []
    used_b: set = set()
    rem_a = dict(A)
    for e, (t, _o) in list(A.items()):
        cand = [b for b in bx.get(t, ()) if b not in used_b]
        if cand:
            identical.append((e, cand[0]))
            used_b.add(cand[0])
            del rem_a[e]
    rem_b = [(e, v) for e, v in B.items() if e not in used_b]
    operand_only: list = []
    structural: list = []
    added: list = []
    for e, (t, o) in rem_a.items():
        best = None
        br = 0.0
        for be, (bt, _bo) in rem_b:
            r = difflib.SequenceMatcher(None, t, bt).quick_ratio()
            if r > br:
                br, best = r, be
        if best is not None:
            bt, bo = dict(rem_b)[best]
            real = difflib.SequenceMatcher(None, t, bt).ratio()
            if real >= thresh:
                (operand_only if o == bo else structural).append((e, best, real))
                rem_b = [x for x in rem_b if x[0] != best]
                continue
        added.append(e)
    removed = [e for e, _ in rem_b]
    return dict(identical=identical, operand_only=operand_only,
                structural=structural, added=added, removed=removed, A=A, B=B)


def format_fndiff(code_a: bytes, code_b: bytes, name_a="A", name_b="B") -> str:
    d = diff_functions(code_a, code_b)
    A, B = d["A"], d["B"]
    real_added = [e for e in d["added"] if len(A[e][0]) > 2]
    L = [f"function-level diff: {name_a} vs {name_b}  ({len(A)} vs {len(B)} funcs)",
         f"  identical (relocated only) : {len(d['identical'])}",
         f"  operand-only (config/map)  : {len(d['operand_only'])}",
         f"  structural (logic change)  : {len(d['structural'])}",
         f"  {name_a}-only added         : {len(real_added)} "
         f"(+{len(d['added']) - len(real_added)} stubs)",
         f"  {name_b}-only removed       : {len(d['removed'])}"]
    for e, be, r in sorted(d["structural"], key=lambda x: -x[2])[:3]:
        L.append(f"\n  STRUCTURAL  {name_a} 0x{e:04x} ~ {name_b} 0x{be:04x} (ratio {r:.2f}):")
        for ln in difflib.unified_diff(list(B[be][0]), list(A[e][0]),
                                       lineterm="", n=1, fromfile=name_b, tofile=name_a):
            if ln[:3] not in ("---", "+++", "@@ "):
                L.append("    " + ln)
    for e in real_added[:2]:
        L.append(f"\n  ADDED ({name_a}-only) 0x{e:04x}:")
        L.extend("    " + t for t in A[e][0][:18])
    return "\n".join(L)
