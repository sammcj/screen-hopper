#ifndef _HOST_ACCEL_H_
#define _HOST_ACCEL_H_

#include <cmath>
#include <cstdint>

// macOS pointer acceleration for a relative USB mouse, ported from Apple's
// IOHIDFamily (IOHIDParametricAcceleration::CreateWithParameters and
// ::multiplier, IOHIDPointerAccelerator::accelerate, in IOHIDEventSystemPlugIns).
//
// macOS scales each relative report on its own. The gain depends only on that
// report's magnitude, floor(sqrt(dx^2 + dy^2)). There is no history and no time
// term: those only apply to a device that publishes HIDPointerReportRate, and a
// USB mouse does not. So the firmware can work out where a report leaves the
// host pointer, and how many counts move it a given distance.
//
// No Pico SDK dependencies: tests/host_accel_test.cpp builds this natively.

struct host_accel_curve_t {
    double index;  // tracking speed this curve applies at
    double gain_linear, gain_parabolic, gain_cubic;
    double tangent_linear, tangent_root;
};

// HIDAccelCurves as macOS publishes it for a generic USB mouse (16.16 fixed in
// `ioreg -l`). Every curve has a linear tangent speed and none has a quartic
// gain, so those branches of Apple's code are not ported.
#define HOST_ACCEL_FX(v) ((v) / 65536.0)
static const host_accel_curve_t HOST_ACCEL_CURVES[] = {
    { HOST_ACCEL_FX(0),      HOST_ACCEL_FX(65536), HOST_ACCEL_FX(0),      HOST_ACCEL_FX(0),     HOST_ACCEL_FX(524288), HOST_ACCEL_FX(0) },
    { HOST_ACCEL_FX(8192),   HOST_ACCEL_FX(60293), HOST_ACCEL_FX(26214),  HOST_ACCEL_FX(5243),  HOST_ACCEL_FX(537395), HOST_ACCEL_FX(1245184) },
    { HOST_ACCEL_FX(32768),  HOST_ACCEL_FX(60948), HOST_ACCEL_FX(36045),  HOST_ACCEL_FX(6554),  HOST_ACCEL_FX(543949), HOST_ACCEL_FX(1179648) },
    { HOST_ACCEL_FX(45056),  HOST_ACCEL_FX(61604), HOST_ACCEL_FX(46531),  HOST_ACCEL_FX(7864),  HOST_ACCEL_FX(550502), HOST_ACCEL_FX(1114112) },
    { HOST_ACCEL_FX(57344),  HOST_ACCEL_FX(62259), HOST_ACCEL_FX(57672),  HOST_ACCEL_FX(9830),  HOST_ACCEL_FX(557056), HOST_ACCEL_FX(1048576) },
    { HOST_ACCEL_FX(65536),  HOST_ACCEL_FX(62915), HOST_ACCEL_FX(69468),  HOST_ACCEL_FX(11796), HOST_ACCEL_FX(563610), HOST_ACCEL_FX(983040) },
    { HOST_ACCEL_FX(98304),  HOST_ACCEL_FX(63570), HOST_ACCEL_FX(81920),  HOST_ACCEL_FX(14418), HOST_ACCEL_FX(570163), HOST_ACCEL_FX(917504) },
    { HOST_ACCEL_FX(131072), HOST_ACCEL_FX(64225), HOST_ACCEL_FX(95027),  HOST_ACCEL_FX(17695), HOST_ACCEL_FX(576717), HOST_ACCEL_FX(851968) },
    { HOST_ACCEL_FX(163840), HOST_ACCEL_FX(64881), HOST_ACCEL_FX(108790), HOST_ACCEL_FX(21627), HOST_ACCEL_FX(583270), HOST_ACCEL_FX(786432) },
    { HOST_ACCEL_FX(196608), HOST_ACCEL_FX(65536), HOST_ACCEL_FX(123208), HOST_ACCEL_FX(26214), HOST_ACCEL_FX(589824), HOST_ACCEL_FX(786432) },
};
#undef HOST_ACCEL_FX

struct host_accel_t {
    double gain_linear, gain_parabolic, gain_cubic;
    double tangent[2];  // standardised speeds where the curve changes segment
    double m[2], b[2];  // tangent line, then the square-root segment
};

// `tracking` is the host's tracking speed: `defaults read -g com.apple.mouse.scaling`.
inline host_accel_t host_accel_create(double tracking) {
    const int n = sizeof(HOST_ACCEL_CURVES) / sizeof(HOST_ACCEL_CURVES[0]);
    int cur = 0;
    for (int i = 0; i < n; i++) {
        if (tracking >= HOST_ACCEL_CURVES[i].index) cur = i;
    }
    host_accel_curve_t c = HOST_ACCEL_CURVES[cur];
    if (c.index < tracking && cur + 1 < n) {
        const host_accel_curve_t& next = HOST_ACCEL_CURVES[cur + 1];
        double ratio = (tracking - c.index) / (next.index - c.index);
        c.gain_linear += ratio * (next.gain_linear - c.gain_linear);
        c.gain_parabolic += ratio * (next.gain_parabolic - c.gain_parabolic);
        c.gain_cubic += ratio * (next.gain_cubic - c.gain_cubic);
        c.tangent_linear += ratio * (next.tangent_linear - c.tangent_linear);
        c.tangent_root += ratio * (next.tangent_root - c.tangent_root);
    }

    host_accel_t a = {};
    a.gain_linear = c.gain_linear;
    a.gain_parabolic = c.gain_parabolic;
    a.gain_cubic = c.gain_cubic;
    double t = c.tangent_linear;
    double y0 = c.gain_linear * t + pow(c.gain_parabolic * t, 2) + pow(c.gain_cubic * t, 3);
    a.m[0] = c.gain_linear + 2 * t * pow(c.gain_parabolic, 2) + 3 * pow(t, 2) * pow(c.gain_cubic, 3);
    a.b[0] = y0 - a.m[0] * t;
    a.tangent[0] = t;
    a.tangent[1] = HUGE_VAL;
    if (c.tangent_root != 0) {
        double y1 = a.m[0] * c.tangent_root + a.b[0];
        a.m[1] = 2 * y1 * a.m[0];
        a.b[1] = pow(y1, 2) - a.m[1] * c.tangent_root;
        a.tangent[1] = c.tangent_root;
    }
    return a;
}

// Host points moved by one relative report of magnitude `counts` (a whole
// number, as macOS floors the magnitude before the lookup).
inline double host_accel_px(const host_accel_t& a, double counts) {
    double x = counts * 67.0 / 400.0;  // counts per report at 400 dpi and Apple's 67 Hz frame rate
    double y;
    if (x <= a.tangent[0]) {
        y = a.gain_linear * x + pow(a.gain_parabolic * x, 2) + pow(a.gain_cubic * x, 3);
    } else if (x <= a.tangent[1]) {
        y = a.m[0] * x + a.b[0];
    } else {
        y = sqrt(a.m[1] * x + a.b[1]);
    }
    return y * 96.0 / 67.0;
}

// Host points per count for a report of (dx, dy): both axes scale by this.
inline double host_accel_gain(const host_accel_t& a, int32_t dx, int32_t dy) {
    double v = floor(sqrt((double) dx * dx + (double) dy * dy));
    if (v < 1) return 0;
    return host_accel_px(a, v) / v;
}

// Smallest report magnitude that moves the host at least `px` points. Capped at
// the int16 range of the relative report.
inline int32_t host_accel_counts(const host_accel_t& a, double px) {
    int32_t lo = 1, hi = 32767;
    while (lo < hi) {
        int32_t mid = (lo + hi) / 2;
        if (host_accel_px(a, mid) >= px) hi = mid; else lo = mid + 1;
    }
    return lo;
}

#endif
