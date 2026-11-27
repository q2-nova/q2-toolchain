# Q2 toolchain

This recipe builds stage-2 Rust and Clang for the Shanling Q2's Ingenic
X2000. The LLVM Project fork includes Rust's bundled LLVM changes, plus Q2's
MSA cost model, lowering fixes, and scheduler. The separately built
q2-os Buildroot SDK supplies the target headers, libraries, GCC runtime, and
linker. [Compiler design](docs/x2000-toolchain.md) · [Measured instruction costs](docs/x2000-msa.md)

## Build on x86-64 Linux

Use Python 3, Git, GNU tar with xz support, CMake, Ninja, and the host tools
required by Rust bootstrap, including a C and C++ compiler. Allow at least
25 GiB of free space for source preparation, build, and install. Bootstrap
may download the previous Rust compiler and Cargo.

Clone the Q2 Rust and LLVM Project forks beside this repository.
Check out the two commits in [the toolchain lock](q2/toolchain/llvm/pin.json).
The builder also accepts local bare Git repositories whose HEADs point at
the pinned commits. Rust matches the selected public nightly source, so its SHA-256-checked
distribution also supplies the vendored crates, distribution metadata,
and remaining source submodules. The recipe checks the Git checkout and its
LLVM gitlink before unpacking the archive, compares the materialized Rust
files with the Git tree, and exports the selected LLVM Project commit. Updating Rust requires a matching source distribution and
a newly checked LLVM pairing.

```sh
git clone --branch q2/nightly-2026-11-12 https://github.com/q2-nova/rust.git ../rust
git clone --branch q2/23.1 https://github.com/q2-nova/llvm-project.git ../llvm-project
git -C ../rust checkout --detach 3ef0c22cdb86ac42dc6b8f0f1ec36857daa6f2a1
git -C ../llvm-project checkout --detach 930e0ff819e61ab7894f0a2f216c2291dd58f270
python3 tools/x2000_toolchain.py check-sources --rust ../rust --llvm ../llvm-project
python3 -m unittest discover -s tools -p 'test_*.py'
python3 tools/x2000_toolchain.py build --rust ../rust --llvm ../llvm-project --jobs 2
python3 tools/x2000_toolchain.py check
```

The recipe uses `build/x2000-toolchain` by default and installs rustc,
rust-src, Clang and the LLVM tools under its `install` directory. Keep the
generated `x2000-toolchain.json` beside the binaries. Set `Q2_X2000_WORK` to
an unused directory for a new attempt. To exercise assembly without
compiling, use `prepare` with the same checkout arguments and a separate
`Q2_X2000_WORK`. Both subcommands take `--rust-source` for an already
downloaded copy of the locked distribution archive.

## Exercise the compiler and SDK

With a relocated q2-os SDK and the Q2 fork's MIPS user-mode emulator, run
the maintained C and LLVM IR fixtures. The check requires the designated
loops to vectorize and compares outputs across scalar and MSA builds.

```sh
python3 tests/perf/x2000_cost/check.py --sdk /path/to/sdk \
  --qemu-user /path/to/qemu-mipsel --out build/x2000-check
```

For player builds set `Q2_SDK` to that SDK, obtain the toolchain environment
via `python3 tools/x2000_toolchain.py shell-env`, and generate the Rust target
JSON:

```sh
mkdir -p build
python3 tools/x2000_toolchain.py spec q2/toolchain/mipsel-q2-linux-gnu.json \
  build/mipsel-q2-os-linux-gnu.json
```

Use the locked nightly's Cargo with `-Zbuild-std=std,panic_abort`,
`-Zjson-target-spec`, and the generated target. Use q2-os's
`os/rust-link.py` SDK linker shim with `Q2_OS_CC` set to the SDK's
`bin/mipsel-buildroot-linux-gnu-gcc`. Keep target directories distinct from
those using the stock nightly. `Q2_X2000_SCHED=0` selects the measured
X2000 costs with generic scheduling, and `Q2_X2000_C=0` leaves Cargo's C
dependencies on GCC.

The X2000 C wrapper carries Buildroot's stack protector, RELRO and BIND_NOW
flags under the corresponding SDK conditions. The OS owns the userspace
comparison and installation checks. [C wrapper](q2/toolchain/q2-x2000-cc)
