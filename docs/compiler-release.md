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

## Build stages

The workflow runs four jobs, each after the one before it. Each building
job fetches the pinned Rust and LLVM commits, checks them against
`q2/toolchain/llvm/pin.json` and assembles the source tree from them, then
continues from the previous job's handoff.

| Job | Builds | Hands off |
| --- | --- | --- |
| **Build LLVM and Clang** | `x.py build src/llvm-project/llvm` | `llvm-handoff`: LLVM's output directory `build/x86_64-unknown-linux-gnu/llvm`. Of its CMake build tree it keeps the Clang and LLVM tool binaries, Clang's resource headers and the MIPS code generation library, which the Rust job reads. About 510 MiB. |
| **Build stage-2 Rust** | `x.py install` on the handed-off LLVM, then adds Clang and the LLVM tools to `install` and writes its receipt | `install-handoff`: the `install` directory. About 180 MiB. |
| **Add the Q2 profile runtime and package the compiler** | the profile runtime, `compiler.tar.gz` and `compiler.tar.gz.sha256` | `compiler`: the two files |
| **Publish the tagged compiler** | on tag pushes only, the prerelease from the `compiler` files | |

A handoff is a `.tar.zst` archive and a JSON manifest. The manifest holds
the producing commit, the source lock, the work directory, the SHA-256 of
the bootstrap configuration without its job counts, the archive's SHA-256,
and the mode and SHA-256 of every file. The LLVM manifest also holds
`CFLAGS` and `CXXFLAGS`, which the Rust build must repeat. The next job
refuses a handoff whose manifest differs from its own checkout in
any of these, whose archive has another digest, or whose unpacked files
differ from the list. The Rust job also fails if bootstrap changed the
handed-off LLVM, so the packaged Clang and LLVM tools are the LLVM job's.
The publish job checks the archive against its checksum file and that the
bundle names the tagged commit as its producer.

The same steps run locally: `build-llvm --handoff DIR`, then
`build-rust --from DIR --handoff DIR2`, then `prepare` and
`restore install --from DIR2`. Each step needs an empty work directory at
the same `Q2_X2000_WORK` path, because the manifest records that path.
`build` runs the whole recipe in one work directory.

## Retry a failed stage

Choose **Re-run failed jobs** on the run, or run
`gh run rerun RUN_ID --failed`. Jobs that passed are not repeated: the
re-run jobs download the handoffs that the earlier jobs of that run
uploaded. To repeat a job that passed, re-run it alone with
`gh run rerun RUN_ID --job JOB_ID`, with the job ID from
`gh run view RUN_ID --json jobs`. GitHub also re-runs the jobs after it.

A re-run builds the run's original commit, so a change to the recipe needs
a new run. Handoffs are kept for 30 days, the period in which GitHub allows
a re-run. The publish job fails if the tag's prerelease already exists.

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
