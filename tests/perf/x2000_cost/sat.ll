; Saturating float-to-integer conversions (Rust's `as`): C has no defined
; form of them, so the loops are IR. check.py compiles this file for both
; arms; bench.c feeds NaN, infinities and out-of-range values.
; EXPECT sat_f32_i32 vector: ftrunc_s.w saturates and gives 0 for a NaN
; EXPECT sat_f32_u32 vector: ftrunc_u.w
; EXPECT sat_f32_i16 vector: ftrunc_s.w, max_s.w, min_s.w, pckev.h
; EXPECT sat_f32_u8 vector: ftrunc_s.w, maxi_s.w, min_s.w, two pckev levels
; EXPECT sat_f64_i32 any: ftrunc_s.d, max_s.d, min_s.d, pckev.w
; EXPECT sat_f64_i64 any: ftrunc_s.d
;
; Calls the MSA lowering would miscompile if a vectorizer widened them
; (docs/x2000-toolchain.md, "Stock lowering that is wrong for generic IR"):
; the cost model returns an invalid cost for their vector forms.
; EXPECT libm_log2 any: llvm.log2 must stay a libm call (flog2 is logb)
; EXPECT libm_exp2 any: llvm.exp2 must stay a libm call (fexp2 takes an integer exponent)
; EXPECT libm_fma any: llvm.fma must stay a libm call (fmadd is selected as a + b * c)
target datalayout = "e-m:m-p:32:32-i8:8:32-i16:16:32-i64:64-n32-S64"
target triple = "mipsel-unknown-linux-gnu"

define void @sat_f32_i32(ptr noalias %d, ptr noalias %a, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %c = call i32 @llvm.fptosi.sat.i32.f32(float %x)
  %q = getelementptr inbounds i32, ptr %d, i32 %i
  store i32 %c, ptr %q, align 4
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @sat_f32_u32(ptr noalias %d, ptr noalias %a, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %c = call i32 @llvm.fptoui.sat.i32.f32(float %x)
  %q = getelementptr inbounds i32, ptr %d, i32 %i
  store i32 %c, ptr %q, align 4
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @sat_f32_i16(ptr noalias %d, ptr noalias %a, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %c = call i16 @llvm.fptosi.sat.i16.f32(float %x)
  %q = getelementptr inbounds i16, ptr %d, i32 %i
  store i16 %c, ptr %q, align 2
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @sat_f32_u8(ptr noalias %d, ptr noalias %a, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %c = call i8 @llvm.fptoui.sat.i8.f32(float %x)
  %q = getelementptr inbounds i8, ptr %d, i32 %i
  store i8 %c, ptr %q, align 1
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @sat_f64_i32(ptr noalias %d, ptr noalias %a, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds double, ptr %a, i32 %i
  %x = load double, ptr %p, align 8
  %c = call i32 @llvm.fptosi.sat.i32.f64(double %x)
  %q = getelementptr inbounds i32, ptr %d, i32 %i
  store i32 %c, ptr %q, align 4
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @sat_f64_i64(ptr noalias %d, ptr noalias %a, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds double, ptr %a, i32 %i
  %x = load double, ptr %p, align 8
  %c = call i64 @llvm.fptosi.sat.i64.f64(double %x)
  %q = getelementptr inbounds i64, ptr %d, i32 %i
  store i64 %c, ptr %q, align 8
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @libm_log2(ptr noalias %d, ptr noalias %a, ptr noalias %b, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %pb = getelementptr inbounds float, ptr %b, i32 %i
  %y = load float, ptr %pb, align 4
  %c = call float @llvm.log2.f32(float %y)
  %q = getelementptr inbounds float, ptr %d, i32 %i
  store float %c, ptr %q, align 4
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @libm_exp2(ptr noalias %d, ptr noalias %a, ptr noalias %b, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %pb = getelementptr inbounds float, ptr %b, i32 %i
  %y = load float, ptr %pb, align 4
  %s = fmul float %x, 0x3F50000000000000
  %c = call float @llvm.exp2.f32(float %s)
  %q = getelementptr inbounds float, ptr %d, i32 %i
  store float %c, ptr %q, align 4
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

define void @libm_fma(ptr noalias %d, ptr noalias %a, ptr noalias %b, i32 %n) {
entry:
  %z = icmp eq i32 %n, 0
  br i1 %z, label %exit, label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %p = getelementptr inbounds float, ptr %a, i32 %i
  %x = load float, ptr %p, align 4
  %pb = getelementptr inbounds float, ptr %b, i32 %i
  %y = load float, ptr %pb, align 4
  %c = call float @llvm.fma.f32(float %x, float %y, float 1.250000e+00)
  %q = getelementptr inbounds float, ptr %d, i32 %i
  store float %c, ptr %q, align 4
  %next = add nuw i32 %i, 1
  %done = icmp eq i32 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret void
}

declare float @llvm.log2.f32(float)
declare float @llvm.exp2.f32(float)
declare float @llvm.fma.f32(float, float, float)
declare i32 @llvm.fptosi.sat.i32.f32(float)
declare i32 @llvm.fptoui.sat.i32.f32(float)
declare i16 @llvm.fptosi.sat.i16.f32(float)
declare i8 @llvm.fptoui.sat.i8.f32(float)
declare i32 @llvm.fptosi.sat.i32.f64(double)
declare i64 @llvm.fptosi.sat.i64.f64(double)
