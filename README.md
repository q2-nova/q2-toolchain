# Q2 toolchain

This recipe builds stage-2 Rust and Clang for the Shanling Q2's Ingenic
X2000. The LLVM Project fork includes Rust's bundled LLVM changes, plus Q2's
MSA cost model, lowering fixes, and scheduler. The separately built
q2-os Buildroot SDK supplies the target headers, libraries, GCC runtime, and
linker. [Compiler design](docs/x2000-toolchain.md) · [Measured instruction costs](docs/x2000-msa.md)

Original Q2 material is GPL-3.0-or-later. [Licenses and attribution](NOTICE.md)

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

## Compiler qualification and promotion

The manually dispatched toolchain checks run the Python controls, generate the Q2 target specification and verify the pinned Rust and LLVM commits and their pairing. CI fetches shallow Git metadata from the published forks and uses the checked-in pins. Compiler builds are a separate release input. The player build consumes a selected compiler and SDK only after qualification.

1. Build from the reviewed toolchain commit with the pinned Rust and LLVM sources and a new work directory. Run `python3 tools/x2000_toolchain.py check` on the completed installation. Record the toolchain recipe commit, source lock, host architecture and Linux ABI, host compiler and bootstrap configuration.
2. With the selected relocated q2-os SDK and pinned QEMU MIPS user emulator, run the C and LLVM IR checks above. Use the installed rustc with the pinned Cargo, Q2 target JSON, q2-os linker shim and `-Zbuild-std=std,panic_abort` to build a Rust program using the standard library. Confirm it runs with QEMU's P5600 CPU and the SDK sysroot. Record the SDK and emulator identities, commands and results.
3. Package the complete `install` directory intact. It contains rustc, its host libraries and Rust source, Clang, its resource headers, the LLVM tools and `x2000-toolchain.json`. Keep upstream Rust and LLVM notices and the applicable third-party notices with any distributed compiler. The package command includes the Q2, Rust, LLVM and Clang notices in `licenses/`.
4. On a clean Linux x86-64 host matching the minimum supported runtime ABI, unpack the exact candidate archive at a different path with the original build path unavailable. Point `Q2_X2000_WORK` at the directory containing the unpacked `install`. Repeat `check`, the C/LLVM IR tests and the Rust cross-build and execution. Verify the tools resolve their host libraries and Clang finds `llvm/lib/clang` resources after relocation. Record the minimum tested host and any required system libraries alongside the archive.
5. Record the SHA-256 of the qualified archive and retained install receipt with the promotion decision. The archive digest covers Rust sources, Clang headers and other contents beyond the binaries checked by `check`. Select that exact archive digest together with the recipe commit and compiler lock in the consumer's component manifest. Reuse it across consumer builds with those same inputs and the supported host ABI.

### Stage a qualified archive in GitHub

Run the packaging and qualification commands on Linux x86-64. Qualification runs the compiler and SDK, so use an isolated, trusted host with the minimum supported Linux ABI and no access to the build checkout's original absolute paths. Keep the package and report together.

```sh
python3 tools/compiler_release.py pack \
  --producer "$PRODUCER_COMMIT" --work "$Q2_X2000_WORK" --bundle compiler.tar.gz

python3 tools/compiler_release.py qualify \
  --producer "$PRODUCER_COMMIT" --bundle compiler.tar.gz --report qualification.json \
  --sdk "$SDK" --sdk-receipt "$SDK_RELOCATION_RECEIPT" \
  --rust-link "$OS_CHECKOUT/os/rust-link.py" \
  --qemu-user "$QEMU_USER" --qemu-receipt "$QEMU_RELEASE_RECEIPT"

sha256sum compiler.tar.gz
```

The SDK relocation receipt provides its `sdk` path and `archive_sha256` from the Buildroot SDK. The QEMU receipt provides `source_commit` and `qemu_user_sha256`. The qualifier rechecks the QEMU binary digest, unpacks the compiler under a new directory and runs the install check, the C/IR fixture and a Rust standard-library program using the SDK sysroot in QEMU. The resulting qualification record contains hashes, identities and the tested ABI.

Review the actual archive and qualification record, including licenses, host paths and any redistribution restrictions. A trusted operator uploads the reviewed `compiler.tar.gz` and `qualification.json` to an intake draft release. Dispatch **Stage a compiler release** on the default branch using `stage`, the exact producer recipe commit, intake tag, unused draft tag and reviewed bundle SHA-256. The workflow checks the producer and upstream lock, archive hash and qualification record and attaches the same bytes to a new draft. Keep the intake draft unpublished. Review the new draft and its downloads before deciding to publish.

Dispatch `validate` to exercise admission with a generated fixture. The validation job has read-only permissions and rejects the fixture in release mode. Both workflow modes use hosted Linux runners for validation or staging. Building and qualifying an actual compiler require a separate build host, a relocated SDK and compatible QEMU; they run using the local commands above.

### Build the compiler in GitHub Actions

Dispatch **Build the X2000 compiler** on `main`. The optional `producer`
selects an exact recipe commit in its history. A hosted Linux x86-64 runner
fetches the selected Rust and LLVM sources, builds stage-2 rustc and Clang,
checks the installation and uploads `q2-compiler-linux-x86_64`. This Actions
artifact contains `compiler.tar.gz` and `SHA256SUMS`. Use the run ID and
archive hash to select the candidate for SDK/QEMU qualification. Promote the
qualified archive once, then reuse it for player and daemon builds.
