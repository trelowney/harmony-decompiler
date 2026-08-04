#pragma description Harmony remote configuration blob (arch 8 and arch 9)
#pragma endian little
#pragma pattern_limit 500000

// ImHex pattern for a Harmony config blob, i.e. the binary part of an .EZHex
// with the XML header and its trailing \r\n removed. tools/split_ezhex.py
// produces one, and samples/harmony525/config.bin is already in that form.
//
// Everything here is verified against real data by round-tripping a config
// through tools/hconfig.py: decompile to JSON, compile back, compare bytes. A
// structure that survives that is described correctly, not merely plausibly.
// Where something is a guess it says so.
//
// Two things to know before reading any of it.
//
// Addresses are ABSOLUTE FLASH ADDRESSES, not file offsets. config_base is
// 0x20000 on both architectures seen so far, so file_offset = address -
// 0x20000. And they are 24 bit, three bytes little-endian, which is why 0x02
// is the single most common byte in a config: it is the high byte of every
// one of them.
//
// The structures below cannot be found by scanning for them from the top of
// the file. They are reached by following pointers, and several of them are
// distinguished only by where a pointer lands rather than by anything in the
// bytes. That is why this file is types only, and why the placements live in
// a generated companion. tools/export_hexpat.py runs the decompiler over a
// config and emits every region it found as a literal placement using these
// types, which gives you the whole file annotated instead of just its header:
//
//     python tools/export_hexpat.py samples/harmony525/config.bin
//
// Documentation of each structure, with the evidence for it, is in
// docs/FORMAT.md. Section numbers in the comments below refer to it.

#define CONFIG_BASE 0x20000

// A 24-bit absolute flash address. Subtract CONFIG_BASE for a file offset.
using Addr24 = u24;

// -------------------------------------------------------------------------
// Blob header - FORMAT.md section 2
//
// arch 9:  magic AHCM, marker CMAH, end marker MCHA, 18 sections
// arch 8:  magic TPTP, marker WLWL, end marker DKDK, 20 sections
//
// Arch 9 mirrors its magic to make its markers and arch 8 does not; WLWL has
// nothing to do with TPTP. Do not infer one from the other.
//
// A null in the section table is not padding and not the end of the table. It
// means that subsystem is absent, and arch 8 has one in the middle. Reading
// until the first zero silently truncates a 20-entry table to 8.

struct BlobHeader<auto sections, auto padding_len> {
    char  magic[4];
    u32   end_address;        // absolute address of the end marker
    u32   unknown_08;         // 0x1400 on the 525, meaning unknown
    u32   section_address[sections];
    u8    tail[padding_len];      // zero padding; `padding` is a reserved word
    char  marker[4];
};

// -------------------------------------------------------------------------
// Name table - FORMAT.md section 4
//
// Section 0. Named symbols belonging to a rule engine the config calls
// "HarmonyAssistant". Wrapped in 0xFEED ... 0xBEEF, which is not a
// coincidence.
//
// `index` is the runtime address of a state variable that can be read live
// over USB while the remote is plugged in, and the numeric suffix in a name
// is that variable's number of possible values: Power_2 has two states,
// Input_9 has nine. Measured on eight variables, eight in range, no
// exceptions. See FORMAT.md section 5c.

struct NameRecord {
    u8    tag;                // 0xA7
    u16   length;             // parent + index + name; the record is length + 3
    u16   parent_id;          // `parent` is a reserved word
    u16   index;              // live state variable address
    char  name[length - 4];
};

struct NameTable<auto count> {
    u16         magic;        // 0xFEED
    u16         declared;     // terminator offset, from the start of the table
    u8          unknown_04;
    NameRecord  records[count];
    u16         terminator;   // 0xBEEF
};

// -------------------------------------------------------------------------
// Pointer table - FORMAT.md section 4b
//
// A count, sometimes some header bytes, then 24-bit addresses. Three shapes
// are known and they differ only in how long the head is, so one struct
// covers all of them:
//
//     <u8 count>  <u24 address>[count]                 head = 1
//     <u16 count> <u24 address>[count]                 head = 2
//     <u16 count> <00> <u24 address>[count]            head = 3   (section 6)
//
// Section 6 is the index to the record array. Section 10 is 487 addresses and
// nothing else, filling all 1,463 of its bytes.
//
// These are the structures that gate any change of length. A section whose
// pointers are decoded can leave everything else opaque and still survive an
// edit; a section with an undecoded pointer in it cannot, and will not say so.

struct PointerTable<auto head, auto count> {
    u8      header[head];     // count, plus any padding between it and the array
    Addr24  target[count];
};

// -------------------------------------------------------------------------
// Key tables - FORMAT.md sections 4e and 5e
//
// A key code, a 16-bit target, and a 0x7F terminator per entry. Five of them
// in the 525 config, in two shapes; the wider one carries a leading flag byte
// that reads 01 on every entry seen so far.
//
// Key codes are keyboard matrix addresses: code = 0x80 | (row << 3) | column,
// eight rows by seven columns on the 525, 50 occupied cells, no collisions.
// Codes outside that form - 0x01, 0x02, 0x05, 0x06, 0x17 - are not physical
// keys. glenharris identified them as system virtual events.
//
// The target is the open question. It is a 16-bit action command, per the
// original developer's description of how these were compiled, with the bit
// layout depending on which subsystem the action belongs to. Splitting it
// into opcode and operand is the thing standing between this repository and a
// config a person can meaningfully edit.
//
// The table at record 0 reads as a canonical ordering: targets 0..46 in
// sequence with four exceptions. The three small tables near 0xE4D8 are
// per-menu binding tables, each covering a different subset of keys with
// different targets. The 51-entry table at 0x1BB9 has every target set to 79,
// which looks like a default or a catch-all.

struct KeyEntry4 {
    u8   code;
    u16  target;
    u8   terminator;          // 0x7F
};

struct KeyEntry5 {
    u8   flag;                // 01 on every entry found so far
    u8   code;
    u16  target;
    u8   terminator;          // 0x7F
};

struct KeyTable4<auto count> { KeyEntry4 entry[count]; };
struct KeyTable5<auto count> { KeyEntry5 entry[count]; };

// -------------------------------------------------------------------------
// The record array - FORMAT.md sections 4d, 4f, 4g
//
// The region below the first section is not covered by the section table and
// is not unstructured. It is an array of 114 records indexed by section 6.
//
// The count in the header looked like a literal 01 00 for a long time because
// 108 of the 114 records carry exactly one address. Six carry more.

struct RecordHeader<auto n> {
    u8      zero;             // 0x00
    Addr24  back_reference;   // usually the previous record's start + 9
    u16     count;
    Addr24  target[n];
};

// Twelve bytes, one block per row of the keyboard matrix. The tail was
// documented as a fixed constant 60 08 8B 2F 03 for a while; it is not, the
// last three bytes are an address, and it only looked fixed because the
// records read by hand happened to share a value. All 1072 of them in the 525
// config point into section 17, which is where the LCD bitmaps live.
//
// What sits between a block header and its 0x17 terminator is the largest
// undecoded thing left in the format.

struct BlockHeader {
    u8      opcode;           // 0x16
    u8      row;              // matrix row
    u8      three;            // 0x03
    u8      zero_1;           // 0x00
    u8      row_times_8;      // row << 3
    u8      zero_2;           // 0x00
    u8      row_times_8_again;
    u8      sixty;            // 0x60
    u8      eight;            // 0x08
    Addr24  screen;           // into section 17
};

// The same 0x16 followed by a real address rather than by <row> 03 00. There
// is no ambiguity: a block header's next three bytes read as roughly 0x0300,
// which is never a valid config address.
//
// 124 of these in the 525 config, against roughly one expected by chance, and
// they arrive in pairs pointing at the same target. They matter out of all
// proportion to their number, because they were sitting inside regions being
// copied as opaque hex, and a length-changing edit would have left every one
// of them aimed at whatever had moved into its place with nothing to report
// it. Assume more are still hidden in the sections that remain opaque.

struct Reference {
    u8      opcode;           // 0x16
    Addr24  target;
};

// 113 of the 114 records end this way. The first address points into section
// 8, the suspected bytecode, which is the evidence that each record carries
// its own program. The second lands on the record's own start + 11.

struct RecordTrailer {
    u8      zero;             // 0x00
    Addr24  program;          // into section 8
    Addr24  self_reference;
};

// -------------------------------------------------------------------------
// Action list - FORMAT.md section 4i
//
// Section 10 is an array of addresses and nothing else, and every one of them
// lands on one of these. The array tiles: 482 of the 486 consecutive pairs in
// the 525 config sit exactly 1 + 3 * count apart, none of the rest overlap,
// and the last list ends on the byte where the index itself begins.
//
// A key table's `target` is an index into that array, so a key binding is
// "run action list N". The instruction is the same three bytes section 8 is
// built from, and the opcode vocabulary is shared between arch 8 and arch 9.
// What any opcode does is not known. Four of them - 0x7C, 0x7D, 0x7E, 0x7F -
// carry three quarters of the instructions in an arch 9 config, so they will
// be the common operations.

struct Instruction {
    u16  operand;
    u8   opcode;
};

struct ActionList<auto n> {
    u8           count;
    Instruction  instruction[n];
};

// -------------------------------------------------------------------------
// LCD bitmap - FORMAT.md section 4h
//
// Section 17 is four of these back to back. 96 x 64 is exactly the 525's
// screen per the manual, and 96 * 64 / 8 is exactly the 768 bytes that
// follow. Rendering them produces axis-aligned rules and a vertical divider
// rather than noise. tools/show_bitmaps.py prints them so this can be checked
// by eye.
//
// Every block header points at one of these, so a block is, among other
// things, choosing which screen layout to draw.

struct Bitmap {
    u8   format;              // 0x02
    u16  width;
    u16  height;
    u8   pixels[width * height / 8];   // 1 bit per pixel, row-major
};

// -------------------------------------------------------------------------
// Everything not understood yet. Kept as an explicit region rather than left
// as a gap, because the model the decompiler works to is that regions tile
// the blob completely, with no gaps and no overlaps. A gap is a place where
// something could be hiding unnoticed.

struct Opaque<auto size> {
    u8 data[size];
};

// === END OF TYPES ===
//
// Nothing is placed below this line in the shared file. Run
// tools/export_hexpat.py to get a copy of everything above plus a placement
// for every region of a specific config.
