// Native test for host_accel.h. Run with `make test` from firmware/.
#include <cstdio>
#include <cstdlib>

#include "host_accel.h"

static int failures = 0;

static void check(bool ok, const char* what, double got, double want) {
    if (!ok) {
        printf("FAIL %s: got %f, want %f\n", what, got, want);
        failures++;
    }
}

// Expected host points per report magnitude, from a separate Python
// transcription of Apple's IOHIDParametricAcceleration over the same
// HIDAccelCurves table (tracking speed, then one value per entry of MAGNITUDES).
static const double MAGNITUDES[] = { 1, 2, 5, 10, 20, 40, 51, 52, 60, 89, 90, 91, 100, 400, 1000, 6136, 32767 };
static const struct { double tracking; double px[17]; } REFERENCE[] = {
    { 0, { 0.24, 0.48, 1.2, 2.4, 4.8, 9.6, 12.24, 12.48, 14.4, 21.36, 21.6, 21.84, 24, 96, 240, 1472.64, 7864.08 } },
    { 0.3, { 0.230804, 0.479397, 1.332198, 3.112002, 8.028832, 23.392203, 35.021961, 36.153197, 45.203081, 78.008912, 79.140148, 80.271384, 90.452504, 278.577119, 465.556054, 1186.489406, 2753.790655 } },
    { 1.0, { 0.275609, 0.641791, 2.286129, 6.860132, 22.989557, 83.998642, 134.44232, 139.615413, 181.048436, 331.243144, 336.414279, 341.519098, 384.424033, 1089.036243, 1806.221509, 4586.58152, 10639.518358 } },
    { 1.25, { 0.285283, 0.678144, 2.50543, 7.732644, 26.51402, 98.515739, 158.442591, 164.618499, 214.229859, 393.78213, 399.705947, 405.543243, 454.719281, 1271.578948, 2106.466192, 5345.919452, 12399.890492 } },
    { 3.0, { 0.382515, 1.051782, 4.805956, 17.039286, 65.080841, 264.512942, 438.962026, 457.265503, 610.561592, 1119.804195, 1134.053356, 1148.125688, 1267.766862, 3352.895297, 5523.699393, 13980.589215, 32414.917718 } },
    { 5.0, { 0.382515, 1.051782, 4.805956, 17.039286, 65.080841, 264.512942, 438.962026, 457.265503, 610.561592, 1119.804195, 1134.053356, 1148.125688, 1267.766862, 3352.895297, 5523.699393, 13980.589215, 32414.917718 } },
};

int main() {
    for (const auto& ref : REFERENCE) {
        host_accel_t a = host_accel_create(ref.tracking);
        for (size_t i = 0; i < sizeof(MAGNITUDES) / sizeof(MAGNITUDES[0]); i++) {
            double got = host_accel_px(a, MAGNITUDES[i]);
            check(fabs(got - ref.px[i]) < 1e-4 * (1 + ref.px[i]), "reference curve", got, ref.px[i]);
        }
    }

    host_accel_t a = host_accel_create(1.0);

    // Hardware measurements from the probe captures (tracking speed 1.0):
    // gain ~= 0.26 + 0.043*v at drag speeds, 0.73 px/count for a 6136 count report.
    for (int v = 2; v <= 16; v++) {
        double fit = 0.26 + 0.043 * v;
        check(fabs(host_accel_gain(a, v, 0) - fit) < 0.06, "measured low speed gain", host_accel_gain(a, v, 0), fit);
    }
    check(fabs(host_accel_gain(a, 6136, 0) - 0.73) < 0.03, "measured large report gain", host_accel_gain(a, 6136, 0), 0.73);

    // The curve never decreases, or host_accel_counts' bisection is invalid.
    for (int v = 1; v < 32767; v++) {
        check(host_accel_px(a, v + 1) >= host_accel_px(a, v), "monotonic", host_accel_px(a, v + 1), host_accel_px(a, v));
    }

    // host_accel_counts is the smallest magnitude reaching the distance.
    for (double px = 1; px < 10000; px = px * 1.37 + 1) {
        int32_t c = host_accel_counts(a, px);
        check(host_accel_px(a, c) >= px, "counts reach the distance", host_accel_px(a, c), px);
        if (c > 1) check(host_accel_px(a, c - 1) < px, "counts are minimal", host_accel_px(a, c - 1), px);
    }
    check(host_accel_counts(a, 1e9) == 32767, "counts cap at int16", host_accel_counts(a, 1e9), 32767);

    // Both axes share one gain, taken from the floored Euclidean magnitude.
    check(host_accel_gain(a, 3, 4) == host_accel_gain(a, 5, 0), "euclidean magnitude", host_accel_gain(a, 3, 4), host_accel_gain(a, 5, 0));
    check(host_accel_gain(a, 1, 1) == host_accel_gain(a, 1, 0), "floored magnitude", host_accel_gain(a, 1, 1), host_accel_gain(a, 1, 0));
    check(host_accel_gain(a, 0, 0) == 0, "no motion", host_accel_gain(a, 0, 0), 0);

    if (failures == 0) printf("host_accel: all checks passed\n");
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
