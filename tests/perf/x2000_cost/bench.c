/* Driver for loops.c: output hash and time per element of every function.
 * Built twice by check.py, once scalar and once with the X2000 toolchain;
 * the hashes must agree, and on the Q2 the times say whether each
 * vectorization decision was right.
 *   bench [REPS [msa]]   one line per function: name hash ns_per_element
 * With `msa` the process executes one MSA instruction first, so the scalar
 * build runs with live MSA context: the cost, to scalar code, of a thread
 * having used MSA at all.
 * Buffers start 1 element off their natural alignment on odd passes, so
 * unaligned vector loads and stores are exercised, and lengths are not
 * multiples of the vector width.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

void add_i32(int32_t *, const int32_t *, const int32_t *, size_t);
void add_u8(uint8_t *, const uint8_t *, const uint8_t *, size_t);
void mul_f32(float *, const float *, float, size_t);
void mul_f64(double *, const double *, double, size_t);
int32_t sum_i32(const int32_t *, size_t);
int32_t dot_i16(const int16_t *, const int16_t *, size_t);
void i16_to_f32(float *, const int16_t *, size_t);
void blend_u8(uint8_t *, const uint8_t *, const uint8_t *, size_t);
void clamp_i32_i16(int16_t *, const int32_t *, size_t);
void div_i32(int32_t *, const int32_t *, const int32_t *, size_t);
void div_f32(float *, const float *, const float *, size_t);
void mul_i64(int64_t *, const int64_t *, const int64_t *, size_t);
void gather(int32_t *, const int32_t *, const uint8_t *, size_t);
struct px { uint8_t r, g, b, a; };
uint32_t pack_bytes(const struct px *, unsigned);
void build_from_gprs(int32_t *, int32_t, int32_t, int32_t, int32_t, int32_t);
void slp_store(int32_t *, const int32_t *, const int32_t *);
void slp_f64(double *, const double *, const double *);
uint64_t xorshift_u64(uint64_t, unsigned);
void f32_to_i16(int16_t *, const float *, size_t);
void i32_to_u8(uint8_t *, const int32_t *, size_t);
void u8_to_f32(float *, const uint8_t *, size_t);
void i64_to_i16(int16_t *, const int64_t *, size_t);
void f64_to_i32(int32_t *, const double *, size_t);
void i32_to_f64(double *, const int32_t *, size_t);
void u16_to_u32(uint32_t *, const uint16_t *, size_t);
void i8_to_i32(int32_t *, const int8_t *, size_t);
void sat_f32_i32(int32_t *, const float *, uint32_t);
void sat_f32_u32(uint32_t *, const float *, uint32_t);
void sat_f32_i16(int16_t *, const float *, uint32_t);
void sat_f32_u8(uint8_t *, const float *, uint32_t);
void sat_f64_i32(int32_t *, const double *, uint32_t);
void sat_f64_i64(int64_t *, const double *, uint32_t);
void libm_log2(float *, const float *, const float *, uint32_t);
void libm_exp2(float *, const float *, const float *, uint32_t);
void libm_fma(float *, const float *, const float *, uint32_t);

#define N 1021
#define BYTES ((N + 8) * 8)
static _Alignas(16) uint8_t A[BYTES], B[BYTES], D[BYTES];
static uint32_t hash;

static void mix(const void *p, size_t n) {
    const uint8_t *b = p;
    for (size_t i = 0; i < n; i++) hash = (hash ^ b[i]) * 16777619u;
}

static uint32_t rng = 0x12345678;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }

static void fill_int(void) {
    for (size_t i = 0; i < BYTES; i++) { A[i] = next() >> 11; B[i] = next() >> 9; }
}
static void fill_f32(void) {
    float *a = (float *)A, *b = (float *)B;
    for (size_t i = 0; i < BYTES / 4; i++) {
        a[i] = (int32_t)(next() >> 8) / 4096.0f - 2000.0f;
        b[i] = (int32_t)(next() >> 8) / 8192.0f + 0.5f;
    }
}
static void fill_f64(void) {
    double *a = (double *)A, *b = (double *)B;
    for (size_t i = 0; i < BYTES / 8; i++) {
        a[i] = (int32_t)(next() >> 8) / 4096.0 - 2000.0;
        b[i] = (int32_t)(next() >> 8) / 8192.0 + 0.5;
    }
}

/* Values around every saturation boundary, with NaN and infinities. */
static const double EDGES[] = {
    0.0, -0.0, 0.5, -0.5, 0.999, -0.999, 1.5, -1.5, 127.0, 127.5, 128.0, -128.0, -128.5, -129.0,
    255.0, 255.5, 256.0, 32767.0, 32767.5, 32768.0, -32768.0, -32768.5, -32769.0, 65535.0, 65536.0,
    2147483520.0, 2147483647.0, 2147483648.0, 2147483904.0, -2147483648.0, -2147483904.0, -4294967296.0,
    4294967040.0, 4294967295.0, 4294967296.0, 4294967808.0, 9.2233720368547758e18, 9.3e18, -9.2233720368547758e18,
    -9.3e18, 1e30, -1e30, 1e-30, -1e-30, 3.4e38, -3.4e38,
};
static void fill_sat_f32(void) {
    float *a = (float *)A;
    size_t edges = sizeof EDGES / sizeof EDGES[0];
    for (size_t i = 0; i < BYTES / 4; i++) {
        if (i % 7 == 3) a[i] = (float)EDGES[(i / 7) % edges];
        else if (i % 61 == 5) a[i] = __builtin_nanf("");
        else if (i % 67 == 9) a[i] = (i & 1) ? __builtin_inff() : -__builtin_inff();
        else a[i] = ((int32_t)next() / 65536.0f) * ((i % 5 == 0) ? 131072.0f : 3.0f);
    }
}
static void fill_sat_f64(void) {
    double *a = (double *)A;
    size_t edges = sizeof EDGES / sizeof EDGES[0];
    for (size_t i = 0; i < BYTES / 8; i++) {
        if (i % 7 == 3) a[i] = EDGES[(i / 7) % edges];
        else if (i % 61 == 5) a[i] = __builtin_nan("");
        else if (i % 67 == 9) a[i] = (i & 1) ? __builtin_inf() : -__builtin_inf();
        else a[i] = ((int32_t)next() / 65536.0) * ((i % 5 == 0) ? 1e15 : 3.0);
    }
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e9 + t.tv_nsec;
}

/* A pointer into buffer X, one element off on odd passes. */
#define P(X, T, pass) ((T *)(X) + ((pass) & 1))

#define RUN(NAME, FILL, ELEMS, CALL, OUT, OUTBYTES)                              \
    do {                                                                         \
        FILL();                                                                  \
        memset(D, 0, sizeof D);                                                  \
        hash = 2166136261u;                                                      \
        for (int pass = 0, k = 7; pass < 2; pass++) { CALL; mix(OUT, OUTBYTES); }       \
        uint32_t h = hash;                                                       \
        double best = 1e30;                                                      \
        for (int r = 0; r < 5; r++) {                                            \
            double t0 = now();                                                   \
            for (int k = 0; k < reps; k++) { int pass = 0; CALL; }               \
            double t = now() - t0;                                               \
            if (t < best) best = t;                                              \
        }                                                                        \
        printf("%-16s %08x %.3f\n", NAME, (unsigned)h, best / reps / (ELEMS));   \
    } while (0)

int main(int argc, char **argv) {
    int reps = argc > 1 ? atoi(argv[1]) : 200;
    if (argc > 2 && !strcmp(argv[2], "msa"))
        __asm__ volatile(".set push\n.set fp=64\n.set msa\naddv.w $w0, $w0, $w0\n.set pop" ::: "$f0");
    static volatile uint32_t sink;
    int32_t r32 = 0;
    uint64_t r64 = 0;

    RUN("add_i32", fill_int, N, add_i32(P(D, int32_t, pass), P(A, int32_t, pass), P(B, int32_t, pass), N), D, N * 4 + 8);
    RUN("add_u8", fill_int, N, add_u8(P(D, uint8_t, pass), P(A, uint8_t, pass), P(B, uint8_t, pass), N), D, N + 8);
    RUN("mul_f32", fill_f32, N, mul_f32(P(D, float, pass), P(A, float, pass), 1.37f, N), D, N * 4 + 8);
    RUN("mul_f64", fill_f64, N, mul_f64(P(D, double, pass), P(A, double, pass), 1.37, N), D, N * 8 + 8);
    RUN("sum_i32", fill_int, N, (r32 = sum_i32(P(A, int32_t, pass), N), sink = r32), &r32, 4);
    RUN("dot_i16", fill_int, N, (r32 = dot_i16(P(A, int16_t, pass), P(B, int16_t, pass), N), sink = r32), &r32, 4);
    RUN("i16_to_f32", fill_int, N, i16_to_f32(P(D, float, pass), P(A, int16_t, pass), N), D, N * 4 + 8);
    RUN("blend_u8", fill_int, N, blend_u8(P(D, uint8_t, pass), P(A, uint8_t, pass), P(B, uint8_t, pass), N), D, N + 8);
    RUN("clamp_i32_i16", fill_int, N, clamp_i32_i16(P(D, int16_t, pass), P(A, int32_t, pass), N), D, N * 2 + 8);
    RUN("div_i32", fill_int, N, div_i32(P(D, int32_t, pass), P(A, int32_t, pass), P(B, int32_t, pass), N), D, N * 4 + 8);
    RUN("div_f32", fill_f32, N, div_f32(P(D, float, pass), P(A, float, pass), P(B, float, pass), N), D, N * 4 + 8);
    RUN("mul_i64", fill_int, N, mul_i64(P(D, int64_t, pass), P(A, int64_t, pass), P(B, int64_t, pass), N), D, N * 8 + 8);
    RUN("gather", fill_int, 256, gather(P(D, int32_t, pass), P(A, int32_t, pass), P(B, uint8_t, pass), 256), D, 256 * 4 + 8);
    RUN("pack_bytes", fill_int, 1, (r32 = pack_bytes(P(A, struct px, pass), pass + 3), sink = r32), &r32, 4);
    RUN("build_from_gprs", fill_int, 4, build_from_gprs(P(D, int32_t, pass), k + 1, k + 2, k + 3, k + 4, 5), D, 24);
    RUN("slp_store", fill_int, 4, slp_store(P(D, int32_t, pass), P(A, int32_t, pass), P(B, int32_t, pass)), D, 24);
    RUN("slp_f64", fill_f64, 2, slp_f64(P(D, double, pass), P(A, double, pass), P(B, double, pass)), D, 24);
    RUN("xorshift_u64", fill_int, 64, (r64 = xorshift_u64(0x9e3779b97f4a7c15ull + pass, 64), sink = (uint32_t)r64), &r64, 8);
    RUN("f32_to_i16", fill_f32, N, f32_to_i16(P(D, int16_t, pass), P(A, float, pass), N), D, N * 2 + 8);
    RUN("i32_to_u8", fill_int, N, i32_to_u8(P(D, uint8_t, pass), P(A, int32_t, pass), N), D, N + 8);
    RUN("u8_to_f32", fill_int, N, u8_to_f32(P(D, float, pass), P(A, uint8_t, pass), N), D, N * 4 + 8);
    RUN("i64_to_i16", fill_int, N, i64_to_i16(P(D, int16_t, pass), P(A, int64_t, pass), N), D, N * 2 + 8);
    RUN("f64_to_i32", fill_f64, N, f64_to_i32(P(D, int32_t, pass), P(A, double, pass), N), D, N * 4 + 8);
    RUN("i32_to_f64", fill_int, N, i32_to_f64(P(D, double, pass), P(A, int32_t, pass), N), D, N * 8 + 8);
    RUN("u16_to_u32", fill_int, N, u16_to_u32(P(D, uint32_t, pass), P(A, uint16_t, pass), N), D, N * 4 + 8);
    RUN("i8_to_i32", fill_int, N, i8_to_i32(P(D, int32_t, pass), P(A, int8_t, pass), N), D, N * 4 + 8);
    RUN("sat_f32_i32", fill_sat_f32, N, sat_f32_i32(P(D, int32_t, pass), P(A, float, pass), N), D, N * 4 + 8);
    RUN("sat_f32_u32", fill_sat_f32, N, sat_f32_u32(P(D, uint32_t, pass), P(A, float, pass), N), D, N * 4 + 8);
    RUN("sat_f32_i16", fill_sat_f32, N, sat_f32_i16(P(D, int16_t, pass), P(A, float, pass), N), D, N * 2 + 8);
    RUN("sat_f32_u8", fill_sat_f32, N, sat_f32_u8(P(D, uint8_t, pass), P(A, float, pass), N), D, N + 8);
    RUN("sat_f64_i32", fill_sat_f64, N, sat_f64_i32(P(D, int32_t, pass), P(A, double, pass), N), D, N * 4 + 8);
    RUN("sat_f64_i64", fill_sat_f64, N, sat_f64_i64(P(D, int64_t, pass), P(A, double, pass), N), D, N * 8 + 8);
    RUN("libm_log2", fill_f32, 255, libm_log2(P(D, float, pass), P(A, float, pass), P(B, float, pass), 255), D, 255 * 4 + 8);
    RUN("libm_exp2", fill_f32, 255, libm_exp2(P(D, float, pass), P(A, float, pass), P(B, float, pass), 255), D, 255 * 4 + 8);
    RUN("libm_fma", fill_f32, 255, libm_fma(P(D, float, pass), P(A, float, pass), P(B, float, pass), 255), D, 255 * 4 + 8);
    return 0;
}
