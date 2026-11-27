#!/usr/bin/env python3
"""What the MIPS backend emits for each vector operation with MSA.

The LLVM Project fork prices an IR operation by the instructions selected
for it, weighted with the measured X2000 costs. This script produces that instruction list: one function
per operation and type, compiled with llc, mnemonics counted.

  tests/perf/x2000_cost/lowering.py --llc LLC --out build/x2000-cost/lower [--cpu x2000]

Output: OUT/ops.ll, OUT/ops.s, OUT/ops.txt (one line per function).
"""
import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

INTS = ['<16 x i8>', '<8 x i16>', '<4 x i32>', '<2 x i64>']
FLOATS = ['<4 x float>', '<2 x double>']


def nm(t):
    return t.strip('<>').replace(' x ', '').replace('float', 'f32').replace('double', 'f64')


def mangle(t):
    return 'v' + nm(t)


def elt(t):
    return t.split()[2][:-1]


def count(t):
    return t.split()[0][1:]


def functions():
    fns = []

    def un(name, src, dst, body):
        fns.append(f'define void @{name}(ptr %d, ptr %a, ptr %b) {{\n  %x = load {src}, ptr %a, align 16\n'
                   f'  %y = load {src}, ptr %b, align 16\n{body}\n  store {dst} %r, ptr %d, align 16\n  ret void\n}}\n')

    for t in INTS:
        n, m = nm(t), mangle(t)
        for op in ['add', 'sub', 'mul', 'sdiv', 'udiv', 'srem', 'urem', 'and', 'or', 'xor', 'shl', 'lshr', 'ashr']:
            un(f'{op}_{n}', t, t, f'  %r = {op} {t} %x, %y')
        for op in ['shl', 'lshr', 'ashr']:
            un(f'{op}c_{n}', t, t, f'  %r = {op} {t} %x, splat ({elt(t)} 3)')
        un(f'mulc_{n}', t, t, f'  %r = mul {t} %x, splat ({elt(t)} 77)')
        un(f'sdivc_{n}', t, t, f'  %r = sdiv {t} %x, splat ({elt(t)} 7)')
        un(f'udivc_{n}', t, t, f'  %r = udiv {t} %x, splat ({elt(t)} 7)')
        un(f'sdivpow2_{n}', t, t, f'  %r = sdiv {t} %x, splat ({elt(t)} 8)')
        for cc in ['eq', 'ne', 'slt', 'sgt', 'sle', 'sge', 'ult', 'ugt', 'ule', 'uge']:
            un(f'icmp{cc}_{n}', t, t, f'  %c = icmp {cc} {t} %x, %y\n  %r = sext <{count(t)} x i1> %c to {t}')
        un(f'select_{n}', t, t, f'  %c = icmp slt {t} %x, %y\n  %r = select <{count(t)} x i1> %c, {t} %x, {t} %y')
        un(f'selectz_{n}', t, t, f'  %c = icmp eq {t} %x, zeroinitializer\n  %r = select <{count(t)} x i1> %c, {t} %y, {t} %x')
        for intr in ['smin', 'smax', 'umin', 'umax', 'sadd.sat', 'uadd.sat', 'ssub.sat', 'usub.sat']:
            un(f'{intr.replace(".", "")}_{n}', t, t, f'  %r = call {t} @llvm.{intr}.{m}({t} %x, {t} %y)')
        for intr in ['abs', 'ctlz', 'cttz']:
            un(f'{intr}_{n}', t, t, f'  %r = call {t} @llvm.{intr}.{m}({t} %x, i1 false)')
        for intr in ['ctpop', 'bswap', 'bitreverse']:
            if intr == 'bswap' and 'i8' in t:
                continue
            un(f'{intr}_{n}', t, t, f'  %r = call {t} @llvm.{intr}.{m}({t} %x)')
        un(f'rotl_{n}', t, t, f'  %r = call {t} @llvm.fshl.{m}({t} %x, {t} %x, {t} %y)')
        un(f'rotlc_{n}', t, t, f'  %r = call {t} @llvm.fshl.{m}({t} %x, {t} %x, {t} splat ({elt(t)} 3))')
    for t in FLOATS:
        n, m = nm(t), mangle(t)
        it = '<4 x i32>' if 'float' in t else '<2 x i64>'
        for op in ['fadd', 'fsub', 'fmul', 'fdiv', 'frem']:
            un(f'{op}_{n}', t, t, f'  %r = {op} {t} %x, %y')
        un(f'fneg_{n}', t, t, f'  %r = fneg {t} %x')
        for cc in ['oeq', 'olt', 'ole', 'ogt', 'oge', 'une', 'ult', 'uno', 'ord', 'one', 'ueq']:
            un(f'fcmp{cc}_{n}', t, it, f'  %c = fcmp {cc} {t} %x, %y\n  %r = sext <{count(t)} x i1> %c to {it}')
        un(f'fselect_{n}', t, t, f'  %c = fcmp olt {t} %x, %y\n  %r = select <{count(t)} x i1> %c, {t} %x, {t} %y')
        for intr in ['fabs', 'sqrt', 'floor', 'ceil', 'trunc', 'rint', 'nearbyint', 'round', 'roundeven']:
            un(f'{intr}_{n}', t, t, f'  %r = call {t} @llvm.{intr}.{m}({t} %x)')
        for intr in ['minnum', 'maxnum', 'minimum', 'maximum', 'minimumnum', 'maximumnum', 'copysign']:
            un(f'{intr}_{n}', t, t, f'  %r = call {t} @llvm.{intr}.{m}({t} %x, {t} %y)')
        for intr in ['fmuladd', 'fma']:
            un(f'{intr}_{n}', t, t, f'  %r = call {t} @llvm.{intr}.{m}({t} %x, {t} %y, {t} %x)')
        un(f'fmulfadd_contract_{n}', t, t, f'  %m = fmul contract {t} %x, %y\n  %r = fadd contract {t} %m, %x')
        for c in ['sitofp', 'uitofp']:
            un(f'{c}_{nm(it)}_{n}', it, t, f'  %r = {c} {it} %x to {t}')
        for c in ['fptosi', 'fptoui']:
            un(f'{c}_{n}_{nm(it)}', t, it, f'  %r = {c} {t} %x to {it}')
            un(f'{c}sat_{n}_{nm(it)}', t, it, f'  %r = call {it} @llvm.{c}.sat.{mangle(it)}.{m}({t} %x)')
    casts = [
        ('zext', '<8 x i8>', '<8 x i16>'), ('sext', '<8 x i8>', '<8 x i16>'),
        ('zext', '<4 x i16>', '<4 x i32>'), ('sext', '<4 x i16>', '<4 x i32>'),
        ('zext', '<4 x i8>', '<4 x i32>'), ('sext', '<4 x i8>', '<4 x i32>'),
        ('zext', '<2 x i32>', '<2 x i64>'), ('sext', '<2 x i32>', '<2 x i64>'),
        ('zext', '<2 x i16>', '<2 x i64>'), ('zext', '<2 x i8>', '<2 x i64>'),
        ('zext', '<16 x i8>', '<16 x i16>'), ('sext', '<16 x i8>', '<16 x i16>'),
        ('zext', '<8 x i16>', '<8 x i32>'), ('sext', '<8 x i16>', '<8 x i32>'),
        ('zext', '<16 x i8>', '<16 x i32>'), ('sext', '<16 x i8>', '<16 x i32>'),
        ('zext', '<4 x i32>', '<4 x i64>'), ('sext', '<4 x i32>', '<4 x i64>'),
        ('trunc', '<8 x i16>', '<8 x i8>'), ('trunc', '<4 x i32>', '<4 x i16>'),
        ('trunc', '<4 x i32>', '<4 x i8>'), ('trunc', '<2 x i64>', '<2 x i32>'),
        ('trunc', '<2 x i64>', '<2 x i16>'), ('trunc', '<2 x i64>', '<2 x i8>'),
        ('trunc', '<16 x i16>', '<16 x i8>'), ('trunc', '<8 x i32>', '<8 x i16>'),
        ('trunc', '<16 x i32>', '<16 x i8>'), ('trunc', '<4 x i64>', '<4 x i32>'),
        ('sitofp', '<4 x i16>', '<4 x float>'), ('uitofp', '<4 x i16>', '<4 x float>'),
        ('sitofp', '<4 x i8>', '<4 x float>'), ('uitofp', '<4 x i8>', '<4 x float>'),
        ('sitofp', '<8 x i16>', '<8 x float>'), ('uitofp', '<16 x i8>', '<16 x float>'),
        ('fptosi', '<4 x float>', '<4 x i16>'), ('fptoui', '<4 x float>', '<4 x i8>'),
        ('fptosi', '<8 x float>', '<8 x i16>'), ('fptoui', '<16 x float>', '<16 x i8>'),
        ('fpext', '<2 x float>', '<2 x double>'), ('fpext', '<4 x float>', '<4 x double>'),
        ('fptrunc', '<2 x double>', '<2 x float>'), ('fptrunc', '<4 x double>', '<4 x float>'),
        ('sitofp', '<2 x i32>', '<2 x double>'), ('sitofp', '<4 x i32>', '<4 x double>'),
        ('fptosi', '<2 x double>', '<2 x i32>'), ('fptosi', '<4 x double>', '<4 x i32>'),
        ('sitofp', '<2 x i64>', '<2 x float>'), ('fptosi', '<2 x float>', '<2 x i64>'),
    ]
    for c, s, d in casts:
        un(f'{c}_{nm(s)}_{nm(d)}', s, d, f'  %r = {c} {s} %x to {d}')

    def shuf(name, t, mask, two=True):
        k = len(mask)
        ms = ', '.join('i32 poison' if i < 0 else f'i32 {i}' for i in mask)
        second = '%y' if two else 'poison'
        un(name, t, f'<{k} x {elt(t)}>', f'  %r = shufflevector {t} %x, {t} {second}, <{k} x i32> <{ms}>')

    shuf('shuf_rev_v4i32', '<4 x i32>', [3, 2, 1, 0], False)
    shuf('shuf_rev_v16i8', '<16 x i8>', list(range(15, -1, -1)), False)
    shuf('shuf_rev_v8i16', '<8 x i16>', list(range(7, -1, -1)), False)
    shuf('shuf_rev_v2i64', '<2 x i64>', [1, 0], False)
    shuf('shuf_rev_v4f32', '<4 x float>', [3, 2, 1, 0], False)
    shuf('shuf_bcast_v4i32', '<4 x i32>', [0, 0, 0, 0], False)
    shuf('shuf_bcast_v16i8', '<16 x i8>', [0] * 16, False)
    shuf('shuf_bcast_v4f32', '<4 x float>', [0] * 4, False)
    shuf('shuf_ilvlo_v4i32', '<4 x i32>', [0, 4, 1, 5])
    shuf('shuf_ilvhi_v4i32', '<4 x i32>', [2, 6, 3, 7])
    shuf('shuf_even_v4i32', '<4 x i32>', [0, 2, 4, 6])
    shuf('shuf_odd_v4i32', '<4 x i32>', [1, 3, 5, 7])
    shuf('shuf_even_v16i8', '<16 x i8>', list(range(0, 32, 2)))
    shuf('shuf_perm1_v4i32', '<4 x i32>', [2, 0, 3, 1], False)
    shuf('shuf_perm1_v8i16', '<8 x i16>', [2, 0, 3, 1, 7, 5, 6, 4], False)
    shuf('shuf_perm1_v16i8', '<16 x i8>', [5, 3, 7, 1, 0, 2, 4, 6, 15, 13, 11, 9, 8, 10, 12, 14], False)
    shuf('shuf_perm2_v4i32', '<4 x i32>', [0, 5, 2, 7])
    shuf('shuf_perm2b_v4i32', '<4 x i32>', [1, 6, 3, 4])
    shuf('shuf_perm2_v16i8', '<16 x i8>', [0, 17, 2, 19, 4, 21, 6, 23, 9, 24, 11, 26, 13, 28, 15, 30])
    shuf('shuf_splice_v16i8', '<16 x i8>', list(range(3, 19)))
    shuf('shuf_select_v4i32', '<4 x i32>', [0, 5, 6, 3])
    shuf('shuf_concat_v4i32', '<4 x i32>', list(range(8)))
    shuf('shuf_extract_hi_v4i32', '<4 x i32>', [2, 3], False)
    shuf('shuf_insertsub_v4i32', '<4 x i32>', [0, 1, 4, 5])
    shuf('shuf_perm1_v2f64', '<2 x double>', [1, 0], False)
    shuf('shuf_perm1_v4f32', '<4 x float>', [2, 0, 3, 1], False)
    shuf('shuf_perm2_v4f32', '<4 x float>', [1, 6, 3, 4])
    shuf('shuf_deint3_v16i8', '<16 x i8>', [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, -1, -1, -1, -1, -1])

    # Vectors shorter than a register: what their loads and stores become.
    for t in ['<8 x i8>', '<4 x i8>', '<4 x i16>', '<2 x i16>', '<2 x i32>']:
        un(f'short_add_{nm(t)}', t, t, f'  %r = add {t} %x, %y')
    un('short_fadd_2f32', '<2 x float>', '<2 x float>', '  %r = fadd <2 x float> %x, %y')

    for t in INTS + FLOATS:
        n, m, s = nm(t), mangle(t), elt(t)
        load = f'  %x = load {t}, ptr %a, align 16\n'
        fns.append(f'define {s} @extract0_{n}(ptr %a) {{\n{load}  %r = extractelement {t} %x, i32 0\n  ret {s} %r\n}}\n')
        fns.append(f'define {s} @extract1_{n}(ptr %a) {{\n{load}  %r = extractelement {t} %x, i32 1\n  ret {s} %r\n}}\n')
        fns.append(f'define void @insert1_{n}(ptr %a, {s} %v) {{\n{load}  %r = insertelement {t} %x, {s} %v, i32 1\n'
                   f'  store {t} %r, ptr %a, align 16\n  ret void\n}}\n')
        splat = (f'  %i = insertelement {t} poison, {s} %v, i32 0\n'
                 f'  %r = shufflevector {t} %i, {t} poison, <{count(t)} x i32> zeroinitializer\n'
                 f'  store {t} %r, ptr %a, align 16\n  ret void\n}}\n')
        fns.append(f'define void @splat_{n}(ptr %a, {s} %v) {{\n' + splat)
        fns.append(f'define void @loadsplat_{n}(ptr %a, ptr %p) {{\n  %v = load {s}, ptr %p\n' + splat)
        build = ''.join(f'  %e{i} = load {s}, ptr %p{i}\n' for i in range(int(count(t))))
        prev = 'poison'
        for i in range(int(count(t))):
            build += f'  %v{i} = insertelement {t} {prev}, {s} %e{i}, i32 {i}\n'
            prev = f'%v{i}'
        args = ', '.join(f'ptr %p{i}' for i in range(int(count(t))))
        if int(count(t)) <= 4:
            fns.append(f'define void @build_{n}(ptr %a, {args}) {{\n{build}  store {t} {prev}, ptr %a, align 16\n  ret void\n}}\n')
        if t in INTS:
            for r in ['add', 'mul', 'and', 'or', 'xor', 'smax', 'smin', 'umax', 'umin']:
                fns.append(f'define {s} @red{r}_{n}(ptr %a) {{\n{load}  %r = call {s} @llvm.vector.reduce.{r}.{m}({t} %x)\n  ret {s} %r\n}}\n')
        else:
            fns.append(f'define {s} @redfadd_{n}(ptr %a) {{\n{load}  %r = call reassoc {s} @llvm.vector.reduce.fadd.{m}({s} 0.0, {t} %x)\n  ret {s} %r\n}}\n')
            fns.append(f'define {s} @redfaddordered_{n}(ptr %a) {{\n{load}  %r = call {s} @llvm.vector.reduce.fadd.{m}({s} 0.0, {t} %x)\n  ret {s} %r\n}}\n')
            fns.append(f'define {s} @redfmax_{n}(ptr %a) {{\n{load}  %r = call nnan {s} @llvm.vector.reduce.fmax.{m}({t} %x)\n  ret {s} %r\n}}\n')
    fns.append('define void @unaligned_v4i32(ptr %d, ptr %a) {\n  %x = load <4 x i32>, ptr %a, align 1\n'
               '  store <4 x i32> %x, ptr %d, align 1\n  ret void\n}\n')
    fns.append('define void @maskedload_v4i32(ptr %d, ptr %a, ptr %m) {\n  %k = load <4 x i32>, ptr %m, align 16\n'
               '  %c = icmp slt <4 x i32> %k, zeroinitializer\n'
               '  %x = call <4 x i32> @llvm.masked.load.v4i32.p0(ptr %a, i32 4, <4 x i1> %c, <4 x i32> poison)\n'
               '  store <4 x i32> %x, ptr %d, align 16\n  ret void\n}\n')
    return fns


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--llc', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--cpu', default='mips32r5')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    ll, asm = a.out / 'ops.ll', a.out / 'ops.s'
    ll.write_text('target datalayout = "e-m:m-p:32:32-i8:8:32-i16:16:32-i64:64-n32-S64"\n'
                  'target triple = "mipsel-unknown-linux-gnu"\n\n' + '\n'.join(functions()))
    p = subprocess.run([str(a.llc), '-O2', '-mcpu=' + a.cpu, '-mattr=+msa,+fp64,+nan2008,+abs2008',
                        '-relocation-model=pic', str(ll), '-o', str(asm)], capture_output=True, text=True)
    sys.stderr.write(p.stderr[:3000])
    if p.returncode:
        return p.returncode
    cur, out = None, collections.OrderedDict()
    for line in asm.read_text().splitlines():
        m = re.match(r'^([A-Za-z_][A-Za-z_0-9.]*):', line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        m = re.match(r'^\s+([a-z_0-9.]+)(\s|$)', line)
        if cur and m and not line.strip().startswith('.'):
            out[cur].append(m.group(1))
    with (a.out / 'ops.txt').open('w') as f:
        for name, insns in out.items():
            body = [i for i in insns if i not in ('jr', 'nop')]
            c = collections.Counter(body)
            f.write(f'{name:34} n={len(body):3}  ' + ' '.join(f'{k}*{v}' if v > 1 else k for k, v in c.items()) + '\n')
    print(a.out / 'ops.txt')
    return 0


if __name__ == '__main__':
    sys.exit(main())
