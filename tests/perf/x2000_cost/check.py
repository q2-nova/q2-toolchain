#!/usr/bin/env python3
"""Check the X2000 cost model and MSA lowering with the built toolchain.

Compiles loops.c and sat.ll with the X2000 clang three times (scalar MIPS32r2;
CPU x2000; the X2000 costs and lowering with generic scheduling), then:
- compares each function's use of MSA registers with its EXPECT line;
- links both with bench.c and runs them under the rig qemu-user: every
  function's output hash must be the same in both builds (unaligned buffers,
  lengths that are not multiples of the vector width).

  tests/perf/x2000_cost/check.py --sdk SDK --qemu-user QEMU --out build/x2000-cost/NAME

OUT/bench-scalar, OUT/bench-x2000 and OUT/bench-gensched are Q2 executables. On the Q2 they print
the time per element of each function, which is the test of the cost
model's decisions (device.py runs both and prints the ratios).

Exit status 1 on an unmet expectation or a differing hash.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import x2000_toolchain  # noqa: E402

COMMON = ['--target=mipsel-linux-gnu', '-mfp64', '-mnan=2008', '-mabs=2008', '-mabicalls', '-fPIC', '-O2',
          '-ffp-contract=off', '-fno-inline']
ARMS = {'scalar': ['-march=mips32r2'],
        # The default: CPU x2000, its scheduling model and the machine scheduler.
        'x2000': ['-march=x2000', '-mmsa'],
        # Costs and lowering with the generic scheduling model.
        'gensched': ['-march=mips32r5', '-mmsa', '-Xclang', '-target-feature', '-Xclang', '+x2000']}


def expectations():
    out = {}
    for source in ('loops.c', 'sat.ll'):
        for m in re.finditer(r'EXPECT (\w+) (vector|scalar|any):', (HERE / source).read_text()):
            out[m.group(1)] = m.group(2)
    return out


def msa_use(asm):
    """Instructions and MSA-register instructions per function."""
    use, name = {}, None
    for line in asm.splitlines():
        m = re.match(r'^([A-Za-z_]\w*):', line)
        if m:
            name = m.group(1)
            use[name] = [0, 0]
        elif name and re.match(r'^\s+[a-z]', line):
            use[name][0] += 1
            use[name][1] += bool(re.search(r'\$w\d', line))
    return use


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--reps', type=int, default=3, help='benchmark repetitions in the emulator (times are not Q2 times)')
    ap.add_argument('--sdk', type=Path, required=True)
    ap.add_argument('--qemu-user', type=Path, required=True)
    a = ap.parse_args()
    install = x2000_toolchain.installed()
    clang = install / 'llvm/bin/clang'
    sdk = a.sdk.resolve()
    sysroot = sdk / 'mipsel-buildroot-linux-gnu/sysroot'
    gcc = sdk / 'bin/mipsel-buildroot-linux-gnu-gcc'
    a.out.mkdir(parents=True)
    headers = ['-nostdlibinc', '-isystem', str(sysroot / 'usr/include')]
    subprocess.run([str(gcc), '-O2', '-c', str(HERE / 'bench.c'), '-o', str(a.out / 'bench.o')], check=True)
    results, uses = {}, {}
    for arm, flags in ARMS.items():
        uses[arm], objects = {}, []
        for source in ('loops.c', 'sat.ll'):
            stem = source.split('.')[0]
            base = [str(clang), *COMMON, *flags, *(headers if source.endswith('.c') else ['-Wno-override-module']),
                    str(HERE / source)]
            subprocess.run([*base, '-S', '-o', str(a.out / f'{stem}-{arm}.s')], check=True)
            subprocess.run([*base, '-c', '-o', str(a.out / f'{stem}-{arm}.o')], check=True)
            objects.append(str(a.out / f'{stem}-{arm}.o'))
            uses[arm].update(msa_use((a.out / f'{stem}-{arm}.s').read_text()))
        binary = a.out / f'bench-{arm}'
        subprocess.run([str(gcc), str(a.out / 'bench.o'), *objects, '-lm', '-o', str(binary)], check=True)
        p = subprocess.run([str(a.qemu_user.resolve()), '-cpu', 'P5600', '-L', str(sysroot), str(binary), str(a.reps)],
                           capture_output=True, text=True, timeout=600)
        if p.returncode:
            print(f'{arm}: exit {p.returncode}\n{p.stderr[-500:]}')
            return 1
        (a.out / f'run-{arm}.txt').write_text(p.stdout)
        results[arm] = {line.split()[0]: line.split()[1] for line in p.stdout.splitlines()}
    failed = False
    print(f"{'function':18}{'expect':>8}{'x2000':>8}{'insns':>7}{'msa':>5}   output")
    for name, want in expectations().items():
        insns, msa = uses['x2000'][name]
        got = 'vector' if msa else 'scalar'
        same = name in results['scalar'] and len({results[arm].get(name) for arm in ARMS}) == 1
        ok = (want == 'any' or want == got) and same and not uses['scalar'][name][1]
        failed |= not ok
        print(f"{name:18}{want:>8}{got:>8}{insns:>7}{msa:>5}   {'same' if same else 'DIFFERS'}{'' if ok else '   FAIL'}")
    print('FAILED' if failed else 'passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
