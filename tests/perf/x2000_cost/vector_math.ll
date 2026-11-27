; One MSA register of generic vector log2, exp2 and fma per call, and the
; MSA fmadd and fexp2 intrinsics (vector_math.c compares them with libm).
target datalayout = "e-m:m-p:32:32-i8:8:32-i16:16:32-i64:64-n32-S64"
target triple = "mipsel-unknown-linux-gnu"

define void @vm_log2_f32(ptr %d, ptr %a) {
  %x = load <4 x float>, ptr %a, align 16
  %r = call <4 x float> @llvm.log2.v4f32(<4 x float> %x)
  store <4 x float> %r, ptr %d, align 16
  ret void
}
define void @vm_exp2_f32(ptr %d, ptr %a) {
  %x = load <4 x float>, ptr %a, align 16
  %r = call <4 x float> @llvm.exp2.v4f32(<4 x float> %x)
  store <4 x float> %r, ptr %d, align 16
  ret void
}
define void @vm_fma_f32(ptr %d, ptr %a, ptr %b, ptr %c) {
  %x = load <4 x float>, ptr %a, align 16
  %y = load <4 x float>, ptr %b, align 16
  %z = load <4 x float>, ptr %c, align 16
  %r = call <4 x float> @llvm.fma.v4f32(<4 x float> %x, <4 x float> %y, <4 x float> %z)
  store <4 x float> %r, ptr %d, align 16
  ret void
}
define void @vm_log2_f64(ptr %d, ptr %a) {
  %x = load <2 x double>, ptr %a, align 16
  %r = call <2 x double> @llvm.log2.v2f64(<2 x double> %x)
  store <2 x double> %r, ptr %d, align 16
  ret void
}
define void @vm_exp2_f64(ptr %d, ptr %a) {
  %x = load <2 x double>, ptr %a, align 16
  %r = call <2 x double> @llvm.exp2.v2f64(<2 x double> %x)
  store <2 x double> %r, ptr %d, align 16
  ret void
}
define void @vm_fma_f64(ptr %d, ptr %a, ptr %b, ptr %c) {
  %x = load <2 x double>, ptr %a, align 16
  %y = load <2 x double>, ptr %b, align 16
  %z = load <2 x double>, ptr %c, align 16
  %r = call <2 x double> @llvm.fma.v2f64(<2 x double> %x, <2 x double> %y, <2 x double> %z)
  store <2 x double> %r, ptr %d, align 16
  ret void
}
; fmadd.w wd, ws, wt computes wd + ws * wt.
define void @vm_fmadd_w(ptr %d, ptr %a, ptr %b, ptr %c) {
  %x = load <4 x float>, ptr %a, align 16
  %y = load <4 x float>, ptr %b, align 16
  %z = load <4 x float>, ptr %c, align 16
  %r = call <4 x float> @llvm.mips.fmadd.w(<4 x float> %x, <4 x float> %y, <4 x float> %z)
  store <4 x float> %r, ptr %d, align 16
  ret void
}
; fexp2.w wd, ws, wt computes ws * 2^wt for integer wt.
define void @vm_fexp2_w(ptr %d, ptr %a, ptr %b) {
  %x = load <4 x float>, ptr %a, align 16
  %y = load <4 x i32>, ptr %b, align 16
  %r = call <4 x float> @llvm.mips.fexp2.w(<4 x float> %x, <4 x i32> %y)
  store <4 x float> %r, ptr %d, align 16
  ret void
}
