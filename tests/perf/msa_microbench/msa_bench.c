/* X2000 (XBurst II) MSA and scalar instruction costs, measured on the Q2.
 *
 * Every benchmark is one inline-asm loop: a setup, then a block of 64 or 96
 * copies of the instruction and a 3-instruction loop tail (addiu, bnez, nop).
 * Variants of one instruction:
 *   lat  one dependent chain (destination feeds the next source): latency.
 *   t4, t8, t12  that many independent chains: reciprocal throughput when the
 *        chain count exceeds latency x issue width.
 *   ind  12 destinations, sources never written: reciprocal throughput
 *        without a chain (accumulating instructions still chain on wd).
 *   acc  for accumulating instructions, one chain through the accumulator
 *        only.
 * Output is ns per instruction (per repetition for the named multi-instruction
 * sequences, per element for the kernels); the runner
 * (tests/perf/msa_microbench.py) converts to cycles with the cpufreq value
 * printed in the header. Constants live in one table reached through %[c],
 * so the asm has no symbol references.
 *
 * Build: see msa_microbench.py (GCC, -march=mips32r5 -mfp64 -mnan=2008 -mmsa,
 * static). Runs on one CPU (affinity 0), needs no arguments.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

struct bench {
    const char *name, *variant;
    void (*fn)(long, void *, void *, const void *);
    int ops;  /* instructions (or elements, or bytes) per loop iteration */
    int poff; /* byte offset of %[p] from a page boundary */
    int fill; /* 0: %[p] is the work buffer; else kernel with this source data */
};

#define CLOB "$2", "$3", "$8", "$9", "$10", "$11", "$12", "$13", "$14", "$15", "$16", "$17", "$24", "$25", \
    "$f0", "$f1", "$f2", "$f3", "$f4", "$f5", "$f6", "$f7", "$f8", "$f9", "$f10", "$f11", "$f12", "$f13", \
    "$f14", "$f15", "$f16", "$f17", "$f18", "$f19", "$f20", "$f21", "$f22", "$f23", "$f24", "$f25", "$f26", \
    "$f27", "$f28", "$f29", "$f30", "$f31", "memory"

#define BENCH_(sec, id, nm, var, ops_, poff_, fill_, setup, block, post)                                   \
    static __attribute__((noinline)) void fn_##id(long n, void *p, void *q, const void *c) {              \
        __asm__ volatile(setup "1:\n" block "addiu %[n],%[n],-1\nbnez %[n],1b\n" post                      \
                         : [n] "+r"(n) : [p] "r"(p), [q] "r"(q), [c] "r"(c) : CLOB);                       \
    }                                                                                                      \
    static const struct bench b_##id __attribute__((used, section(sec), aligned(4))) =                     \
        {nm, var, fn_##id, ops_, poff_, fill_};
#define BENCH_X(sec, id, ...) BENCH_(sec, id, __VA_ARGS__)
#define BENCH(...) BENCH_X("benchtab", __COUNTER__, __VA_ARGS__)
#define MEMFN(...) BENCH_X("memtab", __COUNTER__, __VA_ARGS__)

#define R2(x) x x
#define R4(x) R2(R2(x))
#define R8(x) R4(R2(x))
#define R12(x) R8(x) R4(x)
#define R16(x) R8(R2(x))
#define R24(x) R12(R2(x))
#define R32(x) R16(R2(x))
#define R64(x) R32(R2(x))
#define C4(F, x, y, z) F(x, y, z, 0, 0) F(x, y, z, 1, 1) F(x, y, z, 2, 2) F(x, y, z, 3, 3)
#define C8(F, x, y, z) C4(F, x, y, z) F(x, y, z, 4, 4) F(x, y, z, 5, 5) F(x, y, z, 6, 6) F(x, y, z, 7, 7)
#define C12(F, x, y, z) C8(F, x, y, z) F(x, y, z, 8, 8) F(x, y, z, 9, 9) F(x, y, z, 10, 10) F(x, y, z, 11, 11)
#define I12(F, x, y, z)                                                                                   \
    F(x, y, z, 0, 16) F(x, y, z, 1, 16) F(x, y, z, 2, 16) F(x, y, z, 3, 16) F(x, y, z, 4, 16)             \
    F(x, y, z, 5, 16) F(x, y, z, 6, 16) F(x, y, z, 7, 16) F(x, y, z, 8, 16) F(x, y, z, 9, 16)             \
    F(x, y, z, 10, 16) F(x, y, z, 11, 16)

/* Chain registers 0..11 and the constant source 16, per register file. */
#define GR_0 "$8"
#define GR_1 "$9"
#define GR_2 "$10"
#define GR_3 "$11"
#define GR_4 "$12"
#define GR_5 "$13"
#define GR_6 "$14"
#define GR_7 "$15"
#define GR_8 "$24"
#define GR_9 "$25"
#define GR_10 "$2"
#define GR_11 "$3"
#define GR_16 "$16"
#define GR(d) GR_##d

/* Instruction forms: (pre, mid, post, d, s). */
#define V(pre, mid, post, d, s) pre " $w" #d mid "$w" #s post "\n"       /* vector d <- vector s */
#define VD(pre, mid, post, d, s) pre " $w" #d post "\n"                   /* vector d only */
#define S(pre, mid, post, d, s) pre " $f" #d mid "$f" #s post "\n"       /* FPR d <- FPR s */
#define G(pre, mid, post, d, s) pre " " GR(d) mid GR(s) post "\n"         /* GPR d <- GPR s */
#define GV(pre, mid, post, d, s) pre " " GR(d) mid "$w" #s post "\n"      /* GPR d <- vector s */
#define VG(pre, mid, post, d, s) pre " $w" #d mid GR(s) post "\n"         /* vector d <- GPR s */
#define SG(pre, mid, post, d, s) pre " " GR(s) ",$f" #d "\n"              /* mtc1 */
#define GS(pre, mid, post, d, s) pre " " GR(d) ",$f" #s "\n"              /* mfc1 */
#define GM(pre, mid, post, d, s) pre " " GR(d) "," #d "*32(%[p])\n"       /* GPR load */
#define GMS(pre, mid, post, d, s) pre " $16," #d "*32(%[p])\n"            /* GPR store */
#define SM(pre, mid, post, d, s) pre " $f" #d "," #d "*32(%[p])\n"        /* FPR load */
#define SMS(pre, mid, post, d, s) pre " $f16," #d "*32(%[p])\n"           /* FPR store */
#define VM(pre, mid, post, d, s) pre " $w" #d "," #d "*32(%[p])\n"        /* vector load */
#define VMS(pre, mid, post, d, s) pre " $w16," #d "*32(%[p])\n"           /* vector store */
#define GM0(pre, mid, post, d, s) pre " " GR(d) ",0(%[p])\n"             /* GPR load, one address */
#define GMS0(pre, mid, post, d, s) pre " $16,0(%[p])\n"                  /* GPR store, one address */
#define VM0(pre, mid, post, d, s) pre " $w" #d ",0(%[p])\n"               /* vector load, one address */
#define VMS0(pre, mid, post, d, s) pre " $w16,0(%[p])\n"                  /* vector store, one address */

#define LT(nm, poff, setup, F, x, y, z)                                                                   \
    BENCH(nm, "lat", 64, poff, 0, setup, R64(F(x, y, z, 0, 0)), "")                                        \
    BENCH(nm, "t4", 96, poff, 0, setup, R24(C4(F, x, y, z)), "")                                           \
    BENCH(nm, "t8", 96, poff, 0, setup, R12(C8(F, x, y, z)), "")                                           \
    BENCH(nm, "t12", 96, poff, 0, setup, R8(C12(F, x, y, z)), "")                                          \
    BENCH(nm, "ind", 96, poff, 0, setup, R8(I12(F, x, y, z)), "")
#define ACC(nm, setup, F, x, y, z) BENCH(nm, "acc", 64, 0, 0, setup, R64(F(x, y, z, 0, 16)), "")
#define LTA(nm, setup, F, x, y, z) LT(nm, 0, setup, F, x, y, z) ACC(nm, setup, F, x, y, z)
#define TP(nm, poff, setup, F, x, y, z) BENCH(nm, "ind", 96, poff, 0, setup, R8(I12(F, x, y, z)), "")
#define CUSTOM(nm, var, ops, setup, block) BENCH(nm, var, ops, 0, 0, setup, block, "")

/* Constant classes in the table: five vectors each (chain start, w16..w19). */
#define KI 0    /* i32 3 | 5 | 2 | bytes 0..15 | 0 */
#define KF 80   /* f32 1.5 | 1.25 | 0.0 | 1.0 | 1.37 */
#define KD 160  /* f64 1.5 | 1.25 | 0.0 | 1.0 | 1.37 */
#define KZ 240  /* 0 | f32 1.25 | 0 | 0 | f32 -1.0 */
#define KZD 320 /* 0 | f64 1.25 | 0 | 0 | f64 -1.0 */
#define KN 400  /* f32 denormal | denormal | 0.0 | 1.0 | 1.37 */
#define KK 480  /* 0 | f32 1/32768 | 0 | f32 1.0 | 0 */
#define KP 560  /* 0 | bytes 0xff | 0 | RGB->RGBA control | 0 */
#define STR_(x) #x
#define STR(x) STR_(x)
#define LDW(r, b, o) "ld.w $w" #r "," STR(b) "+" #o "(%[c])\n"
#define SETV(b)                                                                                           \
    LDW(0, b, 0) LDW(1, b, 0) LDW(2, b, 0) LDW(3, b, 0) LDW(4, b, 0) LDW(5, b, 0) LDW(6, b, 0)             \
    LDW(7, b, 0) LDW(8, b, 0) LDW(9, b, 0) LDW(10, b, 0) LDW(11, b, 0) LDW(12, b, 0) LDW(13, b, 0)        \
    LDW(16, b, 16) LDW(17, b, 32) LDW(18, b, 48) LDW(19, b, 64)
#define LDS(i, r, b, o) i " $f" #r "," STR(b) "+" #o "(%[c])\n"
#define SETFP(i, b)                                                                                       \
    LDS(i, 0, b, 0) LDS(i, 1, b, 0) LDS(i, 2, b, 0) LDS(i, 3, b, 0) LDS(i, 4, b, 0) LDS(i, 5, b, 0)        \
    LDS(i, 6, b, 0) LDS(i, 7, b, 0) LDS(i, 8, b, 0) LDS(i, 9, b, 0) LDS(i, 10, b, 0) LDS(i, 11, b, 0)     \
    LDS(i, 16, b, 16) LDS(i, 17, b, 32) LDS(i, 18, b, 48) LDS(i, 19, b, 64)
#define SETS(b) SETFP("lwc1", b)
#define SETD(b) SETFP("ldc1", b)
#define SETG "li $8,3\nli $9,3\nli $10,3\nli $11,3\nli $12,3\nli $13,3\nli $14,3\nli $15,3\n"             \
             "li $24,3\nli $25,3\nli $2,3\nli $3,3\nli $16,5\nli $17,3\n"

/* ---- loop overhead ---- */
CUSTOM("nop", "x64", 64, "", R64("nop\n"))
CUSTOM("nop", "x96", 96, "", R32("nop\nnop\nnop\n"))

/* ---- scalar integer ---- */
LT("addu", 0, SETG, G, "addu", ",", ",$17")
LT("subu", 0, SETG, G, "subu", ",", ",$17")
LT("and", 0, SETG, G, "and", ",", ",$17")
LT("sll", 0, SETG, G, "sll", ",", ",3")
LT("slt", 0, SETG, G, "slt", ",", ",$17")
LT("movn", 0, SETG, G, "movn", ",", ",$17")
LT("mul", 0, SETG, G, "mul", ",", ",$17")
CUSTOM("div+mflo+or", "lat", 16, SETG "lui $16,0x7fff\nmove $8,$16\n", R16("div $0,$8,$17\nmflo $8\nor $8,$8,$16\n"))
CUSTOM("lw (pointer chase)", "lat", 64, "sw %[p],0(%[p])\nmove $8,%[p]\n", R64("lw $8,0($8)\n"))
CUSTOM("sw+lw (same address)", "lat", 32, SETG, R32("sw $8,0(%[p])\nlw $8,0(%[p])\n"))
TP("lw", 0, SETG, GM, "lw", "", "")
TP("lbu", 0, SETG, GM, "lbu", "", "")
TP("sw", 0, SETG, GMS, "sw", "", "")
TP("sb", 0, SETG, GMS, "sb", "", "")
TP("lw (one address)", 0, SETG, GM0, "lw", "", "")
TP("sw (one address)", 0, SETG, GMS0, "sw", "", "")

/* ---- scalar float ---- */
LT("add.s", 0, SETS(KF), S, "add.s", ",", ",$f17")
LT("mul.s", 0, SETS(KF), S, "mul.s", ",", ",$f18")
LT("madd.s (addend)", 0, SETS(KF), S, "madd.s", ",", ",$f17,$f18")
LT("madd.s (multiplicand)", 0, SETS(KF), S, "madd.s", ",$f17,", ",$f18")
LT("div.s", 0, SETS(KF), S, "div.s", ",$f19,", "")
LT("sqrt.s", 0, SETS(KF), S, "sqrt.s", ",", "")
LT("mov.s", 0, SETS(KF), S, "mov.s", ",", "")
LT("add.d", 0, SETD(KD), S, "add.d", ",", ",$f17")
LT("mul.d", 0, SETD(KD), S, "mul.d", ",", ",$f18")
LT("madd.d (addend)", 0, SETD(KD), S, "madd.d", ",", ",$f17,$f18")
LT("madd.d (multiplicand)", 0, SETD(KD), S, "madd.d", ",$f17,", ",$f18")
LT("div.d", 0, SETD(KD), S, "div.d", ",$f19,", "")
LT("sqrt.d", 0, SETD(KD), S, "sqrt.d", ",", "")
LT("cvt.s.w", 0, SETS(KZ), S, "cvt.s.w", ",", "")
LT("cvt.d.w", 0, SETS(KZ), S, "cvt.d.w", ",", "")
LT("trunc.w.s", 0, SETS(KZ), S, "trunc.w.s", ",", "")
LT("cvt.w.s", 0, SETS(KZ), S, "cvt.w.s", ",", "")
LT("cvt.d.s", 0, SETS(KZ), S, "cvt.d.s", ",", "")
LT("trunc.w.d", 0, SETD(KZD), S, "trunc.w.d", ",", "")
LT("cvt.s.d", 0, SETD(KZD), S, "cvt.s.d", ",", "")
CUSTOM("cvt.s.w+trunc.w.s (value 1.0)", "lat", 32, SETS(KF) "mov.s $f0,$f18\n", R32("trunc.w.s $f0,$f0\ncvt.s.w $f0,$f0\n"))
CUSTOM("cvt.d.s+cvt.s.d (value 1.5)", "lat", 32, SETS(KF), R32("cvt.d.s $f0,$f0\ncvt.s.d $f0,$f0\n"))
TP("mtc1", 0, SETG, SG, "mtc1", "", "")
TP("mfc1", 0, SETG SETS(KF), GS, "mfc1", "", "")
CUSTOM("mtc1+mfc1", "lat", 32, SETG, R32("mtc1 $8,$f0\nmfc1 $8,$f0\n"))
TP("lwc1", 0, "", SM, "lwc1", "", "")
TP("ldc1", 0, "", SM, "ldc1", "", "")
TP("swc1", 0, SETS(KF), SMS, "swc1", "", "")
TP("sdc1", 0, SETD(KD), SMS, "sdc1", "", "")
CUSTOM("add.s (denormal operand)", "lat", 64, SETV(KN), R64("add.s $f0,$f0,$f17\n"))
CUSTOM("mul.s (denormal operand)", "lat", 64, SETV(KN), R64("mul.s $f0,$f0,$f18\n"))

/* ---- MSA integer ---- */
LT("addv.b", 0, SETV(KI), V, "addv.b", ",", ",$w17")
LT("addv.h", 0, SETV(KI), V, "addv.h", ",", ",$w17")
LT("addv.w", 0, SETV(KI), V, "addv.w", ",", ",$w17")
LT("addv.d", 0, SETV(KI), V, "addv.d", ",", ",$w17")
LT("addvi.w", 0, SETV(KI), V, "addvi.w", ",", ",1")
LT("subv.h", 0, SETV(KI), V, "subv.h", ",", ",$w17")
LT("subv.w", 0, SETV(KI), V, "subv.w", ",", ",$w17")
LT("adds_s.h", 0, SETV(KI), V, "adds_s.h", ",", ",$w17")
LT("adds_u.b", 0, SETV(KI), V, "adds_u.b", ",", ",$w17")
LT("subs_u.b", 0, SETV(KI), V, "subs_u.b", ",", ",$w17")
LT("aver_u.b", 0, SETV(KI), V, "aver_u.b", ",", ",$w17")
LT("asub_u.b", 0, SETV(KI), V, "asub_u.b", ",", ",$w17")
LT("hadd_u.h", 0, SETV(KI), V, "hadd_u.h", ",", ",$w17")
LT("mulv.b", 0, SETV(KI), V, "mulv.b", ",", ",$w17")
LT("mulv.h", 0, SETV(KI), V, "mulv.h", ",", ",$w17")
LT("mulv.w", 0, SETV(KI), V, "mulv.w", ",", ",$w17")
LT("mulv.d", 0, SETV(KI), V, "mulv.d", ",", ",$w17")
LT("mul_q.h", 0, SETV(KI), V, "mul_q.h", ",", ",$w17")
LT("mulr_q.w", 0, SETV(KI), V, "mulr_q.w", ",", ",$w17")
LTA("maddv.h", SETV(KI), V, "maddv.h", ",", ",$w17")
LTA("maddv.w", SETV(KI), V, "maddv.w", ",", ",$w17")
LT("dotp_s.h", 0, SETV(KI), V, "dotp_s.h", ",", ",$w17")
LT("dotp_s.w", 0, SETV(KI), V, "dotp_s.w", ",", ",$w17")
LT("dotp_u.h", 0, SETV(KI), V, "dotp_u.h", ",", ",$w17")
LTA("dpadd_s.h", SETV(KI), V, "dpadd_s.h", ",", ",$w17")
LTA("dpadd_s.w", SETV(KI), V, "dpadd_s.w", ",", ",$w17")
LTA("dpadd_s.d", SETV(KI), V, "dpadd_s.d", ",", ",$w17")
LT("div_s.w", 0, SETV(KI), V, "div_s.w", ",", ",$w17")
LT("min_s.w", 0, SETV(KI), V, "min_s.w", ",", ",$w17")
LT("max_s.w", 0, SETV(KI), V, "max_s.w", ",", ",$w17")
LT("max_s.h", 0, SETV(KI), V, "max_s.h", ",", ",$w17")
LT("min_u.b", 0, SETV(KI), V, "min_u.b", ",", ",$w17")
LT("maxi_s.w", 0, SETV(KI), V, "maxi_s.w", ",", ",0")
LT("sat_s.w", 0, SETV(KI), V, "sat_s.w", ",", ",15")
LT("sat_s.h", 0, SETV(KI), V, "sat_s.h", ",", ",7")
LT("sat_u.h", 0, SETV(KI), V, "sat_u.h", ",", ",7")
LT("srai.w", 0, SETV(KI), V, "srai.w", ",", ",3")
LT("srai.h", 0, SETV(KI), V, "srai.h", ",", ",3")
LT("slli.w", 0, SETV(KI), V, "slli.w", ",", ",3")
LT("srli.w", 0, SETV(KI), V, "srli.w", ",", ",3")
LT("srli.h", 0, SETV(KI), V, "srli.h", ",", ",3")
LT("srari.w", 0, SETV(KI), V, "srari.w", ",", ",3")
LT("srl.w", 0, SETV(KI), V, "srl.w", ",", ",$w17")
LT("sra.h", 0, SETV(KI), V, "sra.h", ",", ",$w17")
LT("sll.b", 0, SETV(KI), V, "sll.b", ",", ",$w17")
LT("and.v", 0, SETV(KI), V, "and.v", ",", ",$w17")
LT("or.v", 0, SETV(KI), V, "or.v", ",", ",$w17")
LT("xor.v", 0, SETV(KI), V, "xor.v", ",", ",$w17")
LT("nor.v", 0, SETV(KI), V, "nor.v", ",", ",$w17")
LT("andi.b", 0, SETV(KI), V, "andi.b", ",", ",15")
LTA("bsel.v", SETV(KI), V, "bsel.v", ",", ",$w17")
LTA("bmnz.v", SETV(KI), V, "bmnz.v", ",", ",$w17")
LT("ceq.w", 0, SETV(KI), V, "ceq.w", ",", ",$w17")
LT("ceq.b", 0, SETV(KI), V, "ceq.b", ",", ",$w17")
LT("clt_s.w", 0, SETV(KI), V, "clt_s.w", ",", ",$w17")
LT("clt_u.b", 0, SETV(KI), V, "clt_u.b", ",", ",$w17")
LT("cle_s.h", 0, SETV(KI), V, "cle_s.h", ",", ",$w17")
LT("clti_s.w", 0, SETV(KI), V, "clti_s.w", ",", ",0")

/* ---- MSA float ---- */
LT("fadd.w", 0, SETV(KF), V, "fadd.w", ",", ",$w17")
LT("fadd.d", 0, SETV(KD), V, "fadd.d", ",", ",$w17")
LT("fsub.w", 0, SETV(KF), V, "fsub.w", ",", ",$w17")
LT("fmul.w", 0, SETV(KF), V, "fmul.w", ",", ",$w18")
LT("fmul.d", 0, SETV(KD), V, "fmul.d", ",", ",$w18")
LTA("fmadd.w", SETV(KF), V, "fmadd.w", ",", ",$w17")
LTA("fmadd.d", SETV(KD), V, "fmadd.d", ",", ",$w17")
LT("fdiv.w", 0, SETV(KF), V, "fdiv.w", ",$w19,", "")
LT("fdiv.d", 0, SETV(KD), V, "fdiv.d", ",$w19,", "")
LT("fsqrt.w", 0, SETV(KF), V, "fsqrt.w", ",", "")
LT("fsqrt.d", 0, SETV(KD), V, "fsqrt.d", ",", "")
LT("frcp.w", 0, SETV(KF), V, "frcp.w", ",", "")
LT("frsqrt.w", 0, SETV(KF), V, "frsqrt.w", ",", "")
LT("fmax.w", 0, SETV(KF), V, "fmax.w", ",", ",$w17")
LT("fmax.d", 0, SETV(KD), V, "fmax.d", ",", ",$w17")
LT("fmin.w", 0, SETV(KF), V, "fmin.w", ",", ",$w18")
LT("frint.w", 0, SETV(KF), V, "frint.w", ",", "")
LT("ftint_s.w", 0, SETV(KZ), V, "ftint_s.w", ",", "")
LT("ftint_s.d", 0, SETV(KZD), V, "ftint_s.d", ",", "")
LT("ftint_u.w", 0, SETV(KZ), V, "ftint_u.w", ",", "")
LT("ftrunc_s.w", 0, SETV(KZ), V, "ftrunc_s.w", ",", "")
LT("ftrunc_s.d", 0, SETV(KZD), V, "ftrunc_s.d", ",", "")
LT("ffint_s.w", 0, SETV(KZ), V, "ffint_s.w", ",", "")
LT("ffint_s.d", 0, SETV(KZD), V, "ffint_s.d", ",", "")
LT("ffint_u.w", 0, SETV(KZ), V, "ffint_u.w", ",", "")
LT("fexdo.w", 0, SETV(KZD), V, "fexdo.w", ",", ",$w17")
LT("fexupr.d", 0, SETV(KZ), V, "fexupr.d", ",", "")
LT("fexupl.d", 0, SETV(KZ), V, "fexupl.d", ",", "")
LT("ffqr.w", 0, SETV(KZ), V, "ffqr.w", ",", "")
LT("ffql.w", 0, SETV(KZ), V, "ffql.w", ",", "")
LT("ftq.h", 0, SETV(KZ), V, "ftq.h", ",", ",$w17")
LT("fcle.w", 0, SETV(KZ), V, "fcle.w", ",", ",$w19")
LT("fcle.d", 0, SETV(KZD), V, "fcle.d", ",", ",$w19")
LT("fclt.w", 0, SETV(KZ), V, "fclt.w", ",", ",$w17")
CUSTOM("fsqrt.w+fmul.w (value 1.88)", "lat", 32, SETV(KF), R32("fsqrt.w $w0,$w0\nfmul.w $w0,$w0,$w19\n"))
CUSTOM("fsqrt.d+fmul.d (value 1.88)", "lat", 32, SETV(KD), R32("fsqrt.d $w0,$w0\nfmul.d $w0,$w0,$w19\n"))
CUSTOM("ftint_s.w+ffint_s.w (value 2.0)", "lat", 32, SETV(KF), R32("ftint_s.w $w0,$w0\nffint_s.w $w0,$w0\n"))
CUSTOM("fexupr.d+fexdo.w (value 1.5)", "lat", 32, SETV(KF), R32("fexupr.d $w0,$w0\nfexdo.w $w0,$w0,$w0\n"))
CUSTOM("fadd.w (denormal operand)", "lat", 64, SETV(KN), R64("fadd.w $w0,$w0,$w17\n"))
CUSTOM("fmul.w (denormal operand)", "lat", 64, SETV(KN), R64("fmul.w $w0,$w0,$w18\n"))
BENCH("fadd.w (denormal operand, MSACSR.FS=1)", "lat", 64, 0, 0,
      SETV(KN) "cfcmsa $8,$1\nlui $9,0x0100\nor $9,$9,$8\nctcmsa $1,$9\n", R64("fadd.w $w0,$w0,$w17\n"), "ctcmsa $1,$8\n")
BENCH("fmul.w (denormal operand, MSACSR.FS=1)", "lat", 64, 0, 0,
      SETV(KN) "cfcmsa $8,$1\nlui $9,0x0100\nor $9,$9,$8\nctcmsa $1,$9\n", R64("fmul.w $w0,$w0,$w18\n"), "ctcmsa $1,$8\n")

/* ---- data movement ---- */
LT("move.v", 0, SETV(KI), V, "move.v", ",", "")
TP("ldi.b", 0, SETV(KI), VD, "ldi.b", "", ",7")
TP("ldi.w", 0, SETV(KI), VD, "ldi.w", "", ",7")
TP("ldi.d", 0, SETV(KI), VD, "ldi.d", "", ",7")
TP("fill.b", 0, SETV(KI) SETG, VG, "fill.b", ",", "")
TP("fill.h", 0, SETV(KI) SETG, VG, "fill.h", ",", "")
TP("fill.w", 0, SETV(KI) SETG, VG, "fill.w", ",", "")
TP("copy_s.w", 0, SETV(KI) SETG, GV, "copy_s.w", ",", "[0]")
TP("copy_s.h", 0, SETV(KI) SETG, GV, "copy_s.h", ",", "[1]")
TP("copy_u.b", 0, SETV(KI) SETG, GV, "copy_u.b", ",", "[1]")
TP("insert.w", 0, SETV(KI) SETG, VD, "insert.w", "", "[0],$16")
TP("insert.b", 0, SETV(KI) SETG, VD, "insert.b", "", "[5],$16")
CUSTOM("insert.w", "lat", 64, SETV(KI) SETG, R64("insert.w $w0[1],$16\n"))
CUSTOM("fill.w+copy_s.w", "lat", 32, SETV(KI) SETG, R32("fill.w $w0,$8\ncopy_s.w $8,$w0[0]\n"))
CUSTOM("insert.w+copy_s.w", "lat", 32, SETV(KI) SETG, R32("insert.w $w0[0],$8\ncopy_s.w $8,$w0[0]\n"))
CUSTOM("4x insert.w+copy_s.w", "lat", 16, SETV(KI) SETG,
       R16("insert.w $w0[0],$8\ninsert.w $w0[1],$8\ninsert.w $w0[2],$8\ninsert.w $w0[3],$8\ncopy_s.w $8,$w0[2]\n"))
CUSTOM("4x sw+ld.w+copy_s.w", "lat", 16, SETV(KI) SETG,
       R16("sw $8,0(%[p])\nsw $8,4(%[p])\nsw $8,8(%[p])\nsw $8,12(%[p])\nld.w $w0,0(%[p])\ncopy_s.w $8,$w0[2]\n"))
CUSTOM("sw+ld.w+copy_s.w", "lat", 16, SETV(KI) SETG, R16("sw $8,0(%[p])\nld.w $w0,0(%[p])\ncopy_s.w $8,$w0[0]\n"))
CUSTOM("st.w+lw+fill.w", "lat", 16, SETV(KI) SETG, R16("st.w $w0,0(%[p])\nlw $8,4(%[p])\nfill.w $w0,$8\n"))
CUSTOM("st.w+ld.w (same address)", "lat", 32, SETV(KI), R32("st.w $w0,0(%[p])\nld.w $w0,0(%[p])\n"))
BENCH("st.w+ld.w (same address, +1)", "lat", 32, 1, 0, SETV(KI), R32("st.w $w0,0(%[p])\nld.w $w0,0(%[p])\n"), "")
CUSTOM("st.w+ld.w (load 4 bytes later)", "lat", 32, SETV(KI), R32("st.w $w0,0(%[p])\nld.w $w0,4(%[p])\n"))
LT("insve.w", 0, SETV(KI), V, "insve.w", "[1],", "[0]")
LT("splati.b", 0, SETV(KI), V, "splati.b", ",", "[1]")
LT("splati.h", 0, SETV(KI), V, "splati.h", ",", "[1]")
LT("splati.w", 0, SETV(KI), V, "splati.w", ",", "[1]")
LT("splati.d", 0, SETV(KI), V, "splati.d", ",", "[1]")
LT("splat.w", 0, SETV(KI) SETG "li $17,1\n", V, "splat.w", ",", "[$17]")
LT("shf.b", 0, SETV(KI), V, "shf.b", ",", ",0x1b")
LT("shf.h", 0, SETV(KI), V, "shf.h", ",", ",0x1b")
LT("shf.w", 0, SETV(KI), V, "shf.w", ",", ",0x1b")
LT("vshf.b (control)", 0, SETV(KI), VD, "vshf.b", "", ",$w16,$w17")
LT("vshf.h (control)", 0, SETV(KI), VD, "vshf.h", "", ",$w16,$w17")
LT("vshf.w (control)", 0, SETV(KI), VD, "vshf.w", "", ",$w16,$w17")
CUSTOM("move.v+vshf.b (data)", "lat", 32, SETV(KI),
       R16("move.v $w12,$w18\nvshf.b $w12,$w0,$w0\nmove.v $w0,$w18\nvshf.b $w0,$w12,$w12\n"))
CUSTOM("move.v+vshf.b (data)", "t4", 48, SETV(KI),
       R12("move.v $w12,$w18\nvshf.b $w12,$w0,$w0\nmove.v $w13,$w18\nvshf.b $w13,$w1,$w1\n"
           "move.v $w0,$w18\nvshf.b $w0,$w2,$w2\nmove.v $w1,$w18\nvshf.b $w1,$w3,$w3\n"))
LT("ilvr.b", 0, SETV(KI), V, "ilvr.b", ",", ",$w17")
LT("ilvr.h", 0, SETV(KI), V, "ilvr.h", ",", ",$w17")
LT("ilvr.w", 0, SETV(KI), V, "ilvr.w", ",", ",$w17")
LT("ilvr.d", 0, SETV(KI), V, "ilvr.d", ",", ",$w17")
LT("ilvl.b", 0, SETV(KI), V, "ilvl.b", ",", ",$w17")
LT("ilvl.h", 0, SETV(KI), V, "ilvl.h", ",", ",$w17")
LT("ilvl.w", 0, SETV(KI), V, "ilvl.w", ",", ",$w17")
LT("ilvev.b", 0, SETV(KI), V, "ilvev.b", ",", ",$w17")
LT("ilvev.h", 0, SETV(KI), V, "ilvev.h", ",", ",$w17")
LT("ilvev.w", 0, SETV(KI), V, "ilvev.w", ",", ",$w17")
LT("ilvod.b", 0, SETV(KI), V, "ilvod.b", ",", ",$w17")
LT("ilvod.h", 0, SETV(KI), V, "ilvod.h", ",", ",$w17")
LT("ilvod.w", 0, SETV(KI), V, "ilvod.w", ",", ",$w17")
LT("pckev.b", 0, SETV(KI), V, "pckev.b", ",", ",$w17")
LT("pckev.h", 0, SETV(KI), V, "pckev.h", ",", ",$w17")
LT("pckev.w", 0, SETV(KI), V, "pckev.w", ",", ",$w17")
LT("pckod.b", 0, SETV(KI), V, "pckod.b", ",", ",$w17")
LT("pckod.h", 0, SETV(KI), V, "pckod.h", ",", ",$w17")
LT("sldi.b", 0, SETV(KI), V, "sldi.b", ",", "[3]")
/* Widening and narrowing idioms, as chains (w19 is zero). */
CUSTOM("u8->u16: ilvr.b with zero", "lat", 64, SETV(KI), R64("ilvr.b $w0,$w19,$w0\n"))
CUSTOM("i16->i32: ilvr.h self + srai.w 16", "lat", 32, SETV(KI), R32("ilvr.h $w0,$w0,$w0\nsrai.w $w0,$w0,16\n"))
CUSTOM("i16->i32: clti_s.h + ilvr.h", "lat", 32, SETV(KI), R32("clti_s.h $w12,$w0,0\nilvr.h $w0,$w12,$w0\n"))
CUSTOM("u8->f32: ilvr.b, ilvr.h, ffint_u.w", "lat", 16, SETV(KI),
       R16("ilvr.b $w0,$w19,$w0\nilvr.h $w0,$w19,$w0\nffint_u.w $w0,$w0\n"))
CUSTOM("i32->i16 sat: sat_s.w + pckev.h", "lat", 32, SETV(KI), R32("sat_s.w $w0,$w0,15\npckev.h $w0,$w0,$w0\n"))
CUSTOM("u16->u8 sat: sat_u.h + pckev.b", "lat", 32, SETV(KI), R32("sat_u.h $w0,$w0,7\npckev.b $w0,$w0,$w0\n"))

/* ---- vector load and store, L1-resident ---- */
TP("ld.b", 0, "", VM, "ld.b", "", "")
TP("ld.h", 0, "", VM, "ld.h", "", "")
TP("ld.w", 0, "", VM, "ld.w", "", "")
TP("ld.d", 0, "", VM, "ld.d", "", "")
TP("ld.b (address +1)", 1, "", VM, "ld.b", "", "")
TP("ld.w (address +1)", 1, "", VM, "ld.w", "", "")
TP("ld.w (address +4)", 4, "", VM, "ld.w", "", "")
TP("ld.w (crosses a 32-byte line)", 24, "", VM, "ld.w", "", "")
TP("ld.w (crosses a 4 KiB page)", -8, "", VM0, "ld.w", "", "")
TP("ld.w (one address)", 0, "", VM0, "ld.w", "", "")
TP("st.b", 0, SETV(KI), VMS, "st.b", "", "")
TP("st.w", 0, SETV(KI), VMS, "st.w", "", "")
TP("st.d", 0, SETV(KI), VMS, "st.d", "", "")
TP("st.w (address +1)", 1, SETV(KI), VMS, "st.w", "", "")
TP("st.w (address +4)", 4, SETV(KI), VMS, "st.w", "", "")
TP("st.w (crosses a 32-byte line)", 24, SETV(KI), VMS, "st.w", "", "")
TP("st.w (crosses a 4 KiB page)", -8, SETV(KI), VMS0, "st.w", "", "")
TP("st.w (one address)", 0, SETV(KI), VMS0, "st.w", "", "")

/* ---- kernels: 1024 elements per pass, 4 KiB L1-resident buffers ---- */
#define KLOOP(count, body, incs) \
    "move $24,%[p]\nmove $25,%[q]\nli $15," #count "\n2:\n" body incs "addiu $15,$15,-1\nbnez $15,2b\n"
#define KERNEL(nm, var, fill, setup, block) BENCH(nm, var, 1024, 0, fill, setup, block, "")
#define E4(M) M(0, "$8", "$f0") M(1, "$9", "$f1") M(2, "$10", "$f2") M(3, "$11", "$f3")
enum { F_BYTES = 1, F_F32_PIXEL, F_I32_INRANGE, F_I32_MIXED, F_F32_AUDIO, F_I16 };

/* u8 -> f32 */
KERNEL("u8->f32", "msa", F_BYTES, SETV(KI),
       KLOOP(64, "ld.b $w0,0($24)\nilvr.b $w1,$w19,$w0\nilvl.b $w2,$w19,$w0\n"
                 "ilvr.h $w3,$w19,$w1\nilvl.h $w4,$w19,$w1\nilvr.h $w5,$w19,$w2\nilvl.h $w6,$w19,$w2\n"
                 "ffint_u.w $w3,$w3\nffint_u.w $w4,$w4\nffint_u.w $w5,$w5\nffint_u.w $w6,$w6\n"
                 "st.w $w3,0($25)\nst.w $w4,16($25)\nst.w $w5,32($25)\nst.w $w6,48($25)\n",
             "addiu $24,$24,16\naddiu $25,$25,64\n"))
#define K1S(i, r, f) "lbu " r "," #i "($24)\nmtc1 " r "," f "\ncvt.s.w " f "," f "\nswc1 " f "," #i "*4($25)\n"
KERNEL("u8->f32", "scalar", F_BYTES, "", KLOOP(256, E4(K1S), "addiu $24,$24,4\naddiu $25,$25,16\n"))

/* f32 -> u8, truncating, clamped to 0..255 */
#define K2V(r, o) "ld.w $w" #r "," #o "($24)\nftrunc_s.w $w" #r ",$w" #r "\nmaxi_s.w $w" #r ",$w" #r ",0\nsat_u.w $w" #r ",$w" #r ",7\n"
KERNEL("f32->u8 saturating", "msa", F_F32_PIXEL, SETV(KI),
       KLOOP(64, K2V(0, 0) K2V(1, 16) K2V(2, 32) K2V(3, 48)
                 "pckev.h $w0,$w1,$w0\npckev.h $w2,$w3,$w2\npckev.b $w0,$w2,$w0\nst.b $w0,0($25)\n",
             "addiu $24,$24,64\naddiu $25,$25,16\n"))
#define K2S(i, r, f) "lwc1 " f "," #i "*4($24)\ntrunc.w.s " f "," f "\nmfc1 " r "," f "\nslt $12," r ",$0\nmovn " r ",$0,$12\n" \
                     "slti $12," r ",256\nmovz " r ",$16,$12\nsb " r "," #i "($25)\n"
KERNEL("f32->u8 saturating", "scalar branchless", F_F32_PIXEL, "li $16,255\n",
       KLOOP(256, E4(K2S), "addiu $24,$24,16\naddiu $25,$25,4\n"))

/* i32 -> i16, saturating */
KERNEL("i32->i16 saturating, in-range data", "msa", F_I32_INRANGE, SETV(KI),
       KLOOP(128, "ld.w $w0,0($24)\nld.w $w1,16($24)\nsat_s.w $w0,$w0,15\nsat_s.w $w1,$w1,15\n"
                  "pckev.h $w0,$w1,$w0\nst.h $w0,0($25)\n", "addiu $24,$24,32\naddiu $25,$25,16\n"))
KERNEL("i32->i16 saturating, half out of range", "msa", F_I32_MIXED, SETV(KI),
       KLOOP(128, "ld.w $w0,0($24)\nld.w $w1,16($24)\nsat_s.w $w0,$w0,15\nsat_s.w $w1,$w1,15\n"
                  "pckev.h $w0,$w1,$w0\nst.h $w0,0($25)\n", "addiu $24,$24,32\naddiu $25,$25,16\n"))
#define K3BRANCH ".set push\n.set noreorder\nlw $8,0($24)\nslt $9,$8,$17\nbeqz $9,31f\nslt $9,$16,$8\nmove $8,$17\n" \
                 "31:\nbeqz $9,32f\naddiu $24,$24,4\nmove $8,$16\n32:\nsh $8,0($25)\n.set pop\n"
#define K3SETUP "li $16,32767\nli $17,-32768\n"
KERNEL("i32->i16 saturating, in-range data", "scalar branches", F_I32_INRANGE, K3SETUP, KLOOP(1024, K3BRANCH, "addiu $25,$25,2\n"))
KERNEL("i32->i16 saturating, half out of range", "scalar branches", F_I32_MIXED, K3SETUP, KLOOP(1024, K3BRANCH, "addiu $25,$25,2\n"))
#define K3S(i, r, f) "lw " r "," #i "*4($24)\nslt $12," r ",$17\nmovn " r ",$17,$12\nslt $12,$16," r "\nmovn " r ",$16,$12\nsh " r "," #i "*2($25)\n"
KERNEL("i32->i16 saturating, in-range data", "scalar branchless", F_I32_INRANGE, K3SETUP,
       KLOOP(256, E4(K3S), "addiu $24,$24,16\naddiu $25,$25,8\n"))
KERNEL("i32->i16 saturating, half out of range", "scalar branchless", F_I32_MIXED, K3SETUP,
       KLOOP(256, E4(K3S), "addiu $24,$24,16\naddiu $25,$25,8\n"))

/* f32 -> i32, scaled, round to nearest, saturating */
#define K4V(r, o) "ld.w $w" #r "," #o "($24)\nfmul.w $w" #r ",$w" #r ",$w16\nftint_s.w $w" #r ",$w" #r "\nst.w $w" #r "," #o "($25)\n"
KERNEL("f32*k->i32", "msa", F_F32_AUDIO, SETV(KF),
       KLOOP(64, K4V(0, 0) K4V(1, 16) K4V(2, 32) K4V(3, 48), "addiu $24,$24,64\naddiu $25,$25,64\n"))
#define K4S(i, r, f) "lwc1 " f "," #i "*4($24)\nmul.s " f "," f ",$f16\ncvt.w.s " f "," f "\nswc1 " f "," #i "*4($25)\n"
KERNEL("f32*k->i32", "scalar", F_F32_AUDIO, SETS(KF), KLOOP(256, E4(K4S), "addiu $24,$24,16\naddiu $25,$25,16\n"))

/* i16 -> f32 scaled by 1/32768 */
#define K5V(r, s, o) "ld.h $w" #r "," #o "($24)\nilvr.h $w" #s ",$w" #r ",$w" #r "\nilvl.h $w" #r ",$w" #r ",$w" #r "\n" \
                     "srai.w $w" #s ",$w" #s ",16\nsrai.w $w" #r ",$w" #r ",16\nffint_s.w $w" #s ",$w" #s "\nffint_s.w $w" #r ",$w" #r "\n" \
                     "fmul.w $w" #s ",$w" #s ",$w16\nfmul.w $w" #r ",$w" #r ",$w16\nst.w $w" #s "," #o "*2($25)\nst.w $w" #r "," #o "*2+16($25)\n"
KERNEL("i16->f32 /32768", "msa ilv+srai+ffint_s+fmul", F_I16, SETV(KK),
       KLOOP(64, K5V(0, 1, 0) K5V(2, 3, 16), "addiu $24,$24,32\naddiu $25,$25,64\n"))
#define K5Q(r, s, o) "ld.h $w" #r "," #o "($24)\nffqr.w $w" #s ",$w" #r "\nffql.w $w" #r ",$w" #r "\n" \
                     "st.w $w" #s "," #o "*2($25)\nst.w $w" #r "," #o "*2+16($25)\n"
KERNEL("i16->f32 /32768", "msa ffqr/ffql", F_I16, SETV(KK),
       KLOOP(64, K5Q(0, 1, 0) K5Q(2, 3, 16), "addiu $24,$24,32\naddiu $25,$25,64\n"))
#define K5S(i, r, f) "lh " r "," #i "*2($24)\nmtc1 " r "," f "\ncvt.s.w " f "," f "\nmul.s " f "," f ",$f16\nswc1 " f "," #i "*4($25)\n"
KERNEL("i16->f32 /32768", "scalar", F_I16, SETS(KK), KLOOP(256, E4(K5S), "addiu $24,$24,8\naddiu $25,$25,16\n"))

/* u8 * u8 >> 8 (two source rows 2048 bytes apart) */
KERNEL("u8*u8>>8", "msa", F_BYTES, SETV(KI),
       KLOOP(64, "addiu $14,$24,2048\nld.b $w0,0($24)\nld.b $w1,0($14)\nilvr.b $w2,$w19,$w0\nilvl.b $w3,$w19,$w0\n"
                 "ilvr.b $w4,$w19,$w1\nilvl.b $w5,$w19,$w1\nmulv.h $w2,$w2,$w4\nmulv.h $w3,$w3,$w5\n"
                 "srli.h $w2,$w2,8\nsrli.h $w3,$w3,8\npckev.b $w2,$w3,$w2\nst.b $w2,0($25)\n",
             "addiu $24,$24,16\naddiu $25,$25,16\n"))
#define K6S(i, r, f) "lbu " r "," #i "($24)\nlbu $12,2048+" #i "($24)\nmul " r "," r ",$12\nsrl " r "," r ",8\nsb " r "," #i "($25)\n"
KERNEL("u8*u8>>8", "scalar", F_BYTES, "", KLOOP(256, E4(K6S), "addiu $24,$24,4\naddiu $25,$25,4\n"))

/* RGB24 -> RGBA32 with alpha 255; elements are pixels */
KERNEL("RGB24->RGBA32", "msa vshf.b", F_BYTES, SETV(KP),
       KLOOP(256, "ld.b $w0,0($24)\nmove.v $w1,$w18\nvshf.b $w1,$w16,$w0\nst.b $w1,0($25)\n",
             "addiu $24,$24,12\naddiu $25,$25,16\n"))
#define K7S(i, r, f) "lbu $8," #i "*3($24)\nlbu $9," #i "*3+1($24)\nlbu $10," #i "*3+2($24)\nsb $8," #i "*4($25)\n" \
                     "sb $9," #i "*4+1($25)\nsb $10," #i "*4+2($25)\nsb $16," #i "*4+3($25)\n"
KERNEL("RGB24->RGBA32", "scalar bytes", F_BYTES, "li $16,255\n", KLOOP(256, E4(K7S), "addiu $24,$24,12\naddiu $25,$25,16\n"))

/* ---- memory bandwidth: n blocks from p to q ---- */
#define MSET "move $24,%[p]\nmove $25,%[q]\nli $8,0\nli $9,0\nli $10,0\nli $11,0\n" \
             "ldi.b $w0,0\nldi.b $w1,0\nldi.b $w2,0\nldi.b $w3,0\nldi.b $w19,0\n"
#define MEM(nm, var, bytes, block) MEMFN(nm, var, bytes, 0, 0, MSET, block, "")
MEM("sum", "lw", 32, "lw $12,0($24)\nlw $13,4($24)\nlw $14,8($24)\nlw $15,12($24)\naddu $8,$8,$12\naddu $9,$9,$13\n"
    "addu $10,$10,$14\naddu $11,$11,$15\nlw $12,16($24)\nlw $13,20($24)\nlw $14,24($24)\nlw $15,28($24)\n"
    "addu $8,$8,$12\naddu $9,$9,$13\naddu $10,$10,$14\naddu $11,$11,$15\naddiu $24,$24,32\n")
MEM("sum", "ld.w", 64, "ld.w $w4,0($24)\nld.w $w5,16($24)\nld.w $w6,32($24)\nld.w $w7,48($24)\naddv.w $w0,$w0,$w4\n"
    "addv.w $w1,$w1,$w5\naddv.w $w2,$w2,$w6\naddv.w $w3,$w3,$w7\naddiu $24,$24,64\n")
MEM("copy", "lw/sw", 32, "lw $8,0($24)\nlw $9,4($24)\nlw $10,8($24)\nlw $11,12($24)\nsw $8,0($25)\nsw $9,4($25)\n"
    "sw $10,8($25)\nsw $11,12($25)\nlw $8,16($24)\nlw $9,20($24)\nlw $10,24($24)\nlw $11,28($24)\nsw $8,16($25)\n"
    "sw $9,20($25)\nsw $10,24($25)\nsw $11,28($25)\naddiu $24,$24,32\naddiu $25,$25,32\n")
MEM("copy", "ld.b/st.b", 64, "ld.b $w4,0($24)\nld.b $w5,16($24)\nld.b $w6,32($24)\nld.b $w7,48($24)\nst.b $w4,0($25)\n"
    "st.b $w5,16($25)\nst.b $w6,32($25)\nst.b $w7,48($25)\naddiu $24,$24,64\naddiu $25,$25,64\n")
MEM("copy", "ld.b/st.b + pref 256 ahead", 64, "pref 0,256($24)\npref 1,256($25)\nld.b $w4,0($24)\nld.b $w5,16($24)\n"
    "ld.b $w6,32($24)\nld.b $w7,48($24)\nst.b $w4,0($25)\nst.b $w5,16($25)\nst.b $w6,32($25)\nst.b $w7,48($25)\n"
    "addiu $24,$24,64\naddiu $25,$25,64\n")
MEM("fill", "sw", 32, "sw $0,0($25)\nsw $0,4($25)\nsw $0,8($25)\nsw $0,12($25)\nsw $0,16($25)\nsw $0,20($25)\n"
    "sw $0,24($25)\nsw $0,28($25)\naddiu $25,$25,32\n")
MEM("fill", "st.b", 64, "st.b $w19,0($25)\nst.b $w19,16($25)\nst.b $w19,32($25)\nst.b $w19,48($25)\naddiu $25,$25,64\n")

extern const struct bench __start_benchtab[], __stop_benchtab[], __start_memtab[], __stop_memtab[];

static uint32_t consts[160] __attribute__((aligned(16)));
static sigjmp_buf trap_env;
static volatile sig_atomic_t trap_signal;

static void on_trap(int sig) { trap_signal = sig; siglongjmp(trap_env, 1); }

static uint64_t now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return x < y ? -1 : x > y;
}

static void put_f32(int off, float v) { for (int i = 0; i < 4; i++) memcpy((char *)consts + off + 4 * i, &v, 4); }
static void put_f64(int off, double v) { for (int i = 0; i < 2; i++) memcpy((char *)consts + off + 8 * i, &v, 8); }
static void put_i32(int off, int32_t v) { for (int i = 0; i < 4; i++) memcpy((char *)consts + off + 4 * i, &v, 4); }

static void init_consts(void) {
    uint8_t *b = (uint8_t *)consts;
    put_i32(KI, 3); put_i32(KI + 16, 5); put_i32(KI + 32, 2);
    for (int i = 0; i < 16; i++) b[KI + 48 + i] = (uint8_t)i;
    put_f32(KF, 1.5f); put_f32(KF + 16, 1.25f); put_f32(KF + 32, 0.0f); put_f32(KF + 48, 1.0f); put_f32(KF + 64, 1.37f);
    put_f64(KD, 1.5); put_f64(KD + 16, 1.25); put_f64(KD + 32, 0.0); put_f64(KD + 48, 1.0); put_f64(KD + 64, 1.37);
    put_f32(KZ + 16, 1.25f); put_f32(KZ + 64, -1.0f);
    put_f64(KZD + 16, 1.25); put_f64(KZD + 64, -1.0);
    put_i32(KN, 0x00012345); put_i32(KN + 16, 0x00012345); put_f32(KN + 48, 1.0f); put_f32(KN + 64, 1.37f);
    put_f32(KK + 16, 1.0f / 32768.0f); put_f32(KK + 48, 1.0f);
    memset(b + KP + 16, 0xff, 16);
    static const uint8_t control[16] = {0, 1, 2, 16, 3, 4, 5, 16, 6, 7, 8, 16, 9, 10, 11, 16};
    memcpy(b + KP + 48, control, 16);
}

static uint32_t rng_state = 0x12345678u;
static uint32_t rng(void) { rng_state ^= rng_state << 13; rng_state ^= rng_state >> 17; rng_state ^= rng_state << 5; return rng_state; }

static void fill_source(uint8_t *src, int kind) {
    rng_state = 0x12345678u;
    for (int i = 0; i < 2048; i++) {
        uint32_t r = rng();
        float f; int32_t v; int16_t h;
        switch (kind) {
        case F_BYTES: memcpy(src + 4 * i, &r, 4); break;
        case F_F32_PIXEL: f = (float)(r % 35000u) / 100.0f - 50.0f; memcpy(src + 4 * i, &f, 4); break;
        case F_I32_INRANGE: v = (int32_t)(r % 60001u) - 30000; memcpy(src + 4 * i, &v, 4); break;
        case F_I32_MIXED: v = (r & 1) ? (int32_t)(r % 60001u) - 30000 : (int32_t)r; memcpy(src + 4 * i, &v, 4); break;
        case F_F32_AUDIO: f = (float)(int32_t)(r % 2000001u) / 1000.0f - 1000.0f; memcpy(src + 4 * i, &f, 4); break;
        case F_I16: h = (int16_t)r; memcpy(src + 4 * i, &h, 2); h = (int16_t)(r >> 16); memcpy(src + 4 * i + 2, &h, 2); break;
        }
    }
}

static uint32_t checksum(const uint8_t *p, int n) {
    uint32_t h = 2166136261u;
    for (int i = 0; i < n; i++) h = (h ^ p[i]) * 16777619u;
    return h;
}

#define REPS 5
/* Calibrates the iteration count to about target_ns, then times REPS runs. */
static int measure(const struct bench *b, void *p, void *q, uint64_t target_ns, long *n_out, uint64_t *t) {
    long n = 1;
    uint64_t dt;
    if (sigsetjmp(trap_env, 1)) return -1;
    for (;;) {
        uint64_t t0 = now();
        b->fn(n, p, q, consts);
        dt = now() - t0;
        if (dt >= target_ns / 4 || n >= (1L << 26)) break;
        n = dt < 20000 ? n * 16 : (long)((uint64_t)n * (target_ns / 2) / dt) + 1;
    }
    n = (long)((uint64_t)n * target_ns / (dt ? dt : 1));
    if (n < 1) n = 1;
    for (int i = 0; i < REPS; i++) {
        uint64_t t0 = now();
        b->fn(n, p, q, consts);
        t[i] = now() - t0;
    }
    qsort(t, REPS, sizeof *t, cmp_u64);
    *n_out = n;
    return 0;
}

static long read_long(const char *path) {
    long v = -1;
    FILE *f = fopen(path, "r");
    if (f) { if (fscanf(f, "%ld", &v) != 1) v = -1; fclose(f); }
    return v;
}

static void print_clock(const char *when) {
    char governor[64] = "unknown";
    FILE *f = fopen("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "r");
    if (f) { if (fscanf(f, "%63s", governor) != 1) strcpy(governor, "unknown"); fclose(f); }
    printf("H cpufreq_khz_%s %ld\nH governor_%s %s\n", when,
           read_long("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"), when, governor);
}

static long mem_available_kib(void) {
    char line[128];
    long v = -1;
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) return -1;
    while (fgets(line, sizeof line, f))
        if (sscanf(line, "MemAvailable: %ld kB", &v) == 1) break;
    fclose(f);
    return v;
}

/* rdhwr 2 (cycle counter) and 3 (its resolution): readable, and at what cost. */
static void cycle_counter(void) {
    uint32_t a = 0, b = 0, res = 0;
    if (sigsetjmp(trap_env, 1)) { printf("H rdhwr_cc signal_%d\n", (int)trap_signal); return; }
    __asm__ volatile("rdhwr %0,$2\nrdhwr %1,$3" : "=r"(a), "=r"(res));
    uint64_t t0 = now();
    for (int i = 0; i < 2000; i++) __asm__ volatile("rdhwr %0,$2" : "=r"(b));
    uint64_t t1 = now();
    __asm__ volatile("rdhwr %0,$2" : "=r"(a));
    uint64_t w0 = now();
    while (now() - w0 < 20000000ull) {}
    __asm__ volatile("rdhwr %0,$2" : "=r"(b));
    uint64_t w1 = now();
    printf("H rdhwr_cc readable\nH rdhwr_ccres %u\nH rdhwr_read_ns %.1f\nH rdhwr_ticks_per_s %.0f\n", res,
           (double)(t1 - t0) / 2000.0, (double)(uint32_t)(b - a) * 1e9 / (double)(w1 - w0));
}

/* ---- first use and context switches ---- */
#define MSA_INSN __asm__ volatile("addv.w $w0,$w0,$w0" : : : "$f0")
#define FPU_INSN __asm__ volatile("add.s $f0,$f0,$f0" : : : "$f0")
enum { SAMPLES = 24 };
struct first { int mode; uint64_t empty, first, second, upgrade, after_sleep[SAMPLES], empty_sleep[SAMPLES]; };

static void *first_thread(void *arg) {
    struct first *f = arg;
    uint64_t a, b;
    f->empty = ~0ull;
    for (int i = 0; i < 8; i++) { a = now(); b = now(); if (b - a < f->empty) f->empty = b - a; }
    if (f->mode == 0) {
        a = now(); MSA_INSN; b = now(); f->first = b - a;
        a = now(); MSA_INSN; b = now(); f->second = b - a;
        f->upgrade = 0;
    } else {
        a = now(); FPU_INSN; b = now(); f->first = b - a;
        a = now(); FPU_INSN; b = now(); f->second = b - a;
        a = now(); MSA_INSN; b = now(); f->upgrade = b - a;
    }
    for (int i = 0; i < SAMPLES; i++) {
        usleep(1500);
        a = now(); b = now(); f->empty_sleep[i] = b - a;
        usleep(1500);
        a = now();
        if (f->mode == 0) MSA_INSN; else FPU_INSN;
        b = now(); f->after_sleep[i] = b - a;
    }
    return NULL;
}

static void first_use(void) {
    static struct first runs[2][SAMPLES];
    static const char *names[2] = {"msa", "fpu"};
    for (int mode = 0; mode < 2; mode++) {
        uint64_t v[5][SAMPLES], sl[SAMPLES * SAMPLES], es[SAMPLES * SAMPLES];
        for (int i = 0; i < SAMPLES; i++) {
            pthread_t th;
            runs[mode][i].mode = mode;
            if (pthread_create(&th, NULL, first_thread, &runs[mode][i])) { printf("F %s_thread failed\n", names[mode]); return; }
            pthread_join(th, NULL);
            v[0][i] = runs[mode][i].empty; v[1][i] = runs[mode][i].first; v[2][i] = runs[mode][i].second;
            v[3][i] = runs[mode][i].upgrade;
            memcpy(sl + i * SAMPLES, runs[mode][i].after_sleep, sizeof runs[mode][i].after_sleep);
            memcpy(es + i * SAMPLES, runs[mode][i].empty_sleep, sizeof runs[mode][i].empty_sleep);
        }
        static const char *keys[4] = {"empty_pair", "first_insn_fresh_thread", "second_insn", "first_msa_after_fpu"};
        for (int k = 0; k < 4; k++) {
            if (k == 3 && mode == 0) continue;
            qsort(v[k], SAMPLES, sizeof(uint64_t), cmp_u64);
            printf("F %s_%s_ns min %llu median %llu max %llu\n", names[mode], keys[k], (unsigned long long)v[k][0],
                   (unsigned long long)v[k][SAMPLES / 2], (unsigned long long)v[k][SAMPLES - 1]);
        }
        qsort(sl, SAMPLES * SAMPLES, sizeof(uint64_t), cmp_u64);
        qsort(es, SAMPLES * SAMPLES, sizeof(uint64_t), cmp_u64);
        printf("F %s_empty_pair_after_sleep_ns min %llu median %llu max %llu\n", names[mode], (unsigned long long)es[0],
               (unsigned long long)es[SAMPLES * SAMPLES / 2], (unsigned long long)es[SAMPLES * SAMPLES - 1]);
        printf("F %s_insn_after_sleep_ns min %llu median %llu max %llu\n", names[mode], (unsigned long long)sl[0],
               (unsigned long long)sl[SAMPLES * SAMPLES / 2], (unsigned long long)sl[SAMPLES * SAMPLES - 1]);
    }
}

/* Two threads on one CPU pass a byte through pipes; each uses no FP, scalar FP or MSA per turn. */
struct pong { int in, out, mode, rounds; uint64_t best; };
static void *pong_thread(void *arg) {
    struct pong *p = arg;
    char c;
    for (int i = 0; i < p->rounds; i++) {
        if (read(p->in, &c, 1) != 1) break;
        if (p->mode == 1) FPU_INSN;
        if (p->mode == 2) MSA_INSN;
        if (write(p->out, &c, 1) != 1) break;
    }
    return NULL;
}

static void *ping_main(void *arg) {
    struct pong *p = arg;
    char c = 1;
    uint64_t best = ~0ull;
    for (int rep = 0; rep < 5; rep++) {
        uint64_t t0 = now();
        for (int i = 0; i < p->rounds / 5; i++) {
            if (p->mode == 1) FPU_INSN;
            if (p->mode == 2) MSA_INSN;
            if (write(p->out, &c, 1) != 1 || read(p->in, &c, 1) != 1) return NULL;
        }
        uint64_t dt = now() - t0;
        if (dt < best) best = dt;
    }
    p->best = best;
    return NULL;
}

static void context_switch(void) {
    static const char *names[3] = {"no_fp", "fpu", "msa"};
    for (int mode = 0; mode < 3; mode++) {
        int ab[2], ba[2];
        struct pong ping, pong;
        pthread_t t1, t2;
        if (pipe(ab) || pipe(ba)) return;
        ping = (struct pong){ba[0], ab[1], mode, 2500, 0};
        pong = (struct pong){ab[0], ba[1], mode, 2500, 0};
        /* fresh threads, so the no_fp pair never owned the FPU */
        pthread_create(&t2, NULL, pong_thread, &pong);
        pthread_create(&t1, NULL, ping_main, &ping);
        pthread_join(t1, NULL); pthread_join(t2, NULL);
        close(ab[0]); close(ab[1]); close(ba[0]); close(ba[1]);
        printf("F pipe_round_trip_%s_ns %.0f\n", names[mode], (double)ping.best / 500.0);
    }
}

/* One memory test: `off` 1 moves the source address by one byte. The entry after the table is libc memcpy. */
static void mem_one(const struct bench *b, uint8_t *src, uint8_t *dst, long size, int off) {
    int libc = b == __stop_memtab;
    volatile uint64_t best = ~0ull;
    if (sigsetjmp(trap_env, 1)) { printf("M\t%s\t%s\t%ld\tsignal %d\n", b->name, b->variant, size, (int)trap_signal); return; }
    long blocks = libc ? 1 : size / b->ops;
    int passes = (int)((24 << 20) / size);
    if (passes > 400) passes = 400;
    for (int rep = 0; rep < REPS; rep++) {
        uint64_t a = now();
        for (int i = 0; i < passes; i++) {
            if (libc) memcpy(dst, src, size);
            else b->fn(blocks, src + off, dst, consts);
        }
        uint64_t d = now() - a;
        if (d < best) best = d;
    }
    printf("M\t%s\t%s%s\t%ld\t%.4f\n", libc ? "copy" : b->name, libc ? "libc memcpy" : b->variant,
           off ? " (address +1)" : "", size, (double)best / ((double)passes * size));
}

int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    alarm(240); /* bounded device lifetime */
    FILE *oom = fopen("/proc/self/oom_score_adj", "w");
    if (oom) { fputs("1000\n", oom); fclose(oom); }
    cpu_set_t set;
    CPU_ZERO(&set); CPU_SET(0, &set);
    printf("H affinity_cpu0 %s\n", sched_setaffinity(0, sizeof set, &set) ? "failed" : "ok");
    printf("H cpus_online %ld\n", sysconf(_SC_NPROCESSORS_ONLN));
    print_clock("start");
    struct timespec res;
    clock_getres(CLOCK_MONOTONIC, &res);
    printf("H clock_resolution_ns %ld\n", (long)res.tv_nsec);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_trap;
    sigaction(SIGILL, &sa, NULL); sigaction(SIGFPE, &sa, NULL); sigaction(SIGBUS, &sa, NULL); sigaction(SIGSEGV, &sa, NULL);
    init_consts();
    cycle_counter();
    uint64_t t0 = now();

    first_use();
    context_switch();

    uint8_t *work = mmap(NULL, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    uint8_t *ksrc = mmap(NULL, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    uint8_t *kdst = mmap(NULL, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (work == MAP_FAILED || ksrc == MAP_FAILED || kdst == MAP_FAILED) return 2;
    memset(work, 0, 16384); memset(ksrc, 0, 16384); memset(kdst, 0, 16384);

    for (const struct bench *b = __start_benchtab; b < __stop_benchtab; b++) {
        uint64_t t[REPS];
        long n;
        void *p = work + 4096 + b->poff, *q = kdst;
        if (b->fill) { fill_source(ksrc, b->fill); memset(kdst, 0, 8192); p = ksrc; }
        else memset(work, 0, 16384);
        if (measure(b, p, q, 5000000ull, &n, t)) {
            printf("B\t%s\t%s\tsignal %d\n", b->name, b->variant, (int)trap_signal);
            continue;
        }
        double per = (double)n * b->ops;
        printf("B\t%s\t%s\t%d\t%ld\t%.4f\t%.4f", b->name, b->variant, b->ops, n, (double)t[0] / per, (double)t[REPS / 2] / per);
        if (b->fill) printf("\t%08x", checksum(kdst, 4096));
        printf("\n");
    }
    print_clock("after_instructions");

    static const long sizes[3] = {16 << 10, 256 << 10, 8 << 20};
    long avail = mem_available_kib();
    printf("H mem_available_kib %ld\n", avail);
    for (int s = 0; s < 3; s++) {
        long size = sizes[s];
        if (size > (1 << 20) && avail >= 0 && avail < 28 * 1024) { printf("M\tskipped\t%ld\tlow memory\n", size); continue; }
        uint8_t *src = mmap(NULL, size + 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        uint8_t *dst = mmap(NULL, size + 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (src == MAP_FAILED || dst == MAP_FAILED) { printf("M\tskipped\t%ld\tmmap failed\n", size); continue; }
        memset(src, 1, size + 4096); memset(dst, 2, size + 4096);
        for (const struct bench *b = __start_memtab; b <= __stop_memtab; b++) {
            mem_one(b, src, dst, size, 0);
            if (b < __stop_memtab && !strcmp(b->variant, "ld.w")) mem_one(b, src, dst, size, 1);
        }
        munmap(src, size + 4096); munmap(dst, size + 4096);
    }
    print_clock("end");
    printf("H elapsed_s %.1f\n", (double)(now() - t0) / 1e9);
    printf("H done 1\n");
    return 0;
}
