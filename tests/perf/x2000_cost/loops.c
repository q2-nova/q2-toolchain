/* Loops for the X2000 cost-model check (tests/perf/x2000_cost/check.py). Each
 * function is one decision the vectorizers make. EXPECT lines say what the
 * X2000 measurements (docs/x2000-msa.md) require:
 *   vector: the function must use MSA registers.
 *   scalar: it must not.
 *   any:    the measurements do not decide it; the check reports the choice
 *           and the Q2 timing (bench.c) says whether it was right.
 */
#include <stddef.h>
#include <stdint.h>

/* EXPECT add_i32 vector: addv.w 0.50 cycles per 4 lanes */
void add_i32(int32_t *restrict d, const int32_t *restrict a, const int32_t *restrict b, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] + b[i];
}
/* EXPECT add_u8 vector: addv.b 0.50 cycles per 16 lanes */
void add_u8(uint8_t *restrict d, const uint8_t *restrict a, const uint8_t *restrict b, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] + b[i];
}
/* EXPECT mul_f32 vector: fmul.w 0.625 cycles per 4 lanes */
void mul_f32(float *restrict d, const float *restrict a, float k, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * k;
}
/* EXPECT mul_f64 any: fmul.d 4.0 cycles per 2 lanes, mul.d 2.0 per lane */
void mul_f64(double *restrict d, const double *restrict a, double k, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * k;
}
/* EXPECT sum_i32 vector: addv.w reduction, interleaved chains */
int32_t sum_i32(const int32_t *a, size_t n) {
    int32_t s = 0;
    for (size_t i = 0; i < n; i++) s += a[i];
    return s;
}
/* EXPECT dot_i16 vector: widening multiply-add. 8 sign extensions per iteration: 1.35 of scalar
 * on the Q2 when their instructions were emitted chain by chain; the machine scheduler
 * interleaves them (without it the cost model keeps this loop scalar) */
int32_t dot_i16(const int16_t *a, const int16_t *b, size_t n) {
    int32_t s = 0;
    for (size_t i = 0; i < n; i++) s += a[i] * b[i];
    return s;
}
/* EXPECT i16_to_f32 vector: kernel 1.75 cycles per sample against 5.02 */
void i16_to_f32(float *restrict d, const int16_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * (1.0f / 32768.0f);
}
/* EXPECT blend_u8 vector: kernel 0.94 cycles per byte against 4.51 */
void blend_u8(uint8_t *restrict d, const uint8_t *restrict a, const uint8_t *restrict b, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = (uint8_t)((a[i] * b[i]) >> 8);
}
/* EXPECT clamp_i32_i16 vector: kernel 0.89 cycles per sample against 6.5 */
void clamp_i32_i16(int16_t *restrict d, const int32_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) {
        int32_t v = a[i];
        d[i] = v > 32767 ? 32767 : v < -32768 ? -32768 : v;
    }
}
/* EXPECT div_i32 any: div_s.w 66 cycles per 4 lanes, scalar div about 22 */
void div_i32(int32_t *restrict d, const int32_t *restrict a, const int32_t *restrict b, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] / (b[i] | 1);
}
/* EXPECT div_f32 any: fdiv.w 60 cycles per 4 lanes, div.s 15 per lane */
void div_f32(float *restrict d, const float *restrict a, const float *restrict b, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] / b[i];
}
/* EXPECT mul_i64 any: mulv.d 4.0 cycles per 2 lanes */
void mul_i64(int64_t *restrict d, const int64_t *restrict a, const int64_t *restrict b, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * b[i];
}
/* EXPECT gather any: no gather; 16 lbu, lw and insert.w per 4 st.w against 16 lbu, lw, sw */
void gather(int32_t *restrict d, const int32_t *restrict t, const uint8_t *restrict idx, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = t[idx[i]];
}
/* EXPECT pack_bytes scalar: 4 lanes extracted to GPRs to build one word */
struct px { uint8_t r, g, b, a; };
uint32_t pack_bytes(const struct px *p, unsigned k) {
    return ((p->r + k) & 255) | (((p->g + k) & 255) << 8) | (((p->b + k) & 255) << 16) | ((uint32_t)(p->a + k) << 24);
}
/* EXPECT build_from_gprs scalar: 4 insert.w from GPRs for one addv.w */
void build_from_gprs(int32_t *d, int32_t a, int32_t b, int32_t c, int32_t e, int32_t k) {
    d[0] = a + k; d[1] = b + k; d[2] = c + k; d[3] = e + k;
}
/* EXPECT slp_store vector: 2 ld.w, addv.w, st.w against 12 scalar instructions */
void slp_store(int32_t *restrict d, const int32_t *restrict a, const int32_t *restrict b) {
    d[0] = a[0] + b[0]; d[1] = a[1] + b[1]; d[2] = a[2] + b[2]; d[3] = a[3] + b[3];
}
/* EXPECT slp_f64 any: fmul.d and fadd.d cost per lane what mul.d and add.d cost */
void slp_f64(double *restrict d, const double *restrict a, const double *restrict b) {
    d[0] = a[0] * b[0] + 1.0; d[1] = a[1] * b[1] + 1.0;
}
/* EXPECT xorshift_u64 scalar: 64-bit state in a vector register crosses to GPRs */
uint64_t xorshift_u64(uint64_t x, unsigned n) {
    while (n--) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; }
    return x;
}

/* Conversions whose lowering the X2000 patch changes (pckev narrowing,
 * ffint/ftrunc at the float's element size). */
/* EXPECT f32_to_i16 vector: ftrunc_s.w, pckev.h */
void f32_to_i16(int16_t *restrict d, const float *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = (int16_t)(a[i] * 8.0f);
}
/* EXPECT i32_to_u8 vector: two pckev levels */
void i32_to_u8(uint8_t *restrict d, const int32_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = (uint8_t)(a[i] >> 3);
}
/* EXPECT u8_to_f32 vector: kernel 0.88 cycles per element against 3.52 */
void u8_to_f32(float *restrict d, const uint8_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * (1.0f / 255.0f);
}
/* EXPECT i64_to_i16 any: three pckev levels from v2i64 */
void i64_to_i16(int16_t *restrict d, const int64_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = (int16_t)(a[i] >> 5);
}
/* EXPECT f64_to_i32 any: ftrunc_s.d, pckev.w */
void f64_to_i32(int32_t *restrict d, const double *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = (int32_t)(a[i] * 4.0);
}
/* EXPECT i32_to_f64 any: sign extension to v2i64, ffint_s.d */
void i32_to_f64(double *restrict d, const int32_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * 0.5;
}
/* EXPECT u16_to_u32 vector: zero extension */
void u16_to_u32(uint32_t *restrict d, const uint16_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * 3u + 1u;
}
/* EXPECT i8_to_i32 vector: sign extension over two levels */
void i8_to_i32(int32_t *restrict d, const int8_t *restrict a, size_t n) {
    for (size_t i = 0; i < n; i++) d[i] = a[i] * 5 - 7;
}
