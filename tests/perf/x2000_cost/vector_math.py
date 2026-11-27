#!/usr/bin/env python3
"""Check generic vector log2, exp2 and fma against libm under qemu-user.

Compiles vector_math.ll with llc for the CPUs x2000 and mips32r5 (MSA, FP64,
NaN-2008), links it with vector_math.c and runs both under the Q2 fork's
qemu-mipsel (CPU XBurst2). Every lane of llvm.log2, llvm.exp2 and llvm.fma
on <4 x float> and <2 x double>, and of the MSA fmadd.w and fexp2.w
intrinsics, is compared with the scalar libm function: signed zeros,
denormals, the normal range's ends, infinities, quiet and signalling NaNs,
overflow and underflow of exp2, products whose single rounding differs from
two, and generated values. Two NaNs with different bits count as a match
and are reported apart.

  tests/perf/x2000_cost/vector_math.py --sdk SDK --qemu-user QEMU --out build/x2000-cost/NAME [--llc LLC]

Without --llc it uses the installed toolchain's (Q2_X2000_WORK). Exit status
1 when any lane differs.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

CPUS = ('x2000', 'mips32r5')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--sdk', type=Path, required=True)
    ap.add_argument('--qemu-user', type=Path, required=True)
    ap.add_argument('--llc', type=Path, help='default: the installed toolchain')
    a = ap.parse_args()
    if a.llc is None:
        import x2000_toolchain  # noqa: E402
        a.llc = x2000_toolchain.installed() / 'llvm/bin/llc'
    sdk = a.sdk.resolve()
    sysroot = sdk / 'mipsel-buildroot-linux-gnu/sysroot'
    gcc = sdk / 'bin/mipsel-buildroot-linux-gnu-gcc'
    a.out.mkdir(parents=True)
    harness = a.out / 'vector_math_c.o'
    subprocess.run([str(gcc), '-O2', '-ffp-contract=off', '-fno-builtin', '-c', str(HERE / 'vector_math.c'),
                    '-o', str(harness)], check=True)
    failed = False
    for cpu in CPUS:
        obj, binary = a.out / f'vector_math-{cpu}.o', a.out / f'vector_math-{cpu}'
        subprocess.run([str(a.llc), '-O2', '-mtriple=mipsel-linux-gnu', f'-mcpu={cpu}',
                        '-mattr=+fp64,+msa,+nan2008,+abs2008', '-relocation-model=pic', '-filetype=obj',
                        str(HERE / 'vector_math.ll'), '-o', str(obj)], check=True)
        subprocess.run([str(gcc), str(harness), str(obj), '-lm', '-o', str(binary)], check=True)
        p = subprocess.run([str(a.qemu_user.resolve()), '-cpu', 'XBurst2', '-L', str(sysroot), str(binary)],
                           capture_output=True, text=True, timeout=600)
        (a.out / f'run-{cpu}.txt').write_text(p.stdout)
        print(f'== -mcpu={cpu}\n{p.stdout}', end='')
        if p.returncode:
            failed = True
            if p.returncode != 1:
                print(f'exit {p.returncode}\n{p.stderr[-500:]}')
    print('FAILED' if failed else 'passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
