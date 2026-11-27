# Contributing to the Q2 toolchain

Open an issue to discuss a larger change before starting. Pull requests
target `main`.

## Check a change locally

The unit tests need only Python 3 and run in under a second:

```sh
python3 -m unittest discover -s tools -p 'test_*.py'
python3 -m unittest discover -s tests/perf -p 'test_*.py'
mkdir -p build
python3 tools/x2000_toolchain.py spec q2/toolchain/mipsel-q2-linux-gnu.json build/target.json
```

A change to the compiler recipe, the source pins or the LLVM fork also needs
a full build and its checks. Follow the [build steps](README.md#build-on-x86-64-linux),
then run the [compiler and SDK checks](README.md#exercise-the-compiler-and-sdk)
with a relocated q2-os SDK and the Q2 `qemu-mipsel`. Code generation changes
belong in the [LLVM Project fork](https://github.com/q2-nova/llvm-project)
with a pin update here.

A change to the emulator recipe in `q2/toolchain/qemu-ci.json` or
`tools/build_qemu_ci.py` is checked by a manual dispatch of **Build the Q2
QEMU asset** on the default branch. [QEMU prerelease assets](docs/qemu-ci.md)

## CI

No workflow runs automatically on pull requests. Maintainers dispatch the
workflows on the pull request branch:

- **Toolchain controls** runs the unit tests above, generates the target
  specification and verifies the pinned Rust and LLVM commits and their
  pairing against the published forks.
- **Build the X2000 compiler** builds the compiler, the profile runtime and
  the release archive without publishing (up to six hours).

A change merges after review once the checks that apply to it pass. A
compiler reaches the player only as a tagged release that has passed the
[qualification steps](docs/compiler-release.md#qualify-a-compiler-archive).

## Commits

Commits on `main` must be signed. Write commit messages that state what the
change does. Keep personal data, local paths and machine names out of
commits, logs and test fixtures.

## License

Contributions are accepted under the repository's license,
`GPL-3.0-or-later`. [Licenses and attribution](NOTICE.md)
