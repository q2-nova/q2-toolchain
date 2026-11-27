/* Lanes of vector_math.ll against the scalar libm functions, on edge cases
 * (signed zeros, denormals, the normal range's ends, infinities, quiet and
 * signalling NaNs, overflow and underflow of exp2, products whose single
 * rounding differs from two) and generated values. A lane matches when its
 * bits are the scalar result's, or when both are NaN; NaNs with other bits
 * are counted apart. Exit status 1 when any lane does not match. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

void vm_log2_f32(float *, const float *);
void vm_exp2_f32(float *, const float *);
void vm_fma_f32(float *, const float *, const float *, const float *);
void vm_log2_f64(double *, const double *);
void vm_exp2_f64(double *, const double *);
void vm_fma_f64(double *, const double *, const double *, const double *);
void vm_fmadd_w(float *, const float *, const float *, const float *);
void vm_fexp2_w(float *, const float *, const int32_t *);

static uint32_t seed = 12345;
static uint32_t next(void) { seed = seed * 1664525u + 1013904223u; return seed; }
static uint64_t next64(void) { uint64_t hi = next(); return hi << 32 | next(); }
static float f32(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }
static double f64(uint64_t b) { double d; memcpy(&d, &b, 8); return d; }
static uint32_t b32(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }
static uint64_t b64(double d) { uint64_t b; memcpy(&b, &d, 8); return b; }

static const uint32_t E32[] = {
    0x00000000, 0x80000000, 0x00000001, 0x80000001, 0x007fffff, 0x807fffff, 0x00800000, 0x80800000,
    0x3f800000, 0xbf800000, 0x3f800001, 0x3f7fffff, 0x40000000, 0x3f000000, 0x40400000, 0x41200000,
    0x0da24260, 0x7149f2ca, 0x7f7fffff, 0xff7fffff, 0x7f800000, 0xff800000, 0x7fc00000, 0xffc00000,
    0x7fa00000, 0x7fc12345, 0xc0000000, 0x42fe0000, 0x42ffffff, 0x43000000, 0xc2fc0000, 0xc2fd0000,
    0xc3150000, 0xc3158000, 0xc3160000, 0xc3170000, 0x3dcccccd, 0xbdcccccd, 0x41bc0000, 0x322bcc77,
    0xb22bcc77, 0x42808000, 0xc2808000, 0x3f800800, 0x3f801000, 0xbf801000, 0x33800000, 0x34000000,
};
static const uint64_t E64[] = {
    0x0000000000000000, 0x8000000000000000, 0x0000000000000001, 0x8000000000000001,
    0x000fffffffffffff, 0x0010000000000000, 0x3ff0000000000000, 0xbff0000000000000,
    0x3ff0000000000001, 0x3fefffffffffffff, 0x4000000000000000, 0x3fe0000000000000,
    0x7fefffffffffffff, 0xffefffffffffffff, 0x7ff0000000000000, 0xfff0000000000000,
    0x7ff8000000000000, 0xfff8000000000000, 0x7ff4000000000000, 0x7ff8000000012345,
    0x408ff80000000000, 0x408fffffffffffff, 0x4090000000000000, 0xc08ff00000000000,
    0xc090c80000000000, 0xc090cc0000000000, 0xc090d00000000000, 0x3fb999999999999a,
    0x3ff0000008000000, 0xbff0000010000000, 0x3ca0000000000000, 0x3cb0000000000000,
};
#define N32 (sizeof E32 / sizeof E32[0])
#define N64 (sizeof E64 / sizeof E64[0])

struct tally { const char *name; unsigned cases, bad, nan_bits, shown; };
static int failed;

static void check32(struct tally *t, float got, float want, const float *in, int n) {
    t->cases++;
    if (b32(got) == b32(want)) return;
    if (isnan(got) && isnan(want)) { t->nan_bits++; return; }
    t->bad++;
    if (t->shown++ < 4) {
        printf("  %s(", t->name);
        for (int i = 0; i < n; i++) printf("%s%08x", i ? ", " : "", b32(in[i]));
        printf(") = %08x, scalar %08x\n", b32(got), b32(want));
    }
}
static void check64(struct tally *t, double got, double want, const double *in, int n) {
    t->cases++;
    if (b64(got) == b64(want)) return;
    if (isnan(got) && isnan(want)) { t->nan_bits++; return; }
    t->bad++;
    if (t->shown++ < 4) {
        printf("  %s(", t->name);
        for (int i = 0; i < n; i++) printf("%s%016llx", i ? ", " : "", (unsigned long long)b64(in[i]));
        printf(") = %016llx, scalar %016llx\n", (unsigned long long)b64(got), (unsigned long long)b64(want));
    }
}
static void report(struct tally *t) {
    printf("%-10s %7u lanes %6u wrong %5u NaNs with other bits\n", t->name, t->cases, t->bad, t->nan_bits);
    failed |= t->bad != 0;
}

/* Unary f32 inputs: the edges, then generated bits and generated values in
 * [-160, 160]. */
static float unary32(unsigned i) {
    if (i < N32) return f32(E32[i]);
    if (i % 2) return f32(next());
    return (float)((int32_t)next() / 2147483648.0 * 160.0);
}
static double unary64(unsigned i) {
    if (i < N64) return f64(E64[i]);
    if (i % 2) return f64(next64());
    return (int32_t)next() / 2147483648.0 * 1100.0;
}
/* fma inputs: every triple of the first 24 edges, single-rounding cases,
 * cancelling sums and generated bits. */
static void triple32(unsigned i, float *v) {
    if (i < 24 * 24 * 24) { v[0] = f32(E32[i / 576]); v[1] = f32(E32[i / 24 % 24]); v[2] = f32(E32[i % 24]); return; }
    switch (i % 4) {
    case 0: v[0] = f32(0x3f800001); v[1] = f32(0x3f7fffff); v[2] = -1.0f; break; /* fused -2^-46, unfused 0 */
    case 1: v[0] = f32(next() & 0x3fffffff); v[1] = f32(next() & 0x3fffffff); v[2] = -(v[0] * v[1]); break;
    case 2: v[0] = f32(0x1c800000 | (next() & 0xfffff)); v[1] = f32(0x1c800000 | (next() & 0xfffff)); v[2] = f32(next() & 0x807fffff); break;
    default: v[0] = f32(next()); v[1] = f32(next()); v[2] = f32(next()); break;
    }
}
static void triple64(unsigned i, double *v) {
    if (i < 20 * 20 * 20) { v[0] = f64(E64[i / 400]); v[1] = f64(E64[i / 20 % 20]); v[2] = f64(E64[i % 20]); return; }
    switch (i % 4) {
    case 0: v[0] = f64(0x3ff0000000000001); v[1] = f64(0x3fefffffffffffff); v[2] = -1.0; break;
    case 1: v[0] = f64(next64() & 0x3fffffffffffffff); v[1] = f64(next64() & 0x3fffffffffffffff); v[2] = -(v[0] * v[1]); break;
    case 2: v[0] = f64(0x1ff0000000000000 | (next64() & 0xffffffffff)); v[1] = f64(0x1ff0000000000000 | (next64() & 0xffffffffff)); v[2] = f64(next64() & 0x800fffffffffffff); break;
    default: v[0] = f64(next64()); v[1] = f64(next64()); v[2] = f64(next64()); break;
    }
}
static const int32_t EXPONENTS[] = {0, 1, -1, 2, 127, 128, -126, -127, -149, -150, 254, 255, -300, 300, 2147483647, -2147483647 - 1};

int main(void) {
    enum { UNARY = 8192, TRIPLES = 24 * 24 * 24 + 8192 };
    float a[4] __attribute__((aligned(16))), b[4] __attribute__((aligned(16))), c[4] __attribute__((aligned(16))), r[4] __attribute__((aligned(16)));
    double x[2] __attribute__((aligned(16))), y[2] __attribute__((aligned(16))), z[2] __attribute__((aligned(16))), q[2] __attribute__((aligned(16)));
    int32_t n[4] __attribute__((aligned(16)));
    struct tally t;

    t = (struct tally){"log2.f32"};
    for (unsigned i = 0; i < UNARY; i += 4) {
        for (int l = 0; l < 4; l++) a[l] = unary32(i + l);
        vm_log2_f32(r, a);
        for (int l = 0; l < 4; l++) check32(&t, r[l], log2f(a[l]), &a[l], 1);
    }
    report(&t);
    t = (struct tally){"exp2.f32"};
    for (unsigned i = 0; i < UNARY; i += 4) {
        for (int l = 0; l < 4; l++) a[l] = unary32(i + l);
        vm_exp2_f32(r, a);
        for (int l = 0; l < 4; l++) check32(&t, r[l], exp2f(a[l]), &a[l], 1);
    }
    report(&t);
    t = (struct tally){"fma.f32"};
    for (unsigned i = 0; i < TRIPLES; i += 4) {
        for (int l = 0; l < 4; l++) { float v[3]; triple32(i + l, v); a[l] = v[0]; b[l] = v[1]; c[l] = v[2]; }
        vm_fma_f32(r, a, b, c);
        for (int l = 0; l < 4; l++) { float in[3] = {a[l], b[l], c[l]}; check32(&t, r[l], fmaf(a[l], b[l], c[l]), in, 3); }
    }
    report(&t);
    t = (struct tally){"log2.f64"};
    for (unsigned i = 0; i < UNARY; i += 2) {
        for (int l = 0; l < 2; l++) x[l] = unary64(i + l);
        vm_log2_f64(q, x);
        for (int l = 0; l < 2; l++) check64(&t, q[l], log2(x[l]), &x[l], 1);
    }
    report(&t);
    t = (struct tally){"exp2.f64"};
    for (unsigned i = 0; i < UNARY; i += 2) {
        for (int l = 0; l < 2; l++) x[l] = unary64(i + l);
        vm_exp2_f64(q, x);
        for (int l = 0; l < 2; l++) check64(&t, q[l], exp2(x[l]), &x[l], 1);
    }
    report(&t);
    t = (struct tally){"fma.f64"};
    for (unsigned i = 0; i < TRIPLES; i += 2) {
        for (int l = 0; l < 2; l++) { double v[3]; triple64(i + l, v); x[l] = v[0]; y[l] = v[1]; z[l] = v[2]; }
        vm_fma_f64(q, x, y, z);
        for (int l = 0; l < 2; l++) { double in[3] = {x[l], y[l], z[l]}; check64(&t, q[l], fma(x[l], y[l], z[l]), in, 3); }
    }
    report(&t);
    t = (struct tally){"fmadd.w"};
    for (unsigned i = 0; i < TRIPLES; i += 4) {
        for (int l = 0; l < 4; l++) { float v[3]; triple32(i + l, v); a[l] = v[0]; b[l] = v[1]; c[l] = v[2]; }
        vm_fmadd_w(r, c, a, b);
        for (int l = 0; l < 4; l++) { float in[3] = {c[l], a[l], b[l]}; check32(&t, r[l], fmaf(a[l], b[l], c[l]), in, 3); }
    }
    report(&t);
    t = (struct tally){"fexp2.w"};
    for (unsigned i = 0; i < UNARY; i += 4) {
        for (int l = 0; l < 4; l++) {
            a[l] = unary32(i + l);
            unsigned k = (i + l) / N32;
            n[l] = k < sizeof EXPONENTS / sizeof EXPONENTS[0] ? EXPONENTS[k] : (int32_t)(next() % 601) - 300;
        }
        vm_fexp2_w(r, a, n);
        for (int l = 0; l < 4; l++) { float in[2] = {a[l], f32((uint32_t)n[l])}; check32(&t, r[l], ldexpf(a[l], n[l]), in, 2); }
    }
    report(&t);
    return failed;
}
