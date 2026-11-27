# Q2 toolchain

This repository builds the tools that compile and test software for the
Shanling Q2 music player on an x86-64 Linux computer: Rust and Clang
cross-compilers for the Q2's Ingenic X2000 processor, and a QEMU emulator
that runs Q2 programs and boots Q2 system images.

It is for contributors who change a compiler recipe, the X2000 code
generation or the emulator build. To change the player, you don't need to
build these tools: the player's build downloads the tagged compiler and
emulator releases it needs. [Player build guide](https://github.com/q2-nova/q2-player/blob/main/docs/target-build.md)

Rust and Clang use the same Q2 LLVM fork, which adds X2000 instruction
costs, scheduling and code generation fixes. [Compiler design](docs/x2000-toolchain.md) · [Measured instruction costs](docs/x2000-msa.md)

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
downloaded copy of the locked distribution archive. The release workflow
runs the same recipe in stages. [Build stages](docs/compiler-release.md#build-stages)

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

## Profile-guided builds

The compiler carries what a profile-guided Q2 build needs from the LLVM
inside rustc: `install/llvm/bin/llvm-profdata` and compiler-rt's profile
runtime for the Q2 at
`install/llvm/lib/mipsel-q2-os-linux-gnu/libclang_rt.profile.a`. The X2000
C wrapper builds the runtime from the pinned LLVM Project commit against
the SDK sysroot. The SDK release is pinned in
[the runtime lock](q2/toolchain/profile-runtime.json). The receipt
`install/x2000-toolchain.json` records the archive's SHA-256, the source
commit and each source file's SHA-256 under `profile_runtime`. The fork
leaves `compiler-rt/lib/profile` unchanged from Rust's LLVM base.

```sh
python3 tools/x2000_toolchain.py profile-runtime --sdk /path/to/sdk \
  --sdk-archive-sha256 SHA256 --llvm ../llvm-project
```

Consumers' `tools/pgo.py` use the shared driver
[`tools/q2_pgo.py`](tools/q2_pgo.py). It provides the instrumented-build
rustflags, training runs under `qemu-mipsel` or `qemu-system-mipsel` with
`LLVM_PROFILE_FILE`, a weighted `llvm-profdata merge`, a coverage report
(functions and regions with counts per crate and module, plus named hot
paths) and the profile receipt that builds check before `-Cprofile-use`.

## Releases

Compiler and emulator releases are GitHub prereleases for developers.
[Tool releases](https://github.com/q2-nova/q2-toolchain/releases)

- [Release and qualify the compiler](docs/compiler-release.md): the tag
  workflow, the manual checks and the qualification steps a compiler archive
  passes before the player selects it.
- [QEMU prerelease assets](docs/qemu-ci.md): the emulator build, its checks
  and its runtime requirements.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the local checks and what runs
in CI. Report security problems privately as described in
[SECURITY.md](SECURITY.md). Participation follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Related repositories

- [q2-player](https://github.com/q2-nova/q2-player): the player app and the installable update. Start there to install Q2 Nova.
- [q2-os](https://github.com/q2-nova/q2-os): the Linux system, recovery environment and SDK.
- [tsrs](https://github.com/q2-nova/tsrs): the Tailscale network daemon.
- Forks this repository builds from: [rust](https://github.com/q2-nova/rust) (branch `q2/nightly-2026-11-12`), [llvm-project](https://github.com/q2-nova/llvm-project) (branch `q2/23.1`) and [qemu](https://github.com/q2-nova/qemu) (branch `q2/10.1`).

## License

Original Q2 material is licensed under the GNU General Public License,
version 3 or any later version (`GPL-3.0-or-later`). See [LICENSE](LICENSE).
The firmware-sysroot helpers retain MIT-licensed code from q2-librespot
([LICENSES/q2-librespot-MIT.txt](LICENSES/q2-librespot-MIT.txt)). Rust, the
LLVM Project and QEMU keep their upstream licenses. [Licenses and attribution](NOTICE.md)
