# X2000 compiler design

The LLVM Project fork owns this target, its scheduler and its tests.
See [the measured inputs](x2000-msa.md) and [build instructions](../README.md).

## Why the stock compiler's MSA code was slow

The 2026-11-17 MSA-everywhere build (`-C target-feature=+msa`, stock nightly)
decoded images 16–45% slower in the original Q2 player comparison.
Three causes, found with `tests/perf/x2000_cost/lowering.py` on the stock
`llc`:

1. **Every vector load or store that is not 16-byte aligned went through the
   stack.** `MipsSETargetLowering::allowsMisalignedMemoryAccesses` returns
   false for vectors before MIPS32r6, so the legalizer copies the 16 bytes
   word by word (`lwl`/`lwr`) into an aligned stack slot and `ld`s that:
   35 instructions for a load and a store, and a store-to-load stall
   (12 cycles measured) each time. A vectorized loop over `[u8]`, `[i16]` or
   `[f32]` only has element alignment, so this was every auto-vectorized
   loop. MSA `ld`/`st` take any address; unaligned costs 1.5 cycles against
   1.0 ([x2000-msa.md](x2000-msa.md)).
2. **The loop vectorizer saw 32-bit vector registers** (the `BasicTTIImpl`
   default): `i32` and `f32` loops were not vectorized at all, and `u8` loops
   used 4 lanes, a `<4 x i8>` that the backend widens and moves through GPRs.
3. **Narrowing was scalarized.** `trunc <16 x i16> to <16 x i8>` became 16
   `copy_s.h` and 16 `insert.b`; `sitofp <4 x i16>` and `fptosi` to `i16`
   moved every lane through a GPR and an FPR. At cost 1 per move the
   vectorizers chose these freely: 36% of that build's MSA instructions were
   GPR/vector moves.

## LLVM and Clang changes

- **CPU and feature** (`Mips.td`, `MipsSubtarget.h`): `x2000`, implying
  MIPS32r5 and MSA. clang accepts `-march=x2000`.
- **Unaligned MSA memory access** (`MipsSEISelLowering.cpp`): legal 128-bit
  vector loads and stores of any alignment are selected as `ld`/`st`.
- **Narrowing with `pckev`** (`MipsSEISelLowering.cpp`, DAG combine):
  `trunc` of whole registers halves the element size with one `pckev` per
  pair of registers and level.
- **Int/float conversions at the float's element size** (same file):
  `sitofp`/`uitofp` from narrower integers extend in vector registers and use
  `ffint_s`; `fptosi` to narrower integers uses `ftrunc_s` and narrows with
  `pckev`.
- **Saturating float-to-integer conversions** (same file): Rust's `as`
  casts from floats are `llvm.fptosi.sat`/`fptoui.sat`, which the stock
  backend expands to about 12 scalar instructions per lane. `ftrunc_s` and
  `ftrunc_u` already saturate and give 0 for a NaN, so a conversion to the
  float's element size is that one instruction, and a narrower result adds
  `max_s`, `min_s` and `pckev`. The backend already maps the
  `mips.ftrunc_*` intrinsics to the same node.
- **Cost model** (`MipsTargetTransformInfo.{h,cpp}`): below.

### Stock lowering that is wrong for generic IR

Found by the identical-output gate: the first X2000 build decoded MP3 and AAC
to different samples than the stock build (`bench_qemu.py`; an arm without
the loop vectorizer matched). The MIPS backend marks three vector operations
legal in the form its own MSA intrinsics need, not the form generic IR means:

| IR | Selected | Result |
| --- | --- | --- |
| `llvm.log2` on a vector | `flog2.w` | the exponent (`logb`), not the logarithm |
| `llvm.exp2` on a vector (also what `pow(2, x)` becomes) | `fexp2.w` with the float as its integer exponent | wrong |
| `llvm.fma(a, b, c)` on a vector | `fmadd.w` as `a + b * c` | wrong; `a * b + c` is meant |

The stock compiler never reached them: with 32-bit vector registers it did
not vectorize `f32` loops. Scalar code calls libm for all three. The X2000
cost model returns an invalid cost for the vector form of these three
intrinsics, so no vectorizer widens them (`libm_log2`, `libm_exp2`,
`libm_fma` in `tests/perf/x2000_cost/sat.ll` check it). Vector IR written by
hand (`std::simd`) would still get the wrong instructions; the player has
none. A backend fix needs separate nodes for the intrinsics and is not in
this patch.

### Cost model

Reciprocal-throughput costs in units of 0.5 cycles (a scalar `addu` is 1; the
core issues two instructions per cycle). An IR operation is priced by the
instructions the backend selects for it, each at its measured cost.

| Hook | X2000 value | Stock |
| --- | --- | --- |
| Vector register width | 128 | 32 |
| Vector registers | 32 | 8 |
| Interleave factor | 4 with a reduction, else 2 | 1 |
| Maximize vector bandwidth | yes: the factor fills a register with the loop's smallest element | no |
| Epilogue vectorization | no (code size) | no |
| add, sub, min, max, compare `.b/.h/.w`; `mulv.b/.h/.w`; `fadd/fsub/fmul.w` | 1 | 1 (2 for float) |
| logic, shifts, select, `fcmp`, same-size int/float conversion | 2 | 1 |
| `mul` v2i64, `fmul` v2f64 | 8 | 1, 2 |
| `fadd` v2f64 | 4 | 2 |
| integer divide | 33 per lane (132 for v4i32); scalar `div` 20 | 1 |
| `fdiv` v4f32, v2f64; `sqrt` | 128, 120; 112 | 2 |
| zero extension, per result register | 3 (`vshf` and its control move); 10 without the machine scheduler | 1-2 |
| sign extension, per result register | 6-7; 12 into 64-bit elements; 24 without the machine scheduler | 1-2 |
| truncation | 2 per `pckev` | 1 |
| narrow int to float, float to narrow int | extension or `pckev` cost + 2 (+ 8 for `ffint_s.d`) | 1 |
| saturating float to int, same element size / narrower | 2 / 4 + `pckev` cost per source register | scalarized |
| `fpext`, `fptrunc` | 6 per lane (scalarized by the backend) | 1 |
| insert an integer element; extract one | 4; 7 (twice that for `i64`) | 1 |
| any operation on a vector shorter than a register | + 1 | |
| insert or extract a float element; extract lane 0 | 4; 0 | 1 |
| shuffle: broadcast, `ilv*`, `pck*`, `shf.w` | 2 | 5-32 |
| shuffle: other integer (`vshf`) | 3 | 8-32 |
| shuffle: float other than broadcast | 14 (v4f32), 6 (v2f64): scalarized by the backend | 8 |
| vector load, store: 16-byte aligned / other | 2 / 3 | 1 |
| load, store of less than a register | 24 + 4 per word / 6 per word (through the stack and GPRs) | 1 |
| scalar load, store | 2; an extension of a loaded value is free (`lb`, `lh`) | 1 |
| scalar `mul`; `div`; `mul.d`; `div.s`; `div.d`; int/float conversion | 2; 20; 4; 30; 56; 4 | 1; 1; 2; 2; 2; 1 |
| `uadd.sat`, `usub.sat`, `sadd.sat`, `abs`, `ctlz`, `fmuladd`, rotates | by the selected instructions | generic |
| vector `fma`, `log2`, `exp2` | invalid (never vectorized; see above) | 2 |

The vectorizers have no latency term, so three prices stand in for one:

- An insert from a GPR is 4, twice its throughput cost. The Q2 run supports
  it: the `gather` loop (16 `insert.w` per 4 stores) runs at 0.56 of scalar.
- An extract to a GPR is 7, its latency. Its consumer is scalar code that
  continues a dependency chain.
- Every operation on a vector shorter than a register costs 1 more. The SLP
  vectorizer builds such vectors across the 3 or 4 channels of a pixel; when
  the next pixel needs the result (PNG's Paeth filter) the loop runs at the
  MSA latency of 3 cycles per operation against 1. That was the PNG
  regression on the Q2 (1.03).

A float lane (`splati`, `insve`) is priced at 4: float lanes are mostly
taken apart for libm calls, and all MSA registers are caller-saved, so the
live vectors are stored and reloaded around each call.

Not priced from a measurement, and marked in the source: integer divides
other than `div_s.w` (taken as 16.5 cycles per lane), `nlzc` and `pcnt`
(taken as 1.0 cycle).

Uncached memory is out of scope for the model: user space has no uncached mappings. It favours wide
accesses, which is the direction the model already takes.

### Scheduling model and machine scheduler

`MipsScheduleX2000.td` is LLVM's generic MIPS model (complete for every MIPS
instruction) rewritten by `gen_x2000_sched.py`: issue width 2; two ALU and
two MSA/FP units, one load/store unit; and the measured latencies (`lw` 3,
`mul` 3, `div` 22, `add.s`/`mul.s` 3, `add.d`/`mul.d` 5, `madd.s` 7,
`madd.d` 9, MSA integer and logic 3, `move.v`/`insve`/`insert`/`splati` 2,
`mulv`/`maddv` 4, `fadd.w`/`fmul.w` 3, `fadd.d`/`fmul.d` 7, `fmadd.d` 12,
conversions 5, MSA divides and square roots 60-66). The mispredict penalty
and DSP, COP0 and FP-load latencies are the generic values.

The MIPS backend uses a model only in the scheduler after register
allocation, which can move little: registers are already reused. With that
alone the X2000 model made no difference on the Q2 (every loop and case
equal to the generic model's, second run).

The second run also showed why a schedule matters on this core. The
vectorized `dot_i16` loop is 43 instructions, about 28 cycles by throughput,
and took 94: its eight sign extensions are each `ilvr`, `slli`, `srai`
emitted back to back, 9 cycles of latency, and 8 × 9 plus the rest is the
measured time. `i16_to_f32` fits the same sum (16.6 cycles measured for a
17-cycle chain). The core issues in order: an instruction waiting for its
operand holds up those behind it.

So for the CPU `x2000` the patch enables LLVM's machine scheduler, which
runs before register allocation with the model's latencies. It emits that
loop as all loads, all `ilvr`, all `slli`, all `srai`: independent chains
interleaved. It reorders scalar code as well. The Q2 follow-up comparison used the
pre-register-allocation scheduler, which remains the default.

Without the machine scheduler the cost model prices an extension by its
chain (zero extension 10, sign extension 24 per register) and `dot_i16`
stays scalar; with it the throughput costs apply (3 and 6).

## X2000 constraints

- **No 64-bit atomics.** Unchanged: `max-atomic-width` 32 stays in the
  target specification, which the toolchain does not replace; `spec` only
  sets `cpu` and adds `+msa`.
- **Multiply-add.** The stall once attributed to `madd.s`/`msub.s` is now
  attributed to memory thrash, and
  `+nomadd4` is not part of this change. The toolchain neither adds nor
  removes it: a specification with `+nomadd4` keeps it. Vector code has no
  multiply-add: `fmuladd` becomes `fmul` then `fadd`, and `fma` is never
  vectorized. Where a scalar loop used `madd.s` and its vector form uses
  `fmul.w` and `fadd.w`, the results are equal only if the X2000's `madd.s`
  rounds the product, as the MIPS32r2 definition and the emulator do. The Q2 qualification compared output hashes.
- **Memory thrash.** MSA code is larger where loops gain a vector body and a
  scalar remainder, and the player locks its code in RAM. This constrains epilogue vectorization
  and interleaving to control code size.
- **MSA context.** Any thread may now use MSA, so the kernel saves 128-bit
  registers for it. Measured cost of a switch with MSA state equals that with
  scalar FP state (1.7 µs; [x2000-msa.md](x2000-msa.md#first-use-and-context-switches)).
- **ISA level.** The CPU `x2000` marks objects MIPS32r5 with the MSA ASE in
  `.MIPS.abiflags`. The device and the emulator's P5600 are r5 with MSA; the
  Q2 OS runs no other CPU.
