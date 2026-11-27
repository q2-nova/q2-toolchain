"""msa_microbench.py on generated program output: parsing, summaries, refusals and tables."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import msa_microbench as mb  # noqa: E402

SCRIPT = Path(mb.__file__)


def output(khz=(1200000, 1200000, 1200000), scale=1.0, kernel_sums=('9f00', '9f00'), done=True):
    """Text in msa_bench.c's line formats (values generated)."""
    lines = [f'H cpufreq_khz_start {khz[0]}', 'H governor_start performance',
             f'H cpufreq_khz_after_instructions {khz[1]}', f'H cpufreq_khz_end {khz[2]}']
    for variant, ns in (('lat', 0.85), ('t4', 0.30), ('t8', 0.27), ('t12', 0.25), ('ind', 0.42)):
        lines.append(f'B\taddu\t{variant}\t96\t1000\t{ns * scale:.4f}\t{ns * scale * 1.01:.4f}')
    for variant, ns in (('lat', 5.0), ('t4', 0.4), ('ind', 2.5)):
        lines.append(f'B\tsqrt.s\t{variant}\t64\t1000\t{ns * scale:.4f}\t{ns * scale:.4f}')
    lines.append(f'B\tldi+st\tpair\t2\t1000\t{1.25 * scale:.4f}\t{1.3 * scale:.4f}')
    lines.append('B\tdiv.w\tlat\tsignal 4')
    for variant, checksum in zip(('scalar', 'msa'), kernel_sums):
        lines.append(f'B\tbiquad\t{variant}\t1024\t100\t{2.0 * scale:.4f}\t{2.1 * scale:.4f}\t{checksum}')
    lines += [f'M\tld.w\taligned\t16384\t{0.25 * scale:.4f}', 'M\tskipped\t8388608\tlow memory',
              'F msa_first_use_ns min 900 median 1100 max 4000', 'F pipe_round_trip_msa_ns 21000',
              'F msa_thread failed', 'H elapsed_s 31.0']
    if done:
        lines.append('H done 1')
    return '\n'.join(lines) + '\n'


class Summaries(unittest.TestCase):
    def summary(self, texts, target='Q2'):
        return mb.summarize([mb.parse(t) for t in texts], dict(target=target))

    def test_minimum_over_runs_in_cycles_with_spread(self):
        s = self.summary([output(scale=1.1), output()])
        addu = s['instructions']['addu']
        self.assertEqual(addu['lat'], {'ns': 0.85, 'cycles': 1.02, 'run_spread': 1.1})
        self.assertEqual(addu['throughput'], {'cycles': 0.3})
        self.assertEqual(s['instructions']['div.w']['lat'], {'error': 'signal 4'})
        self.assertEqual(s['memory']['ld.w: aligned']['16384']['cycles_per_byte'], 0.3)
        self.assertEqual(s['first_use']['msa_first_use_ns'], {'min': 900, 'median': 1100, 'max': 4000})
        self.assertNotIn('msa_thread', s['first_use'])

    def test_value_dependent_throughput_uses_the_independent_column(self):
        sqrt = self.summary([output()])['instructions']['sqrt.s']
        self.assertEqual(sqrt['throughput'], {'cycles': 3.0})

    def test_kernel_outputs_compared(self):
        self.assertTrue(self.summary([output()])['kernels']['biquad']['outputs_identical'])
        self.assertFalse(self.summary([output(kernel_sums=('9f00', '9f01'))])['kernels']['biquad']['outputs_identical'])

    def test_changed_frequency_refused_on_the_q2_only(self):
        changed = output(khz=(1200000, 1008000, 1200000))
        with self.assertRaisesRegex(SystemExit, 'cpufreq changed'):
            self.summary([changed])
        self.summary([changed], target='qemu-user')


class Commands(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / 'build'
        self.out.mkdir()
        (self.out / 'msa-bench').write_bytes(b'\x7fELF generated')
        digest = hashlib.sha256(b'\x7fELF generated').hexdigest()
        (self.out / 'build.json').write_text(json.dumps(dict(binary_sha256=digest, source_sha256='0' * 64)))
        self.digest = digest

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def fake_qemu(self, text):
        fake = self.out.parent / 'qemu-mipsel'
        (self.out.parent / 'output.txt').write_text(text)
        fake.write_text(f'#!{sys.executable}\nimport sys\nassert sys.argv[1:3] == ["-cpu", "P5600"]\n'
                        f'sys.stdout.write(open({str(self.out.parent / "output.txt")!r}).read())\n')
        os.chmod(fake, 0o755)
        return fake

    def test_qemu_check_summarizes_and_tabulates(self):
        r = self.cli('qemu', '--qemu-user', str(self.fake_qemu(output())), '--out', str(self.out), '--runs', '2')
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = json.loads((self.out / 'qemu/summary.json').read_text())
        self.assertEqual(summary['meta']['target'], 'qemu-user')
        r = self.cli('table', str(self.out / 'qemu/summary.json'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('| `addu` | 1.02 | 0.36 | 0.32 | 0.30 | 0.50 |  | 0.30 |', r.stdout)
        self.assertIn('| biquad | msa | 2.40 | yes |', r.stdout)
        self.assertIn('| `div.w` | lat | signal 4 |', r.stdout)

    def test_qemu_check_fails_on_differing_kernel_outputs(self):
        r = self.cli('qemu', '--qemu-user', str(self.fake_qemu(output(kernel_sums=('1', '2')))), '--out', str(self.out))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('scalar and MSA outputs differ: biquad', r.stderr)

    def test_unfinished_run_refused(self):
        r = self.cli('qemu', '--qemu-user', str(self.fake_qemu(output(done=False))), '--out', str(self.out))
        self.assertIn('did not finish', r.stderr)

    def test_device_runs_of_another_binary_refused(self):
        (self.out / 'q2').mkdir()
        (self.out / 'q2/run-1.txt').write_text(output())
        (self.out / 'q2/device.json').write_text(json.dumps(dict(binary_sha256='f' * 64)))
        r = self.cli('summarize', str(self.out))
        self.assertIn('another binary', r.stderr)
        (self.out / 'q2/device.json').write_text(json.dumps(dict(binary_sha256=self.digest, governor='performance')))
        r = self.cli('summarize', str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = json.loads((self.out / 'q2/summary.json').read_text())
        self.assertEqual((summary['meta']['target'], summary['meta']['device']['governor']), ('Q2', 'performance'))


if __name__ == '__main__':
    unittest.main()
