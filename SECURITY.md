# Security policy

Report a vulnerability privately: open this repository's **Security** tab and
choose **Report a vulnerability**. Do not open a public issue or pull request
for a vulnerability.

Include the affected release tag or commit, the steps to reproduce, and what
an attacker gains. A report gets a reply in the advisory thread, and the fix
is published with the advisory.

## Supported versions

Fixes go to `main` and the next tagged compiler or QEMU release. Earlier
prereleases are not updated.

## Scope

This repository covers the compiler recipe, the target specification, the C
wrapper and linker scripts, the PGO driver and the QEMU build recipe.
Vulnerabilities in upstream Rust, LLVM or QEMU code belong with those
projects unless the Q2 fork introduced them.
