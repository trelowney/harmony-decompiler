"""A PIC18 disassembler, and a search for the thing that runs a config.

    python tools/pic18dis.py firmware.EZUp                 # a listing
    python tools/pic18dis.py firmware.EZUp --analyse       # what is where
    python tools/pic18dis.py firmware.EZUp --at 0x1234     # one region

The remote is a Microchip PIC, and its firmware can be pulled off any working
unit with `concordance --dump-firmware`. That matters because the firmware
contains the routine that interprets a config, and therefore the dispatch on
the instruction opcodes in docs/FORMAT.md section 4i. Those opcodes are the
largest thing standing between this repository and an editor a person can use,
and a disassembly settles what they are rather than supporting a guess.

No firmware is included here. It is Logitech's code, not ours to redistribute,
and anyone with a remote can dump their own in about ten seconds.

The container is the same idea as an .EZHex: XML with the payload as hex inside
<DATA> elements. A bare binary works too.

On the instruction set: PIC18 is 16-bit words, little-endian in the file, with
four two-word instructions (CALL, GOTO, MOVFF, LFSR). The encoding below is
from the standard instruction set summary. Nothing here is Harmony specific,
so if it is useful somewhere else, take it.
"""
import bisect
import json
import re
import sys
from pathlib import Path

import _paths  # noqa: F401


def load_firmware(path: Path) -> bytes:
    """Bytes of program memory, from an .EZUp or a bare binary."""
    raw = path.read_bytes()
    if raw[:1] != b"<":
        return raw
    text = raw.decode("ascii", "replace")
    return bytes.fromhex("".join(re.findall(r"<DATA>([0-9A-Fa-f]+)</DATA>", text)))


# --------------------------------------------------------------------------
# instruction set

# Byte-oriented file register operations: oooo ooda ffff ffff
BYTE_OPS = {
    0b001001: "ADDWF", 0b001000: "ADDWFC", 0b000101: "ANDWF",
    0b000111: "COMF", 0b000001: "DECF", 0b001011: "DECFSZ",
    0b010011: "DCFSNZ", 0b001010: "INCF", 0b001111: "INCFSZ",
    0b010010: "INFSNZ", 0b000100: "IORWF", 0b010100: "MOVF",
    0b001101: "RLCF", 0b010001: "RLNCF", 0b001100: "RRCF",
    0b010000: "RRNCF", 0b010101: "SUBFWB", 0b010111: "SUBWF",
    0b010110: "SUBWFB", 0b001110: "SWAPF", 0b000110: "XORWF",
}
# same shape but no destination bit: oooo ooo a ffff ffff
FILE_OPS = {
    0b0110101: "CLRF", 0b0110001: "CPFSEQ", 0b0110010: "CPFSGT",
    0b0110000: "CPFSLT", 0b0110111: "MOVWF", 0b0000001: "MULWF",
    0b0110110: "NEGF", 0b0110100: "SETF", 0b0110011: "TSTFSZ",
}
BIT_OPS = {0b1001: "BCF", 0b1000: "BSF", 0b1011: "BTFSC",
           0b1010: "BTFSS", 0b0111: "BTG"}
LITERAL_OPS = {0x0F: "ADDLW", 0x0B: "ANDLW", 0x09: "IORLW", 0x0E: "MOVLW",
               0x0D: "MULLW", 0x0C: "RETLW", 0x08: "SUBLW", 0x0A: "XORLW"}
COND_BRANCH = {0xE2: "BC", 0xE6: "BN", 0xE3: "BNC", 0xE7: "BNN",
               0xE5: "BNOV", 0xE1: "BNZ", 0xE4: "BOV", 0xE0: "BZ"}
SIMPLE = {
    0x0000: "NOP", 0x0003: "SLEEP", 0x0004: "CLRWDT", 0x0005: "PUSH",
    0x0006: "POP", 0x0007: "DAW", 0x0008: "TBLRD*", 0x0009: "TBLRD*+",
    0x000A: "TBLRD*-", 0x000B: "TBLRD+*", 0x000C: "TBLWT*", 0x000D: "TBLWT*+",
    0x000E: "TBLWT*-", 0x000F: "TBLWT+*", 0x0010: "RETFIE", 0x0011: "RETFIE 1",
    0x0012: "RETURN", 0x0013: "RETURN 1", 0x00FF: "RESET",
}
# Special function registers worth naming in a listing. Arch 9 is a
# PIC18LF4550 per libconcord/remote_info.h, and these are its addresses.
SFR = {0xF80: "PORTA", 0xF81: "PORTB", 0xF82: "PORTC", 0xF83: "PORTD",
       0xF84: "PORTE", 0xF89: "LATA", 0xF8A: "LATB", 0xF8B: "LATC",
       0xF8C: "LATD", 0xF8D: "LATE", 0xF92: "TRISA", 0xF93: "TRISB",
       0xF94: "TRISC", 0xF95: "TRISD", 0xF96: "TRISE",
       0xFC9: "SSPBUF", 0xFC6: "SSPCON1", 0xFC7: "SSPSTAT",
       0xFBD: "CCP1CON", 0xFBE: "CCPR1L", 0xFBF: "CCPR1H",
       0xFCA: "T2CON", 0xFCB: "PR2", 0xFD0: "RCON", 0xFD6: "TMR0L",
       0xFF2: "INTCON", 0xFF3: "PRODL", 0xFF4: "PRODH", 0xFF5: "TABLAT",
       0xFF6: "TBLPTRL", 0xFF7: "TBLPTRH", 0xFF8: "TBLPTRU", 0xFF9: "PCL",
       0xFFA: "PCLATH", 0xFFB: "PCLATU", 0xFFC: "STKPTR", 0xFFD: "TOSL",
       0xFFE: "TOSH", 0xFFF: "TOSU", 0xFE8: "WREG", 0xFE9: "FSR0L",
       0xFEA: "FSR0H", 0xFEF: "INDF0", 0xFE1: "FSR1L", 0xFE2: "FSR1H",
       0xFE7: "INDF1", 0xFD9: "FSR2L", 0xFDA: "FSR2H", 0xFDF: "INDF2",
       0xFD8: "STATUS"}


def _f(addr, a):
    """Render a file register operand."""
    if not a and addr >= 0x60:            # access bank, upper half is the SFRs
        full = 0xF00 | addr
        return SFR.get(full, f"0x{full:03X}")
    return f"0x{addr:02X}" + ("" if a else ", ACCESS")


def decode(words, i):
    """Decode one instruction. Returns (text, size in words, target or None)."""
    w = words[i]
    nxt = words[i + 1] if i + 1 < len(words) else 0
    hi = w >> 8

    if w in SIMPLE:
        return SIMPLE[w], 1, None
    if hi == 0x01:
        return f"MOVLB 0x{w & 0x0F:X}", 1, None
    if hi in LITERAL_OPS:
        return f"{LITERAL_OPS[hi]} 0x{w & 0xFF:02X}", 1, None

    # two-word instructions
    if 0xEC <= hi <= 0xED and (nxt >> 12) == 0xF:
        target = ((nxt & 0x0FFF) << 8 | (w & 0xFF)) * 2
        s = ", FAST" if hi & 1 else ""
        return f"CALL 0x{target:05X}{s}", 2, target
    if hi == 0xEF and (nxt >> 12) == 0xF:
        target = ((nxt & 0x0FFF) << 8 | (w & 0xFF)) * 2
        return f"GOTO 0x{target:05X}", 2, target
    if (w >> 12) == 0xC and (nxt >> 12) == 0xF:
        return f"MOVFF 0x{w & 0xFFF:03X}, 0x{nxt & 0xFFF:03X}", 2, None
    if hi == 0xEE and (nxt >> 8) == 0xF0:
        return f"LFSR {(w >> 4) & 3}, 0x{(w & 0x0F) << 8 | (nxt & 0xFF):03X}", 2, None

    # relative branches
    if hi in COND_BRANCH:
        off = w & 0xFF
        off -= 0x100 if off & 0x80 else 0
        return f"{COND_BRANCH[hi]} 0x{2 * (i + 1 + off):05X}", 1, None
    if (w >> 11) == 0b11010:
        off = w & 0x7FF
        off -= 0x800 if off & 0x400 else 0
        return f"BRA 0x{2 * (i + 1 + off):05X}", 1, None
    if (w >> 11) == 0b11011:
        off = w & 0x7FF
        off -= 0x800 if off & 0x400 else 0
        return f"RCALL 0x{2 * (i + 1 + off):05X}", 1, 2 * (i + 1 + off)

    # register operations
    op7, op6 = w >> 9, w >> 10
    if op7 in FILE_OPS:
        return f"{FILE_OPS[op7]} {_f(w & 0xFF, (w >> 8) & 1)}", 1, None
    if op6 in BYTE_OPS:
        d = "W" if not (w >> 9) & 1 else "F"
        return (f"{BYTE_OPS[op6]} {_f(w & 0xFF, (w >> 8) & 1)}, {d}", 1, None)
    if (w >> 12) in BIT_OPS:
        return (f"{BIT_OPS[w >> 12]} {_f(w & 0xFF, (w >> 8) & 1)}, "
                f"{(w >> 9) & 7}", 1, None)

    return f"DW 0x{w:04X}", 1, None


def disassemble(data: bytes, start=0, end=None):
    """Yield (byte address, words, text, call/goto target)."""
    words = [int.from_bytes(data[i:i + 2], "little")
             for i in range(0, len(data) - 1, 2)]
    i = start // 2
    stop = len(words) if end is None else min(len(words), end // 2)
    while i < stop:
        text, size, target = decode(words, i)
        yield 2 * i, words[i:i + size], text, target
        i += size


# --------------------------------------------------------------------------
# analysis

def analyse(data: bytes):
    """Where the code is, what calls what, and where a config gets interpreted."""
    words = [int.from_bytes(data[i:i + 2], "little")
             for i in range(0, len(data) - 1, 2)]
    blank = sum(1 for w in words if w == 0xFFFF)
    print(f"{len(data)} bytes, {len(words)} words, "
          f"{blank} erased ({100 * blank / len(words):.1f}%)")

    calls, gotos, literals, returns = {}, {}, {}, 0
    branch_targets = []
    for addr, ws, text, target in disassemble(data):
        if text.startswith("CALL") or text.startswith("RCALL"):
            calls[target] = calls.get(target, 0) + 1
        elif text.startswith("GOTO"):
            gotos[target] = gotos.get(target, 0) + 1
            branch_targets.append(addr)
        elif text.startswith(("MOVLW", "SUBLW", "XORLW", "ADDLW", "RETLW")):
            k = int(text.split("0x")[1], 16)
            literals.setdefault(k, []).append((addr, text.split()[0]))
        elif text.startswith("RETURN") or text.startswith("RETFIE"):
            returns += 1

    print(f"\n{len(calls)} distinct call targets, {sum(calls.values())} calls; "
          f"{returns} returns")
    print("most called routines:")
    for t, n in sorted(calls.items(), key=lambda kv: -kv[1])[:12]:
        print(f"    0x{t:05X}  called {n} times")

    # A dispatch on a byte usually compiles to a run of GOTOs reached by
    # adding to PCL, so consecutive GOTOs are worth finding.
    runs, run = [], []
    for addr in branch_targets:
        if run and addr == run[-1] + 4:
            run.append(addr)
        else:
            if len(run) >= 6:
                runs.append(run)
            run = [addr]
    if len(run) >= 6:
        runs.append(run)
    print(f"\n{len(runs)} jump tables (six or more consecutive GOTOs):")
    for r in runs[:14]:
        print(f"    0x{r[0]:05X} .. 0x{r[-1]:05X}   {len(r)} entries")

    # And the direct question: does the firmware compare against the opcodes
    # a config actually uses?
    config_opcodes = [0x07, 0x0F, 0x1F, 0x3F, 0x71, 0x72, 0x73, 0x75,
                      0x77, 0x7C, 0x7D, 0x7E, 0x7F, 0x81, 0x82, 0x83,
                      0x90, 0x91, 0x92, 0x93, 0x94]
    print("\nliterals matching an action list opcode:")
    for k in config_opcodes:
        hits = literals.get(k, [])
        if hits:
            where = " ".join(f"0x{a:05X}({m})" for a, m in hits[:6])
            print(f"    0x{k:02X}  {len(hits):>3}  {where}"
                  + ("  ..." if len(hits) > 6 else ""))
    return calls, runs


# Every opcode seen in an action list across the samples in this repository,
# from tools/actions.py. Used to work out which comparison chain in the
# firmware is the interpreter's dispatch rather than something else.
OBSERVED_OPCODES = frozenset({
    0x00, 0x07, 0x0F, 0x1F, 0x3F, 0x70, 0x71, 0x72, 0x73, 0x75, 0x77, 0x78,
    0x79, 0x7A, 0x7C, 0x7D, 0x7E, 0x7F, 0x81, 0x82, 0x83, 0x85, 0x86, 0x8E,
    0x8F, 0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A,
    0x9B, 0x9C, 0x9D, 0x9E, 0x9F,
})


def find_dispatch(data: bytes):
    """The interpreter's opcode dispatch, and the handler for each opcode.

    The compiler emitted the same three instructions for every case:

        MOVLW <opcode>        0E kk
        SUBWF <opcode var>, W 5D dd
        BNC   <next case>     E3 nn

    so the whole decision tree can be read off mechanically. Which register
    holds the opcode is not assumed - it is whichever one the chain compares
    against most often.

    The handler is the code between a test and the branch that leaves it, and
    what it calls is the interesting part: the routine an opcode invokes is
    the meaning of that opcode.
    """
    words = [int.from_bytes(data[i:i + 2], "little")
             for i in range(0, len(data) - 1, 2)]

    tests = []
    for i in range(len(words) - 2):
        if (words[i] >> 8) != 0x0E:                       # MOVLW k
            continue
        if (words[i + 1] >> 8) & 0xFE != 0x5C:            # SUBWF f, W
            continue
        if (words[i + 2] >> 8) not in (0xE2, 0xE3):       # BC / BNC
            continue
        tests.append((2 * i, words[i] & 0xFF, words[i + 1] & 0xFF))

    if not tests:
        print("no dispatch chain found")
        return []

    chains = {}
    for a, k, reg in tests:
        chains.setdefault(reg, []).append((a, k))

    print(f"{len(tests)} comparisons of this shape, against "
          f"{len(chains)} different registers:")
    for reg, c in sorted(chains.items(), key=lambda kv: -len(kv[1])):
        if len(c) < 4:
            continue
        print(f"    0x{reg:02X}  {len(c):>3} tests, values 0x{min(k for _, k in c):02X}"
              f" to 0x{max(k for _, k in c):02X}, "
              f"code 0x{min(a for a, _ in c):05X} to 0x{max(a for a, _ in c):05X}")

    # Which chain is the opcode dispatch is decided by the configs rather than
    # by looking: the right register is the one whose compared values overlap
    # the opcodes that actually occur in an action list. The others turn out to
    # be second-level tests on an operand byte or on some state.
    reg = max(chains, key=lambda r: (
        len({k for _, k in chains[r]} & OBSERVED_OPCODES), len(chains[r])))
    hit = {k for _, k in chains[reg]} & OBSERVED_OPCODES
    print(f"\n{len(hit)} of the values compared against 0x{reg:02X} are opcodes "
          f"that occur in a real config, which is more than any other chain")
    chain = chains[reg]
    print(f"\nreading 0x{reg:02X} as the opcode: {len(chain)} cases, "
          f"0x{min(a for a, _ in chain):05X} to 0x{max(a for a, _ in chain):05X}\n")

    starts = {a for a, _ in chain}
    print(f"{'opcode':<8}{'test at':<10}{'handler':<10}calls")
    out = []
    for addr, k in sorted(chain, key=lambda t: t[1]):
        handler = addr + 6
        calls, i = [], handler
        while i < len(data) - 1 and i - handler < 0x60:
            if i in starts:
                break
            text, size, target = decode(words, i // 2)
            if text.startswith(("CALL", "RCALL")):
                calls.append(target)
            if text.startswith(("BRA", "GOTO", "RETURN", "RETFIE")):
                break
            i += 2 * size
        out.append((k, addr, handler, calls))
        shown = " ".join(f"0x{c:05X}" for c in calls) or "-"
        print(f"0x{k:02X}    0x{addr:05X}   0x{handler:05X}   {shown}")

    routines = {}
    for k, _, _, calls in out:
        for c in calls:
            routines.setdefault(c, []).append(k)
    shared = {c: ks for c, ks in routines.items() if len(ks) > 1}
    if shared:
        print("\nroutines shared by more than one opcode:")
        for c, ks in sorted(shared.items(), key=lambda kv: -len(kv[1])):
            print(f"    0x{c:05X}  " + ", ".join(f"0x{k:02X}" for k in sorted(ks)))
    return out


# The routines that walk a config, named in docs/FORMAT.md section 4j.
SEEK_SECTION = 0x066A8      # seek to section [0x158]
STEP_INDEX = 0x0672C        # advance by [0x15E:0x15F] * 3 + [0x15D]


def find_sections(data: bytes, handlers=None):
    """Which config section does each opcode's handler reach into?

    `0x066A8` is called with a section number in `0x158`, and `0x0672C` then
    indexes that section's pointer array by `operand * 3 + [0x15D]`. Both are
    loaded with `MOVLW`, so following the handler and what it calls says which
    section an opcode uses and, when the pair is visible, how wide that
    section's header is.

    Two things this has to get right, both learned by getting them wrong.

    A linear walk has to stop at an unconditional transfer. Opcode handlers sit
    end to end and each finishes with a `BRA` back to the interpreter loop, so a
    walk that only stops at `RETURN` runs out of one handler and into the next,
    and every opcode appears to use every section.

    And it must not walk into shared utilities. Following calls to a routine
    with many callers reaches most of the firmware within three steps, which
    produces a full table that means nothing. What a handler calls directly is
    always followed; below that, only routines with few callers are.
    """
    listing = list(disassemble(data))
    order = [a for a, _, _, _ in listing]
    info = {a: (t, tgt) for a, _, t, tgt in listing}
    pos = {a: i for i, a in enumerate(order)}
    callers = {}
    for a, _, t, tgt in listing:
        if t.startswith(("CALL", "RCALL")) and tgt is not None:
            callers[tgt] = callers.get(tgt, 0) + 1

    def block(start, limit=0x200):
        calls, seeks = [], []
        i = pos.get(start)
        if i is None:
            return calls, seeks
        literal = section = addend = None
        while i < len(order) and order[i] - start < limit:
            text, tgt = info[order[i]]
            if text.startswith("MOVLW"):
                literal = int(text.split("0x")[1], 16)
            elif text.startswith("MOVWF 0x58"):
                section = literal
            elif text.startswith("MOVWF 0x5D"):
                addend = literal
            elif text.startswith(("CALL", "RCALL")):
                if tgt == SEEK_SECTION:
                    seeks.append([section, None])
                elif tgt == STEP_INDEX and seeks and seeks[-1][1] is None:
                    seeks[-1][1] = addend
                elif tgt is not None:
                    calls.append(tgt)
            elif text.startswith(("RETURN", "RETFIE", "BRA", "GOTO")):
                break
            i += 1
        return calls, seeks

    def reach(start, depth=4, shared=3):
        seen, out, stack = set(), [], [(start, 0)]
        while stack:
            a, d = stack.pop()
            if a in seen or d > depth:
                continue
            seen.add(a)
            calls, seeks = block(a)
            out += seeks
            for c in calls:
                if d == 0 or callers.get(c, 0) < shared:
                    stack.append((c, d + 1))
        return out

    if handlers is None:
        handlers = {k: a + 6 for k, a, _, _ in find_dispatch(data)}
        print()

    print("opcode   section it reaches (and the offset added when indexing)")
    out = {}
    for op in sorted(handlers):
        pairs = sorted({(s, k) for s, k in reach(handlers[op]) if s is not None})
        out[op] = pairs
        txt = ", ".join(f"{s} (+{k})" if k is not None else f"{s} (offset not seen)"
                        for s, k in pairs)
        print(f"0x{op:02X}     {txt or '-'}")
    return out


def call_graph(data: bytes, out_path):
    """Write the firmware's call graph as a graphify graph.json.

    The reachability questions in find_sections - does this opcode's handler
    reach a seek to that section, and by what route - are graph queries, and
    doing them by hand needs two hand-tuned filters to stop the answer being
    "everything reaches everything". Emitting the graph instead lets a graph
    tool answer them, and show its route:

        graphify path "opcode 0x72" "section 14" --graph firmware-graph.json
        graphify explain "routine 0x066A8" --graph firmware-graph.json

    A block runs from an entry to the first unconditional transfer, and only
    the calls inside it are attributed to that entry. Attributing everything
    between one entry and the next instead produced a wrong edge on the first
    attempt - the handler for opcode 0x07 is a second-level dispatch several
    hundred bytes long, and it swallowed calls belonging to other paths, which
    then showed up as a confident three-hop route to a section it never
    touches.
    """
    listing = list(disassemble(data))
    order = [a for a, _, _, _ in listing]
    info = {a: (t, tgt) for a, _, t, tgt in listing}
    pos = {a: i for i, a in enumerate(order)}
    dispatch = {k: a + 6 for k, a, _, _ in find_dispatch(data)}

    entries = sorted({tgt for _, _, t, tgt in listing
                      if t.startswith(("CALL", "RCALL")) and tgt is not None}
                     | set(dispatch.values()) | {0})

    def block(start, limit=0x400):
        """Calls and section seeks reachable without leaving this block."""
        out_calls, out_seeks = [], []
        i = pos.get(start)
        literal = section = None
        while i is not None and i < len(order) and order[i] - start < limit:
            text, tgt = info[order[i]]
            if text.startswith("MOVLW"):
                literal = int(text.split("0x")[1], 16)
            elif text.startswith("MOVWF 0x58"):
                section = literal
            elif text.startswith(("CALL", "RCALL")):
                if tgt == SEEK_SECTION and section is not None:
                    out_seeks.append((section, order[i]))
                elif tgt is not None:
                    out_calls.append((tgt, order[i]))
            elif text.startswith(("RETURN", "RETFIE", "BRA", "GOTO")):
                break
            i += 1
        return out_calls, out_seeks

    nodes, links = {}, []

    def node(nid, label, kind):
        nodes.setdefault(nid, {"id": nid, "label": label, "file_type": kind,
                               "source_file": "firmware", "_origin": "pic18dis"})
        return nid

    for a in entries:
        node(f"routine_{a:05X}", f"routine 0x{a:05X}", "code")
    for op, h in dispatch.items():
        nid = node(f"opcode_{op:02X}", f"opcode 0x{op:02X}", "code")
        links.append({"source": nid, "target": f"routine_{h:05X}",
                      "relation": "handled_by", "confidence": "EXTRACTED",
                      "source_location": f"0x{h:05X}", "weight": 1.0})

    for entry in entries:
        out_calls, out_seeks = block(entry)
        for tgt, at in out_calls:
            if f"routine_{tgt:05X}" not in nodes:
                continue
            links.append({"source": f"routine_{entry:05X}",
                          "target": f"routine_{tgt:05X}",
                          "relation": "calls", "confidence": "EXTRACTED",
                          "source_location": f"0x{at:05X}", "weight": 1.0})
        # a seek to a config section is the edge that answers something
        for section, at in out_seeks:
            nid = node(f"section_{section}", f"section {section}", "data")
            links.append({"source": f"routine_{entry:05X}", "target": nid,
                          "relation": "seeks", "confidence": "EXTRACTED",
                          "source_location": f"0x{at:05X}", "weight": 1.0})

    graph = {"input_tokens": 0, "output_tokens": 0,
             "nodes": list(nodes.values()), "links": links, "directed": True}
    Path(out_path).write_text(json.dumps(graph, indent=1), encoding="utf-8")
    print(f"\n{out_path}: {len(nodes)} nodes, {len(links)} links")
    return graph


def main(argv):
    args = [a for a in argv[1:]]
    if not args:
        print(__doc__)
        return 2
    path = Path(args[0])
    data = load_firmware(path)
    rest = args[1:]

    if "--analyse" in rest:
        analyse(data)
        return 0
    if "--dispatch" in rest:
        find_dispatch(data)
        return 0
    if "--sections" in rest:
        find_sections(data)
        return 0
    if "--graph" in rest:
        i = rest.index("--graph")
        out = rest[i + 1] if i + 1 < len(rest) else "firmware-graph.json"
        call_graph(data, out)
        return 0

    start, end = 0, None
    if "--at" in rest:
        start = int(rest[rest.index("--at") + 1], 0)
        end = start + 0x100
    for addr, ws, text, _ in disassemble(data, start, end):
        raw = " ".join(f"{w:04X}" for w in ws)
        print(f"{addr:05X}:  {raw:<10} {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
