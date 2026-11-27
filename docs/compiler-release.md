# Release and qualify the compiler

A compiler release is a GitHub prerelease holding `compiler.tar.gz` and
`compiler.tar.gz.sha256`. Consumers such as the player select it by tag and
reuse it until their compiler pin changes. The QEMU emulator has its own
release procedure. [QEMU prerelease assets](qemu-ci.md)

## Release the compiler

Push a version tag such as `v0.1.0-rc.1`. The **Build the X2000 compiler**
workflow builds and checks the compiler at that tag, then attaches
`compiler.tar.gz` and `compiler.tar.gz.sha256` to the tag's GitHub
prerelease. The archive contains the compiler, Rust sources, licenses and
build manifest. Verify its checksum and extract it before setting
`Q2_X2000_WORK` to the extracted directory.

A manual dispatch of **Build the X2000 compiler** runs the same build
without publishing. For SDK and QEMU qualification of a candidate archive,
use `tools/compiler_release.py qualify --help`.

## Checks on every compiler change

The **Toolchain controls** workflow runs on manual dispatch. It runs the
Python unit tests, generates the Q2 target specification and verifies the
pinned Rust and LLVM commits and their pairing. It fetches shallow Git
metadata from the published forks and uses the checked-in pins. Compiler
builds are a separate release input: the player build consumes a selected
compiler and SDK only after the qualification below.

## Qualify a compiler archive

1. Build from the reviewed toolchain commit with the pinned Rust and LLVM sources and a new work directory. Run `python3 tools/x2000_toolchain.py check` on the completed installation. Record the toolchain recipe commit, source lock, host architecture and Linux ABI, host compiler and bootstrap configuration.
2. With the selected relocated q2-os SDK and pinned QEMU MIPS user emulator, run the C and LLVM IR checks in the [README](../README.md#exercise-the-compiler-and-sdk). Use the installed rustc with the pinned Cargo, Q2 target JSON, q2-os linker shim and `-Zbuild-std=std,panic_abort` to build a Rust program using the standard library. Confirm it runs with QEMU's P5600 CPU and the SDK sysroot. Record the SDK and emulator identities, commands and results.
3. Package the complete `install` directory intact. It contains rustc, its host libraries and Rust source, Clang, its resource headers, the LLVM tools and `x2000-toolchain.json`. Keep upstream Rust and LLVM notices and the applicable third-party notices with any distributed compiler. The package command includes the Q2, Rust, LLVM and Clang notices in `licenses/`.
4. On a clean Linux x86-64 host matching the minimum supported runtime ABI, unpack the exact candidate archive at a different path with the original build path unavailable. Point `Q2_X2000_WORK` at the directory containing the unpacked `install`. Repeat `check`, the C/LLVM IR tests and the Rust cross-build and execution. Verify the tools resolve their host libraries and Clang finds `llvm/lib/clang` resources after relocation. Record the minimum tested host and any required system libraries alongside the archive.
5. Record the SHA-256 of the qualified archive and retained install receipt with the promotion decision. The archive digest covers Rust sources, Clang headers and other contents beyond the binaries checked by `check`. Select that exact archive digest together with the recipe commit and compiler lock in the consumer's component manifest. Reuse it across consumer builds with those same inputs and the supported host ABI.
