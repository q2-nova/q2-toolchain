#!/usr/bin/env python3
"""X2000 MSA and scalar instruction costs (docs/x2000-msa.md): build, check, summarize, tabulate.

  tests/perf/msa_microbench.py build --sdk SDK --out DIR
  tests/perf/msa_microbench.py qemu --qemu-user QEMU_MIPSEL --out DIR [--runs 1]
  tests/perf/msa_microbench.py summarize DIR
  tests/perf/msa_microbench.py table DIR/summary.json

`build` compiles tests/perf/msa_microbench/msa_bench.c with the SDK's GCC
(static, -march=mips32r5 -mfp64 -mmsa, no auto-vectorization) to DIR/msa-bench
and records the compiler, flags and hashes in DIR/build.json.

The Q2 runs come from q2-factory `gates/perf/msa_microbench_device.py
--binary DIR/msa-bench --out DIR/q2`, which runs the program on CPU 0 under
the device lease and writes DIR/q2/run-N.txt and DIR/q2/device.json.
`qemu` runs it under qemu-user instead (DIR/qemu/run-N.txt): that checks the
program executes and that every scalar and MSA kernel gives the same output;
its times are not the Q2's.

`summarize` reads DIR/build.json and the runs of DIR/q2 (or DIR/qemu) and
writes summary.json beside them: per benchmark the minimum over runs in ns,
in cycles at the CPU frequency the program read, and the run spread (slowest
over fastest). On the Q2 it refuses runs whose CPU frequency changed. The
values are descriptive: the minimum of repeated timings of a fixed loop on
one device, not a comparison. `table` prints the Markdown tables of
docs/x2000-msa.md from a summary.json.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'msa_microbench/msa_bench.c'
CC = 'bin/mipsel-buildroot-linux-gnu-gcc'
FLAGS = ['-O2', '-std=gnu11', '-Wall', '-Wextra', '-mabi=32', '-march=mips32r5', '-mhard-float', '-mfp64',
         '-mnan=2008', '-mabs=2008', '-mmsa', '-fno-tree-vectorize', '-fno-tree-slp-vectorize',
         '-fno-toplevel-reorder', '-static', '-pthread']
VALUE_DEPENDENT = {'sqrt.s', 'sqrt.d', 'fsqrt.w', 'fsqrt.d', 'frsqrt.w'}
FREQ_KEYS = ('cpufreq_khz_start', 'cpufreq_khz_after_instructions', 'cpufreq_khz_end')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(sdk, out):
    cc = Path(sdk) / CC
    if not cc.is_file():
        raise SystemExit(f'{cc} does not exist: --sdk names a relocated q2-os SDK')
    out.mkdir(parents=True, exist_ok=False)
    binary = out / 'msa-bench'
    subprocess.run([str(cc), *FLAGS, str(SOURCE), '-o', str(binary)], check=True, timeout=300)
    meta = {'compiler': subprocess.check_output([str(cc), '--version'], text=True).splitlines()[0],
            'flags': FLAGS, 'source_sha256': sha(SOURCE), 'binary_sha256': sha(binary)}
    (out / 'build.json').write_text(json.dumps(meta, indent=1) + '\n')
    return meta


def parse(text):
    """One run's output -> header, benchmarks, memory and first-use values."""
    run = {'header': {}, 'bench': {}, 'memory': {}, 'first_use': {}}
    for line in text.splitlines():
        if line.startswith('H '):
            key, value = line[2:].split(' ', 1)
            run['header'][key] = value
        elif line.startswith('F '):
            key, value = line[2:].split(' ', 1)
            words = value.split()
            if len(words) == 1 and re.fullmatch(r'[0-9.]+', words[0]):
                run['first_use'][key] = float(words[0])
            elif len(words) == 6:
                run['first_use'][key] = {words[i]: int(words[i + 1]) for i in (0, 2, 4)}
        elif line.startswith('B\t'):
            f = line.split('\t')
            if f[3].startswith('signal'):
                run['bench'][(f[1], f[2])] = {'error': f[3]}
            else:
                run['bench'][(f[1], f[2])] = {'ops': int(f[3]), 'ns': float(f[5]), 'ns_median': float(f[6]),
                                              **({'checksum': f[7]} if len(f) > 7 else {})}
        elif line.startswith('M\t'):
            f = line.split('\t')
            if f[1] != 'skipped' and not f[4].startswith('signal'):
                run['memory'][(f[1], f[2], int(f[3]))] = float(f[4])
    return run


def summarize(runs, meta):
    if not runs:
        raise SystemExit('no runs')
    khz = {r['header'].get(k) for r in runs for k in FREQ_KEYS}
    if len(khz) != 1 and meta['target'] == 'Q2':
        raise SystemExit(f'cpufreq changed during the runs: {sorted(khz, key=str)}')
    ghz = int(runs[0]['header']['cpufreq_khz_start']) / 1e6

    def cyc(ns):
        return round(ns * ghz, 3)
    names, kernels = {}, {}
    for key in runs[0]['bench']:
        rows = [r['bench'][key] for r in runs if key in r['bench']]
        name, variant = key
        if any('error' in row for row in rows):
            names.setdefault(name, {})[variant] = {'error': rows[0].get('error')}
            continue
        ns = [row['ns'] for row in rows]
        entry = {'ns': min(ns), 'cycles': cyc(min(ns)), 'run_spread': round(max(ns) / min(ns), 3)}
        if rows[0]['ops'] == 1024 and 'checksum' in rows[0]:
            entry['checksum'] = rows[0]['checksum']
            kernels.setdefault(name, {})[variant] = entry
        else:
            names.setdefault(name, {})[variant] = entry
    for name, variants in names.items():
        free = [v['cycles'] for k, v in variants.items() if k in ('t4', 't8', 't12', 'ind') and 'cycles' in v]
        if free:
            # sqrt chains settle at 1.0, which this core computes faster; only `ind` has a general operand
            variants['throughput'] = {'cycles': variants['ind']['cycles'] if name in VALUE_DEPENDENT else min(free)}
    for name, variants in kernels.items():
        variants['outputs_identical'] = len({v['checksum'] for v in variants.values()}) == 1
    memory = {}
    for key in runs[0]['memory']:
        ns = min(r['memory'][key] for r in runs if key in r['memory'])
        memory.setdefault(f'{key[0]}: {key[1]}', {})[str(key[2])] = {
            'ns_per_byte': ns, 'cycles_per_byte': cyc(ns), 'mib_per_s': round(1e9 / ns / 1048576, 1)}
    first = {}
    for key in runs[0]['first_use']:
        values = [r['first_use'][key] for r in runs if key in r['first_use']]
        first[key] = min(values) if isinstance(values[0], float) else {
            'min': min(v['min'] for v in values), 'median': sorted(v['median'] for v in values)[len(values) // 2],
            'max': max(v['max'] for v in values)}
    return {'meta': meta, 'cpufreq_ghz': ghz, 'headers': [r['header'] for r in runs], 'instructions': names,
            'kernels': kernels, 'memory': memory, 'first_use': first}


def read_runs(directory):
    paths = sorted(directory.glob('run-*.txt'), key=lambda p: int(re.sub(r'\D', '', p.name) or 0))
    texts = [p.read_text() for p in paths]
    for path, text in zip(paths, texts):
        if 'H done 1' not in text:
            raise SystemExit(f'{path} did not finish')
    return [parse(t) for t in texts]


def summarize_dir(out):
    build_meta = json.loads((out / 'build.json').read_text())
    for target, sub in (('Q2', 'q2'), ('qemu-user', 'qemu')):
        if (out / sub).is_dir():
            break
    else:
        raise SystemExit(f'{out} has neither q2/ nor qemu/ runs')
    meta = dict(build_meta, target=target)
    device = out / sub / 'device.json'
    if device.exists():
        record = json.loads(device.read_text())
        if record.get('binary_sha256') != build_meta['binary_sha256']:
            raise SystemExit(f'{device} ran another binary than {out}/build.json records')
        meta['device'] = record
    summary = summarize(read_runs(out / sub), meta)
    path = out / sub / 'summary.json'
    path.write_text(json.dumps(summary, indent=1) + '\n')
    return path, summary


def qemu(qemu_user, out, runs):
    meta = json.loads((out / 'build.json').read_text())
    if sha(out / 'msa-bench') != meta['binary_sha256']:
        raise SystemExit(f'{out}/msa-bench differs from build.json')
    (out / 'qemu').mkdir()
    for i in range(runs):
        text = subprocess.run([str(qemu_user), '-cpu', 'P5600', str(out / 'msa-bench')],
                              capture_output=True, timeout=900, check=True).stdout.decode()
        (out / 'qemu' / f'run-{i + 1}.txt').write_text(text)
    path, summary = summarize_dir(out)
    differ = sorted(name for name, v in summary['kernels'].items() if not v['outputs_identical'])
    if differ:
        raise SystemExit(f'scalar and MSA outputs differ: {", ".join(differ)}')
    return path, summary


def table(path, write=print):
    """Markdown tables for docs/x2000-msa.md from a summary.json."""
    s = json.loads(Path(path).read_text())

    def num(v):
        return '' if v is None or 'cycles' not in v else f"{v['cycles']:.2f}"
    write('| Instruction | Latency | 4 chains | 8 chains | 12 chains | Independent | Accumulator | Throughput |')
    write('| --- | --- | --- | --- | --- | --- | --- | --- |')
    singles = []
    for name, v in s['instructions'].items():
        if 'throughput' in v:
            write(f"| `{name}` | {num(v.get('lat'))} | {num(v.get('t4'))} | {num(v.get('t8'))} | {num(v.get('t12'))} | "
                  f"{num(v.get('ind'))} | {num(v.get('acc'))} | {num(v['throughput'])} |")
        else:
            singles.append((name, v))
    write('\n| Sequence | Variant | Cycles per repetition |\n| --- | --- | --- |')
    for name, v in singles:
        for variant, entry in v.items():
            write(f"| `{name}` | {variant} | {entry.get('error') or num(entry)} |")
    write('\n| Kernel | Variant | Cycles per element | Outputs identical |\n| --- | --- | --- | --- |')
    for name, v in s['kernels'].items():
        for variant, entry in v.items():
            if variant != 'outputs_identical':
                write(f"| {name} | {variant} | {num(entry)} | {'yes' if v['outputs_identical'] else 'NO'} |")
    sizes = sorted({int(k) for v in s['memory'].values() for k in v})
    write('\n| Test | ' + ' | '.join(f'{k // 1024} KiB' for k in sizes) + ' |\n| --- |' + ' --- |' * len(sizes))
    for name, v in s['memory'].items():
        cells = [f"{v[str(k)]['mib_per_s']:.0f} MiB/s, {v[str(k)]['cycles_per_byte']:.2f} c/B" if str(k) in v
                 else 'not measured' for k in sizes]
        write(f'| {name} | ' + ' | '.join(cells) + ' |')
    write('\n| First use and context | Value |\n| --- | --- |')
    for key, v in s['first_use'].items():
        write(f'| {key} | ' + (f'{v:.0f}' if isinstance(v, float) else
                               f"min {v['min']}, median {v['median']}, max {v['max']}") + ' |')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='action', required=True)
    b = sub.add_parser('build')
    b.add_argument('--sdk', type=Path, required=True)
    b.add_argument('--out', type=Path, required=True)
    q = sub.add_parser('qemu')
    q.add_argument('--qemu-user', type=Path, required=True)
    q.add_argument('--out', type=Path, required=True, help='a build directory')
    q.add_argument('--runs', type=int, default=1, choices=range(1, 6))
    s = sub.add_parser('summarize')
    s.add_argument('out', type=Path, help='a build directory with q2/ or qemu/ runs')
    t = sub.add_parser('table')
    t.add_argument('summary', type=Path)
    a = ap.parse_args(argv)
    if a.action == 'build':
        meta = build(a.sdk, a.out)
        print(f"{a.out}/msa-bench {meta['binary_sha256']}")
    elif a.action == 'qemu':
        path, summary = qemu(a.qemu_user, a.out, a.runs)
        print(f"{path}: {len(summary['instructions'])} instructions, {len(summary['kernels'])} kernels, "
              'scalar and MSA outputs identical')
    elif a.action == 'summarize':
        path, summary = summarize_dir(a.out)
        print(f"{path}: {len(summary['instructions'])} instructions, {len(summary['kernels'])} kernels, "
              f"{summary['cpufreq_ghz']} GHz")
    else:
        table(a.summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
