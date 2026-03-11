#include <set>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <bsp/board.h>
#include <tusb.h>

#include "hardware/gpio.h"
#include "hardware/uart.h"
#include "pico/stdio.h"
#include "pico/stdlib.h"

#include "config.h"
#include "crc.h"
#include "descriptor_parser.h"
#include "globals.h"
#include "our_descriptor.h"
#include "remapper.h"
#include "serial.h"

#define FORWARDER_UART uart1
#define FORWARDER_TX_PIN 20

const uint8_t MAPPING_FLAG_STICKY = 0x01;

const uint8_t V_RESOLUTION_BITMASK = (1 << 0);
const uint8_t H_RESOLUTION_BITMASK = (1 << 2);
const uint32_t V_SCROLL_USAGE = 0x00010038;
const uint32_t H_SCROLL_USAGE = 0x000C0238;
const uint32_t MOUSE_X_USAGE = 0x00010030;
const uint32_t MOUSE_Y_USAGE = 0x00010031;
const uint32_t SWITCH_SCREEN_USAGE = 0xFFF20001;

// Profile control usages (vendor-defined). Bind any source key to these
// target_usages via the standard mappings array - same mechanism as
// SWITCH_SCREEN_USAGE.
const uint32_t CYCLE_PROFILE_USAGE = 0xFFF30000;             // next profile (mod profile_count)
const uint32_t ACTIVATE_PROFILE_USAGE_BASE = 0xFFF30001;     // 0xFFF30001..04 activate slots 0..3
const uint32_t TOGGLE_JIGGLE_USAGE = 0xFFF30010;             // flip the jiggler on/off

const uint8_t NLAYERS = 4;
const uint32_t LAYERS_USAGE_PAGE = 0xFFF10000;

const std::unordered_map<uint32_t, uint8_t> resolution_multiplier_masks = {
    { V_SCROLL_USAGE, V_RESOLUTION_BITMASK },
    { H_SCROLL_USAGE, H_RESOLUTION_BITMASK },
};

std::unordered_map<uint32_t, std::vector<map_source_t>> reverse_mapping;  // target -> sources list

std::unordered_map<uint8_t, std::unordered_map<uint32_t, usage_def_t>> our_usages;  // report_id -> usage -> usage_def
std::unordered_map<uint32_t, usage_def_t> our_usages_flat;

std::vector<uint32_t> layer_triggering_stickies;
std::vector<uint64_t> sticky_usages;  // non-layer triggering, layer << 32 | usage
std::vector<uint64_t> screen_switching_usages;
std::vector<uint64_t> cycle_profile_usages;        // layer << 32 | source_usage
std::vector<uint64_t> toggle_jiggle_usages;        // layer << 32 | source_usage
// Each activate-profile entry encodes (layer << 32 | source_usage) plus the
// destination slot index in the low byte of a separate parallel vector.
struct activate_profile_binding_t {
    uint64_t layer_usage;
    uint8_t slot;
};
std::vector<activate_profile_binding_t> activate_profile_usages;

// Profile switches must be deferred: activate_profile() calls
// set_mapping_from_config(), which .assign()s the very vectors the hotkey
// handlers iterate over - that would invalidate the for-loop iterators.
// Instead, the hotkey edge sets pending_profile_switch and the start of the
// next process_mapping() applies it cleanly with the old mappings already
// finished and the new ones ready to take effect on the very next frame.
static int8_t pending_profile_switch = -1;

// Forward declarations: process_mapping() kicks off cursor gestures (the
// jiggler-on confirmation sweep and the profile-number draw) and cancels the
// local gesture on real mouse input, but the gesture player's state and helpers
// live alongside maybe_jiggle() down near the end of the file.
static void start_jiggle_activation_sweep();
static void start_profile_number_gesture(uint8_t profile_index);
static void cancel_local_gesture();

// report_id -> ...
uint8_t* reports[MAX_INPUT_REPORT_ID + 1];
uint8_t* prev_reports[MAX_INPUT_REPORT_ID + 1];
uint8_t* report_masks_relative[MAX_INPUT_REPORT_ID + 1];
uint8_t* report_masks_absolute[MAX_INPUT_REPORT_ID + 1];
uint16_t report_sizes[MAX_INPUT_REPORT_ID + 1];

#define OR_BUFSIZE 8
uint8_t outgoing_reports[OR_BUFSIZE][CFG_TUD_HID_EP_BUFSIZE + 2];
uint8_t or_head = 0;
uint8_t or_tail = 0;
uint8_t or_items = 0;

// We need a certain part of mapping processing (absolute->relative mappings) to
// happen exactly once per millisecond. This variable keeps track of whether we
// already did it this time around. It is set to true when we receive
// start-of-frame from USB host.
volatile bool tick_pending;

std::vector<uint8_t> report_ids;

// usage -> ...
std::unordered_map<uint32_t, int32_t> input_state;
std::unordered_map<uint32_t, int32_t> prev_input_state;
std::unordered_map<uint64_t, int32_t> sticky_state;  // layer << 32 | usage -> state
std::unordered_map<uint32_t, int32_t> accumulated;   // * 1000

std::vector<uint32_t> relative_usages;
std::unordered_set<uint32_t> relative_usage_set;

std::unordered_map<uint32_t, int32_t> accumulated_scroll;
std::unordered_map<uint32_t, uint64_t> last_scroll_timestamp;

bool led_state;
uint64_t next_print = 0;
uint32_t reports_received;
uint32_t reports_sent;

// LED feedback state machine for the jiggler-toggle hotkey. While
// blink_transitions_remaining > 0, the main loop drives the onboard LED on a
// fixed cadence (BLINK_INTERVAL_US per transition) and suspends the per-report
// activity flicker so the user sees clear, countable blinks. When done, the
// activity flicker resumes. blink() schedules N pulses (each pulse = LED on
// then off, so N pulses = 2N transitions). 3 pulses signals "jiggler on",
// 5 pulses signals "jiggler off".
static const uint64_t BLINK_INTERVAL_US = 150000;  // 150 ms per LED transition
static const uint8_t BLINK_PULSES_ON = 3;
static const uint8_t BLINK_PULSES_OFF = 5;
uint8_t blink_transitions_remaining = 0;
uint64_t blink_next_transition_us = 0;
bool blink_led_state = false;

static void blink(uint8_t pulses) {
    blink_transitions_remaining = pulses * 2;
    blink_led_state = false;
    blink_next_transition_us = time_us_64();  // fire first transition immediately
}

static void service_blink() {
    if (blink_transitions_remaining == 0) return;
    uint64_t now = time_us_64();
    if (now < blink_next_transition_us) return;
    blink_led_state = !blink_led_state;
    board_led_write(blink_led_state);
    blink_next_transition_us = now + BLINK_INTERVAL_US;
    blink_transitions_remaining--;
    if (blink_transitions_remaining == 0) {
        // Land on LED off so the activity flicker has a known starting point.
        board_led_write(false);
        led_state = false;
    }
}

int64_t cursor_x = 0;
int64_t cursor_y = 0;

// Remember last active local screen for caps lock toggling
int8_t last_local_screen = -1;

int8_t active_screen = 0;

// Per-output timestamp (time_us_64) of the last real keyboard/mouse input from
// Pico B, indexed by the active screen's output (0 = local/USB, 1 = remote/forwarder).
// The keep-awake jiggle fires once the *target* output has been idle for
// jiggle_interval seconds. Tracking per-output means using one machine never
// resets the other's idle timer, so the inactive side keeps getting jiggled
// even while the active side is in use.
uint64_t last_input_per_output_us[2] = { 0, 0 };

// After a drag, hold off repositioning the cursor via the absolute report until
// the user moves again, so the post-drag correction doesn't snap while the hand
// is stationary. Cleared on movement and on any deliberate warp (F8/caps switch).
bool defer_abs_after_drag = false;

int64_t bounds_min_x;
int64_t bounds_max_x;
int64_t bounds_min_y;
int64_t bounds_max_y;

int32_t handle_scroll(uint32_t source_usage, uint32_t target_usage, int32_t movement) {
    int32_t ret = 0;
    if (resolution_multiplier & resolution_multiplier_masks.at(target_usage)) {  // hi-res
        ret = movement;
    } else {  // lo-res
        if (movement != 0) {
            last_scroll_timestamp[source_usage] = time_us_64();
            accumulated_scroll[source_usage] += movement;
            int ticks = accumulated_scroll[source_usage] / (1000 * RESOLUTION_MULTIPLIER);
            accumulated_scroll[source_usage] -= ticks * (1000 * RESOLUTION_MULTIPLIER);
            ret = ticks * 1000;
        } else {
            if ((accumulated_scroll[source_usage] != 0) &&
                (time_us_64() - last_scroll_timestamp[source_usage] > partial_scroll_timeout)) {
                accumulated_scroll[source_usage] = 0;
            }
        }
    }
    return ret;
}

inline int8_t get_bit(const uint8_t* data, int len, uint16_t bitpos) {
    int byte_no = bitpos / 8;
    int bit_no = bitpos % 8;
    if (byte_no < len) {
        return (data[byte_no] & 1 << bit_no) ? 1 : 0;
    }
    return 0;
}

inline uint32_t get_bits(const uint8_t* data, int len, uint16_t bitpos, uint8_t size) {
    uint32_t value = 0;
    for (int i = 0; i < size; i++) {
        value |= get_bit(data, len, bitpos + i) << i;
    }
    return value;
}

inline void put_bit(uint8_t* data, int len, uint16_t bitpos, uint8_t value) {
    int byte_no = bitpos / 8;
    int bit_no = bitpos % 8;
    if (byte_no < len) {
        data[byte_no] &= ~(1 << bit_no);
        data[byte_no] |= (value & 1) << bit_no;
    }
}

inline void put_bits(uint8_t* data, int len, uint16_t bitpos, uint8_t size, uint32_t value) {
    for (int i = 0; i < size; i++) {
        put_bit(data, len, bitpos + i, (value >> i) & 1);
    }
}

bool needs_to_be_sent(uint8_t report_id) {
    uint8_t* report = reports[report_id];
    uint8_t* prev_report = prev_reports[report_id];
    uint8_t* relative = report_masks_relative[report_id];
    uint8_t* absolute = report_masks_absolute[report_id];

    for (int i = 0; i < report_sizes[report_id]; i++) {
        if ((report[i] & relative[i]) || ((report[i] & absolute[i]) != (prev_report[i] & absolute[i]))) {
            return true;
        }
    }
    return false;
}

void set_mapping_from_config() {
    std::unordered_set<uint32_t> layer_triggering_sticky_set;
    std::unordered_set<uint64_t> sticky_usage_set;
    std::unordered_set<uint64_t> screen_switching_usages_set;
    std::unordered_set<uint64_t> cycle_profile_usages_set;
    std::unordered_set<uint64_t> toggle_jiggle_usages_set;
    std::unordered_set<uint32_t> mapped;

    reverse_mapping.clear();
    activate_profile_usages.clear();

    for (auto const& mapping : config_mappings) {
        reverse_mapping[mapping.target_usage].push_back((map_source_t){
            .usage = mapping.source_usage,
            .scaling = mapping.scaling,
            .sticky = (mapping.flags & MAPPING_FLAG_STICKY) != 0,
            .layer = (mapping.layer < NLAYERS) ? mapping.layer : (uint8_t) 0,
        });
        if (mapping.layer == 0) {
            mapped.insert(mapping.source_usage);
        }
        if ((mapping.flags & MAPPING_FLAG_STICKY) != 0) {
            if ((mapping.target_usage & 0xFFFF0000) == LAYERS_USAGE_PAGE) {
                layer_triggering_sticky_set.insert(mapping.source_usage);
            } else {
                sticky_usage_set.insert(((uint64_t) mapping.layer << 32) | mapping.source_usage);
            }
        }
        if (mapping.target_usage == SWITCH_SCREEN_USAGE) {
            screen_switching_usages_set.insert(((uint64_t) mapping.layer << 32) | mapping.source_usage);
        }
        if (mapping.target_usage == CYCLE_PROFILE_USAGE) {
            cycle_profile_usages_set.insert(((uint64_t) mapping.layer << 32) | mapping.source_usage);
        }
        if (mapping.target_usage == TOGGLE_JIGGLE_USAGE) {
            toggle_jiggle_usages_set.insert(((uint64_t) mapping.layer << 32) | mapping.source_usage);
        }
        if (mapping.target_usage >= ACTIVATE_PROFILE_USAGE_BASE &&
            mapping.target_usage < ACTIVATE_PROFILE_USAGE_BASE + NPROFILES) {
            // Match the codebase's compound-literal idiom (see map_source_t
            // pushes above) rather than C++20 bare designated initializers,
            // because the firmware builds with -std=c++17.
            activate_profile_usages.push_back((activate_profile_binding_t){
                .layer_usage = ((uint64_t) mapping.layer << 32) | mapping.source_usage,
                .slot = (uint8_t) (mapping.target_usage - ACTIVATE_PROFILE_USAGE_BASE),
            });
        }
    }

    layer_triggering_stickies.assign(layer_triggering_sticky_set.begin(), layer_triggering_sticky_set.end());
    sticky_usages.assign(sticky_usage_set.begin(), sticky_usage_set.end());
    screen_switching_usages.assign(screen_switching_usages_set.begin(), screen_switching_usages_set.end());
    cycle_profile_usages.assign(cycle_profile_usages_set.begin(), cycle_profile_usages_set.end());
    toggle_jiggle_usages.assign(toggle_jiggle_usages_set.begin(), toggle_jiggle_usages_set.end());

    if (unmapped_passthrough) {
        for (auto const& [usage, usage_def] : our_usages_flat) {
            if (!mapped.count(usage)) {
                reverse_mapping[usage].push_back((map_source_t){ .usage = usage });
            }
        }
    }
}

void screens_updated() {
    bounds_min_x = screens[0].x;
    bounds_max_x = screens[0].x + screens[0].w;
    bounds_min_y = screens[0].y;
    bounds_max_y = screens[0].y + screens[0].h;
    for (uint8_t i = 1; i < NSCREENS; i++) {
        bounds_min_x = std::min(bounds_min_x, (int64_t) screens[i].x);
        bounds_max_x = std::max(bounds_max_x, (int64_t) screens[i].x + screens[i].w);
        bounds_min_y = std::min(bounds_min_y, (int64_t) screens[i].y);
        bounds_max_y = std::max(bounds_max_y, (int64_t) screens[i].y + screens[i].h);
    }

    // Default to the first local screen (output=0), not the remote
    active_screen = 0;
    last_local_screen = -1;
    for (int i = 0; i < NSCREENS; i++) {
        if (screens.count(i) && screens[i].w > 0 && screens[i].output == 0) {
            if (last_local_screen == -1) {
                last_local_screen = i;
                active_screen = i;
            }
            break;
        }
    }
    cursor_x = screens[active_screen].x + screens[active_screen].w / 2;
    cursor_y = screens[active_screen].y + screens[active_screen].h / 2;
}

bool differ_on_absolute(const uint8_t* report1, const uint8_t* report2, uint8_t report_id) {
    uint8_t* absolute = report_masks_absolute[report_id];

    for (int i = 0; i < report_sizes[report_id]; i++) {
        if ((report1[i] & absolute[i]) != (report2[i] & absolute[i])) {
            return true;
        }
    }

    return false;
}

void aggregate_relative(uint8_t* prev_report, const uint8_t* report, uint8_t report_id) {
    for (auto const& [usage, usage_def] : our_usages[report_id]) {
        if (usage_def.is_relative) {
            int32_t val1 = get_bits(report, report_sizes[report_id], usage_def.bitpos, usage_def.size);
            if (usage_def.logical_minimum < 0) {
                if (val1 & (1 << (usage_def.size - 1))) {
                    val1 |= 0xFFFFFFFF << usage_def.size;
                }
            }
            if (val1) {
                int32_t val2 = get_bits(prev_report, report_sizes[report_id], usage_def.bitpos, usage_def.size);
                if (usage_def.logical_minimum < 0) {
                    if (val2 & (1 << (usage_def.size - 1))) {
                        val2 |= 0xFFFFFFFF << usage_def.size;
                    }
                }

                put_bits(prev_report, report_sizes[report_id], usage_def.bitpos, usage_def.size, val1 + val2);
            }
        }
    }
}

bool within_bounds(int64_t x, int64_t y, int8_t& active_screen) {
    active_screen = -1;
    for (uint8_t i = 0; i < NSCREENS; i++) {
        if (screens[i].w == 0) continue;  // skip unconfigured screens
        if (screens[i].x <= x &&
            x < screens[i].x + screens[i].w &&
            screens[i].y <= y &&
            y < screens[i].y + screens[i].h) {
            active_screen = i;
            break;
        }
    }

    return ((constraint_mode == ConstraintMode::VISIBLE && active_screen != -1) ||
            (constraint_mode == ConstraintMode::BOUNDING_BOX &&
                x >= bounds_min_x &&
                x < bounds_max_x &&
                y >= bounds_min_y &&
                y < bounds_max_y) ||
            (constraint_mode == ConstraintMode::NO_CONSTRAINT));
}

void queue_relative_movement(int16_t dx, int16_t dy, int16_t wheel, int16_t pan, uint8_t screen_idx) {
    // Byte 0 of the relative report is the 8-button bitmap (same usages as the
    // absolute report, same device). Carry the buttons built this frame so a
    // relative drag/crossing burst holds the button instead of looking like a
    // release. Layout (payload): [buttons][X:16][Y:16][Wheel:16][AC Pan:16] -
    // matches REPORT_ID_RELATIVE in our_descriptor.cc.
    uint8_t buttons = reports[REPORT_ID_MOUSE][0];

    // Coalesce into the newest un-sent relative report for the same screen and
    // button state. process_mapping runs once per incoming mouse report, so a
    // high-rate mouse produces relative deltas faster than the USB endpoint
    // drains them; without coalescing they back up in the ring buffer and keep
    // moving the cursor after the mouse stops or the button is released (drag
    // overshoot). Summing into one pending report bounds that to a single
    // residual delta. A button change writes a different buttons byte, so the
    // press/release edges stay as their own reports.
    if (or_items > 0) {
        uint8_t prev = (or_tail + OR_BUFSIZE - 1) % OR_BUFSIZE;
        if (outgoing_reports[prev][1] == REPORT_ID_RELATIVE &&
            outgoing_reports[prev][0] == screen_idx &&
            outgoing_reports[prev][2] == buttons) {
            int32_t px = (int16_t) (outgoing_reports[prev][3] | (outgoing_reports[prev][4] << 8));
            int32_t py = (int16_t) (outgoing_reports[prev][5] | (outgoing_reports[prev][6] << 8));
            int32_t pw = (int16_t) (outgoing_reports[prev][7] | (outgoing_reports[prev][8] << 8));
            int32_t pp = (int16_t) (outgoing_reports[prev][9] | (outgoing_reports[prev][10] << 8));
            int32_t nx = px + dx;
            int32_t ny = py + dy;
            int32_t nw = pw + wheel;
            int32_t np = pp + pan;
            if (nx < -32768) nx = -32768; else if (nx > 32767) nx = 32767;
            if (ny < -32768) ny = -32768; else if (ny > 32767) ny = 32767;
            if (nw < -32768) nw = -32768; else if (nw > 32767) nw = 32767;
            if (np < -32768) np = -32768; else if (np > 32767) np = 32767;
            outgoing_reports[prev][3] = nx & 0xFF;
            outgoing_reports[prev][4] = (nx >> 8) & 0xFF;
            outgoing_reports[prev][5] = ny & 0xFF;
            outgoing_reports[prev][6] = (ny >> 8) & 0xFF;
            outgoing_reports[prev][7] = nw & 0xFF;
            outgoing_reports[prev][8] = (nw >> 8) & 0xFF;
            outgoing_reports[prev][9] = np & 0xFF;
            outgoing_reports[prev][10] = (np >> 8) & 0xFF;
            return;
        }
    }

    if (or_items >= OR_BUFSIZE) return;
    outgoing_reports[or_tail][0] = screen_idx;
    outgoing_reports[or_tail][1] = REPORT_ID_RELATIVE;
    memset(outgoing_reports[or_tail] + 2, 0, report_sizes[REPORT_ID_RELATIVE]);
    outgoing_reports[or_tail][2] = buttons;
    outgoing_reports[or_tail][3] = dx & 0xFF;
    outgoing_reports[or_tail][4] = (dx >> 8) & 0xFF;
    outgoing_reports[or_tail][5] = dy & 0xFF;
    outgoing_reports[or_tail][6] = (dy >> 8) & 0xFF;
    outgoing_reports[or_tail][7] = wheel & 0xFF;
    outgoing_reports[or_tail][8] = (wheel >> 8) & 0xFF;
    outgoing_reports[or_tail][9] = pan & 0xFF;
    outgoing_reports[or_tail][10] = (pan >> 8) & 0xFF;
    or_tail = (or_tail + 1) % OR_BUFSIZE;
    or_items++;
}

// Queue a zeroed report (no buttons, keys or motion) of report_id tagged to
// screen_idx. Used to release whatever is held on the machine we're leaving when
// the cursor crosses to the other computer - see the output-switch flush in
// process_mapping.
void queue_zero_report(uint8_t report_id, uint8_t screen_idx) {
    if (or_items >= OR_BUFSIZE) return;
    outgoing_reports[or_tail][0] = screen_idx;
    outgoing_reports[or_tail][1] = report_id;
    memset(outgoing_reports[or_tail] + 2, 0, report_sizes[report_id]);
    or_tail = (or_tail + 1) % OR_BUFSIZE;
    or_items++;
}

// On a drag release, discard any drag motion that was queued but not yet sent
// to the host. process_mapping advances the internal cursor the moment it
// queues a relative report, but the host only moves when the report is actually
// sent over USB (or serial); a fast drag queues motion faster than the ~1ms
// endpoint drains it, so reports left in the ring buffer at release drain
// *after* the button-up - the cursor keeps shooting in the drag direction. Zero
// their motion (leaving the button bytes so the release still registers) and
// rewind the internal cursor by the same amount, so the absolute report sent
// next frame lands where the host actually is instead of snapping forward.

// Effective dead-reckoning gain (milli) for one flush of relative motion.
// macOS pointer-accel gain (host px rendered per emitted relative count) is
// AFFINE in the per-flush speed v = |rx| + |ry| (measured 2026-05-29 at 993Hz
// via the probe, gain ~= 0.26 + 0.043*v, rising past 1.0 at speed):
//   gain(v) = base + slope*v,  clamped to [0, DRAG_GAIN_CAP_MILLI/1000].
// The earlier saturating curve plateau*v^2/(v^2+k) was the wrong shape - it caps
// at the plateau and can't follow the rising line, so it under-advanced fast
// drags and the post-release report snapped backward. `base_milli` = base*1000
// (the screen's drag_gain field); `slope_e4` = slope*1e4 (the screen's
// drag_curve_k field, reused under CONFIG_VERSION 11). Returns milli; caller /1000.
#define DRAG_GAIN_CAP_MILLI 1300   // ceiling ~1.3 (measured high-speed gain ~1.25)
static inline int64_t drag_advance_gain(int64_t rx, int64_t ry, int64_t base_milli, int64_t slope_e4) {
    int64_t v = (rx < 0 ? -rx : rx) + (ry < 0 ? -ry : ry);
    int64_t gain = base_milli + slope_e4 * v / 10;  // slope_e4*v/10 == slope*v*1000 (milli)
    if (gain > DRAG_GAIN_CAP_MILLI) gain = DRAG_GAIN_CAP_MILLI;
    if (gain < 0) gain = 0;
    return gain;
}

void drop_unsent_relative_motion() {
    for (uint8_t k = 0; k < or_items; k++) {
        uint8_t i = (or_head + k) % OR_BUFSIZE;
        if (outgoing_reports[i][1] != REPORT_ID_RELATIVE) {
            continue;
        }
        int16_t rx = (int16_t) (outgoing_reports[i][3] | (outgoing_reports[i][4] << 8));
        int16_t ry = (int16_t) (outgoing_reports[i][5] | (outgoing_reports[i][6] << 8));
        if (rx == 0 && ry == 0) {
            continue;
        }
        uint8_t screen_idx = outgoing_reports[i][0];
        int64_t scale = (screens.count(screen_idx) && screens[screen_idx].scale > 0)
                            ? (int64_t) screens[screen_idx].scale
                            : coord_scale;
        // Mirror the gain applied when the cursor was advanced for this report
        // (see the drag flush) so dropping unsent motion exactly undoes it.
        int64_t base_milli = screens.count(screen_idx) ? (int64_t) screens[screen_idx].drag_gain : 0;
        int64_t slope_e4 = screens.count(screen_idx) ? (int64_t) screens[screen_idx].drag_curve_k : 0;
        if (base_milli == 0 && slope_e4 == 0) base_milli = 1000;  // flat 1.0 default
        int64_t gain = drag_advance_gain(rx, ry, base_milli, slope_e4);
        cursor_x -= (int64_t) rx * scale * gain / 1000;
        cursor_y -= (int64_t) ry * scale * gain / 1000;
        outgoing_reports[i][3] = 0;
        outgoing_reports[i][4] = 0;
        outgoing_reports[i][5] = 0;
        outgoing_reports[i][6] = 0;
    }
}

void process_mapping(bool auto_repeat) {
    if (suspended) {
        return;
    }

    // Apply any profile switch queued by a previous frame's hotkey edge. Doing
    // this before any iteration over derived mapping vectors guarantees no
    // iterator is invalidated mid-frame by the rebuild in set_mapping_from_config.
    if (pending_profile_switch >= 0) {
        uint8_t target = (uint8_t) pending_profile_switch;
        pending_profile_switch = -1;
        activate_profile(target);
        defer_abs_after_drag = false;
        // Draw the now-active layout's number on the main computer so the user
        // can see which preset they switched to. activate_profile() re-centres
        // the cursor on the first local screen, so the glyph is drawn from there.
        start_profile_number_gesture(get_active_profile());
    }

    for (auto const& usage : layer_triggering_stickies) {
        if ((prev_input_state[usage] == 0) && (input_state[usage] != 0)) {
            sticky_state[usage] = !sticky_state[usage];
        }
        prev_input_state[usage] = input_state[usage];
    }

    static bool layer_state[NLAYERS];
    // layer triggers work on all layers (no matter what layer they are defined on)
    // they can be sticky
    layer_state[0] = true;
    for (int i = 1; i < NLAYERS; i++) {
        layer_state[i] = false;
        for (auto const& map_source : reverse_mapping[LAYERS_USAGE_PAGE | i]) {
            if (map_source.sticky ? sticky_state[map_source.usage] : input_state[map_source.usage]) {
                layer_state[i] = true;
                layer_state[0] = false;
                break;
            }
        }
    }

    for (auto const& layer_usage : sticky_usages) {
        uint32_t usage = layer_usage & 0xFFFFFFFF;
        uint32_t layer = layer_usage >> 32;
        if (layer_state[layer]) {
            if ((prev_input_state[usage] == 0) && (input_state[usage] != 0)) {
                sticky_state[layer_usage] = !sticky_state[layer_usage];
            }
        }
        prev_input_state[usage] = input_state[usage];
    }

    for (auto const& layer_usage : screen_switching_usages) {
        uint32_t usage = layer_usage & 0xFFFFFFFF;
        uint32_t layer = layer_usage >> 32;
        if (layer_state[layer]) {
            if ((prev_input_state[usage] == 0) && (input_state[usage] != 0)) {
                // Toggle between local (output=0) and remote (output=1) computers.
                // Don't cycle through individual local screens - those are
                // handled by boundary crossing via mouse movement.
                uint8_t current_output = (screens.count(active_screen) ? screens[active_screen].output : 0);
                if (current_output == 0) {
                    // Currently on local -> switch to remote
                    last_local_screen = active_screen;
                    // Find first remote screen (output=1)
                    for (int i = 0; i < NSCREENS; i++) {
                        if (screens.count(i) && screens[i].w > 0 && screens[i].output == 1) {
                            active_screen = i;
                            break;
                        }
                    }
                } else {
                    // Currently on remote -> switch back to last local screen
                    if (last_local_screen != -1 && screens.count(last_local_screen) && screens[last_local_screen].w > 0) {
                        active_screen = last_local_screen;
                    } else {
                        // Fallback: find first local screen
                        for (int i = 0; i < NSCREENS; i++) {
                            if (screens.count(i) && screens[i].w > 0 && screens[i].output == 0) {
                                active_screen = i;
                                break;
                            }
                        }
                    }
                }
                cursor_x = screens[active_screen].x + screens[active_screen].w / 2;
                cursor_y = screens[active_screen].y + screens[active_screen].h / 2;
                // A deliberate switch warps the cursor to the new screen centre;
                // don't let a pending post-drag hold suppress that reposition.
                defer_abs_after_drag = false;
            }
        }
        prev_input_state[usage] = input_state[usage];
    }

    // Cycle to the next configured profile on a rising edge. The actual
    // activation is deferred to the start of the next process_mapping call -
    // see the pending_profile_switch comment.
    for (auto const& layer_usage : cycle_profile_usages) {
        uint32_t usage = layer_usage & 0xFFFFFFFF;
        uint32_t layer = layer_usage >> 32;
        if (layer_state[layer]) {
            if ((prev_input_state[usage] == 0) && (input_state[usage] != 0)) {
                uint8_t count = get_profile_count();
                if (count > 1) {
                    pending_profile_switch = (int8_t) ((get_active_profile() + 1) % count);
                }
            }
        }
        prev_input_state[usage] = input_state[usage];
    }

    // Activate a specific profile directly. Each binding carries its target
    // slot number, so multiple keys can each jump to a different profile.
    // Like cycle, the switch is deferred to next frame so we don't invalidate
    // the iterator over activate_profile_usages while we're still inside it.
    for (auto const& binding : activate_profile_usages) {
        uint32_t usage = binding.layer_usage & 0xFFFFFFFF;
        uint32_t layer = binding.layer_usage >> 32;
        if (layer_state[layer]) {
            if ((prev_input_state[usage] == 0) && (input_state[usage] != 0)) {
                if (binding.slot < get_profile_count()) {
                    pending_profile_switch = (int8_t) binding.slot;
                }
            }
        }
        prev_input_state[usage] = input_state[usage];
    }

    // Toggle the jiggler runtime gate. The persisted jiggle_interval is left
    // alone; this is a live override that resets to enabled on every boot.
    // Feedback: 3 LED pulses for "on", 5 pulses for "off" so the user can
    // confirm the toggle without looking at a screen.
    for (auto const& layer_usage : toggle_jiggle_usages) {
        uint32_t usage = layer_usage & 0xFFFFFFFF;
        uint32_t layer = layer_usage >> 32;
        if (layer_state[layer]) {
            if ((prev_input_state[usage] == 0) && (input_state[usage] != 0)) {
                jiggle_enabled = !jiggle_enabled;
                blink(jiggle_enabled ? BLINK_PULSES_ON : BLINK_PULSES_OFF);
                // Only on OFF -> ON: kick a one-shot cursor sweep on the
                // forwarder so the toggle landing is visible on the remote
                // machine without looking at the LED.
                if (jiggle_enabled) {
                    start_jiggle_activation_sweep();
                }
            }
        }
        prev_input_state[usage] = input_state[usage];
    }

    for (auto const& [target, sources] : reverse_mapping) {
        auto search = our_usages_flat.find(target);
        if (search == our_usages_flat.end()) {
            continue;
        }
        const usage_def_t& our_usage = search->second;
        if (our_usage.is_relative || target == MOUSE_X_USAGE || target == MOUSE_Y_USAGE) {
            for (auto const& map_source : sources) {
                bool source_is_relative = relative_usage_set.count(map_source.usage);
                if (auto_repeat || source_is_relative) {
                    int32_t value = 0;
                    if (map_source.sticky) {
                        value = sticky_state[((uint64_t) map_source.layer << 32) | map_source.usage] * map_source.scaling;
                    } else {
                        if (layer_state[map_source.layer]) {
                            value = (source_is_relative
                                            ? input_state[map_source.usage]
                                            : !!input_state[map_source.usage]) *
                                    map_source.scaling;
                        }
                    }
                    if (value != 0) {
                        if (target == V_SCROLL_USAGE || target == H_SCROLL_USAGE) {
                            accumulated[target] += handle_scroll(map_source.usage, target, value * RESOLUTION_MULTIPLIER);
                        } else {
                            accumulated[target] += value;
                        }
                    }
                }
            }
        } else {
            int32_t value = 0;
            for (auto const& map_source : sources) {
                if (map_source.sticky && (sticky_state[((uint64_t) map_source.layer << 32) | map_source.usage] != 0)) {
                    value = sticky_state[((uint64_t) map_source.layer << 32) | map_source.usage];
                } else {
                    if ((layer_state[map_source.layer]) &&
                        (relative_usage_set.count(map_source.usage)
                                ? (input_state[map_source.usage] * map_source.scaling > 0)
                                : input_state[map_source.usage])) {
                        value = 1;
                    }
                }
            }
            if (value) {
                put_bits((uint8_t*) reports[our_usage.report_id], report_sizes[our_usage.report_id], our_usage.bitpos, our_usage.size, value);
            }
        }
    }

    for (auto usage : relative_usages) {
        input_state[usage] = 0;
    }

    // Proper fractional accumulator for cursor movement. Scale this frame's
    // input by sensitivity, emit whole internal units, and carry the sub-unit
    // remainder to the next frame. The previous "accumulated -= dx" mixed raw
    // input units with sensitivity-scaled units; they only cancel at
    // sensitivity == 1000, otherwise the residual feeds back on itself and the
    // cursor oscillates (and drifts/shakes when the mouse stops).
    static int64_t cursor_frac_x = 0;
    static int64_t cursor_frac_y = 0;
    int64_t sensitivity = screens[active_screen].sensitivity;
    int64_t prod_x = cursor_frac_x + (int64_t) accumulated[MOUSE_X_USAGE] * sensitivity;
    int64_t prod_y = cursor_frac_y + (int64_t) accumulated[MOUSE_Y_USAGE] * sensitivity;
    int64_t dx = prod_x / 1000;
    int64_t dy = prod_y / 1000;
    cursor_frac_x = prod_x - dx * 1000;  // bounded |remainder| < 1000, never diverges
    cursor_frac_y = prod_y - dy * 1000;
    int64_t new_cursor_x = cursor_x + dx;
    int64_t new_cursor_y = cursor_y + dy;
    // Fully consume the input accumulator so it can't feed back here, and so
    // the relative-output loop below doesn't re-apply it to the absolute report.
    accumulated[MOUSE_X_USAGE] = 0;
    accumulated[MOUSE_Y_USAGE] = 0;

    // Real cursor movement aborts any confirmation gesture playing on the main
    // computer so it doesn't fight the user. The gesture injects motion straight
    // into the outgoing ring, never through dx/dy, so it can't cancel itself.
    if (dx != 0 || dy != 0) {
        cancel_local_gesture();
    }

    // The button state built this frame decides how the cursor moves: with no
    // button held we position via the absolute report; with a button held we
    // drag via the relative report. Keeping the two paths fully separate is
    // what stops the post-release drift - see the drag branch.
    static uint8_t prev_frame_buttons = 0;
    uint8_t held_buttons = reports[REPORT_ID_MOUSE][0];
    bool buttons_changed = (held_buttons != prev_frame_buttons);
    bool dragging = (held_buttons != 0 || buttons_changed);

    int8_t prev_active_screen = active_screen;
    bool suppress_abs_mouse = false;

    // Drag speed is per-screen: the remote display is scaled differently from
    // the local ones, so relative motion must use the active screen's own
    // internal-units-per-pixel (falling back to the global coord_scale).
    int64_t drag_scale = (active_screen >= 0 && screens.count(active_screen) && screens[active_screen].scale > 0)
                             ? (int64_t) screens[active_screen].scale
                             : coord_scale;

    // Host pointer tracking gain (speed-dependent). The host renders a number of
    // pixels per emitted relative count that rises affinely with drag speed
    // (macOS pointer acceleration), so the internal cursor is advanced by
    // base + slope*v per count (see drag_advance_gain). drag_gain holds base*1000,
    // drag_curve_k holds slope*1e4 (CONFIG_VERSION 11). Both 0 = flat 1.0.
    // Per-output: the active screen's `output` selects which host these apply to.
    int64_t drag_base = (active_screen >= 0 && screens.count(active_screen))
                            ? (int64_t) screens[active_screen].drag_gain : 0;
    int64_t drag_slope = (active_screen >= 0 && screens.count(active_screen))
                            ? (int64_t) screens[active_screen].drag_curve_k : 0;
    if (drag_base == 0 && drag_slope == 0) drag_base = 1000;  // flat 1.0 default (v8 behaviour)

    static int64_t edge_push = 0;

    if (!dragging) {
        // No button held: absolute positioning. The cursor crosses freely
        // between all screens, including onto the remote computer when it
        // reaches the edge the remote sits on. F8/caps lock remains an explicit
        // toggle (and an escape hatch if the remote is offline).
        int8_t new_active_screen;
        int64_t land_x = cursor_x;
        int64_t land_y = cursor_y;
        int8_t land_screen = active_screen;
        if (within_bounds(new_cursor_x, new_cursor_y, new_active_screen)) {
            land_x = new_cursor_x;
            land_y = new_cursor_y;
            land_screen = new_active_screen;
        } else if (within_bounds(cursor_x, new_cursor_y, new_active_screen)) {  // so the cursor doesn't snag on edges
            land_y = new_cursor_y;
            land_screen = new_active_screen;
        } else if (within_bounds(new_cursor_x, cursor_y, new_active_screen)) {
            land_x = new_cursor_x;
            land_screen = new_active_screen;
        }

        // Edge resistance: crossing to a screen on the *other* computer (a
        // different output) requires pushing past the edge by edge_resistance
        // internal units. Same-output hops cross instantly. While the push is
        // below the threshold we pin the cursor to the current screen's edge
        // and accumulate the attempted travel; once it passes the threshold the
        // crossing goes through and the accumulator resets.
        bool cross_output = (land_screen != active_screen) && active_screen != -1 && land_screen != -1 &&
                            screens.count(land_screen) && screens.count(active_screen) &&
                            screens[land_screen].output != screens[active_screen].output;
        if (cross_output && edge_resistance > 0) {
            edge_push += (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
            if (edge_push < (int64_t) edge_resistance) {
                land_screen = active_screen;
                int64_t min_x = screens[active_screen].x;
                int64_t max_x = screens[active_screen].x + screens[active_screen].w - 1;
                int64_t min_y = screens[active_screen].y;
                int64_t max_y = screens[active_screen].y + screens[active_screen].h - 1;
                land_x = new_cursor_x < min_x ? min_x : (new_cursor_x > max_x ? max_x : new_cursor_x);
                land_y = new_cursor_y < min_y ? min_y : (new_cursor_y > max_y ? max_y : new_cursor_y);
            } else {
                edge_push = 0;
            }
        } else if (dx != 0 || dy != 0) {
            // Only reset on real movement that isn't a cross attempt.
            // process_mapping also runs on every idle 1ms tick (dx==dy==0);
            // resetting on those would wipe the accumulated push between mouse
            // reports and the crossing could never build to the threshold.
            edge_push = 0;
        }

        cursor_x = land_x;
        cursor_y = land_y;
        active_screen = land_screen;

        // Boundary crossing between same-output screens: push the cursor across
        // the macOS display edge with a relative burst, then absolute
        // positioning takes over on the new display. (During a drag the real
        // relative drag motion already carries the cursor across, so the burst
        // is only needed here on the no-button path.)
        static const int16_t CROSSING_DELTA = 100;
        if (active_screen != -1 && prev_active_screen != -1 &&
            active_screen != prev_active_screen &&
            screens.count(active_screen) && screens.count(prev_active_screen) &&
            screens[active_screen].output == screens[prev_active_screen].output) {

            int16_t rel_dx = 0;
            int16_t rel_dy = 0;
            int64_t prev_cx = screens[prev_active_screen].x + screens[prev_active_screen].w / 2;
            int64_t new_cx = screens[active_screen].x + screens[active_screen].w / 2;
            int64_t prev_cy = screens[prev_active_screen].y + screens[prev_active_screen].h / 2;
            int64_t new_cy = screens[active_screen].y + screens[active_screen].h / 2;

            if (new_cx > prev_cx) rel_dx = CROSSING_DELTA;
            else if (new_cx < prev_cx) rel_dx = -CROSSING_DELTA;
            if (new_cy > prev_cy) rel_dy = CROSSING_DELTA;
            else if (new_cy < prev_cy) rel_dy = -CROSSING_DELTA;

            queue_relative_movement(rel_dx, rel_dy, 0, 0, active_screen);
        }
    } else {
        // Button held: drag via the relative report. macOS won't synthesise
        // drag events from absolute warps, and only registers a button
        // transition that arrives with motion, so a held button (and its
        // press/release edges) must move via relative.
        //
        // The internal absolute cursor is advanced ONLY by the relative motion
        // we actually emit - never by raw input that wasn't rendered. The
        // absolute report is suppressed while a button is held, so if cursor_x
        // tracked raw hand motion that macOS never saw, the absolute report sent
        // the moment the button releases would snap the cursor forward by that
        // unrendered motion. That snap was the drift after a click/drag.
        //
        // macOS applies pointer acceleration to relative input but not to
        // absolute positioning, so the tiny hand-jitter of a click, routed
        // through relative, would be amplified into a visible jump. We hold the
        // cursor still until travel passes a few px of slop: below that it's a
        // click (no motion rendered, cursor frozen), above it a real drag.
        // Either way a press/release with no rendered motion gets a 1px nudge
        // (+1 engaging, -1 releasing, netting zero) so macOS registers the click.
        static const int64_t DRAG_SLOP_PX = 4;
        static int64_t drag_frac_x = 0;
        static int64_t drag_frac_y = 0;
        static int64_t drag_travel = 0;
        static int64_t drag_pending_x = 0;  // internal units accumulated awaiting a flush
        static int64_t drag_pending_y = 0;
        static int32_t drag_pending_wheel = 0;  // report units (ticks) awaiting a flush
        static int32_t drag_pending_pan = 0;
        static bool drag_active = false;

        if (active_screen != -1 && drag_scale > 0) {
            drag_travel += (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
            if (drag_travel > drag_scale * DRAG_SLOP_PX) {
                drag_active = true;
            }
            if (drag_active) {
                drag_pending_x += dx;
                drag_pending_y += dy;
            }
            // Divert scroll into the relative report for as long as a button is
            // held, not just once the drag crosses the slop threshold - the
            // absolute report (which normally carries Wheel/AC Pan) is suppressed
            // below the moment a button goes down, so a hold-and-scroll with no
            // motion would otherwise drop the scroll. Take the whole-tick part
            // each frame and leave the sub-tick remainder in accumulated[] (the
            // generic report loop carries it, writing 0 to the suppressed
            // absolute report). accumulated is in *1000 units.
            {
                int32_t w = accumulated[V_SCROLL_USAGE] / 1000;
                accumulated[V_SCROLL_USAGE] -= w * 1000;
                drag_pending_wheel += w;
                int32_t p = accumulated[H_SCROLL_USAGE] / 1000;
                accumulated[H_SCROLL_USAGE] -= p * 1000;
                drag_pending_pan += p;
            }

            // Flush accumulated motion to a relative report only on the
            // USB-paced tick or on a button edge. process_mapping also runs per
            // incoming mouse report, so a high-rate mouse outpaces the ~1ms USB
            // drain; emitting per report would queue motion faster than it's
            // sent and the backlog would keep moving the cursor after release.
            bool flush = auto_repeat || buttons_changed;
            int64_t rx = 0;
            int64_t ry = 0;
            int32_t flush_wheel = 0;
            int32_t flush_pan = 0;
            if (flush && drag_active) {
                int64_t numx = drag_pending_x + drag_frac_x;
                int64_t numy = drag_pending_y + drag_frac_y;
                rx = numx / drag_scale;
                ry = numy / drag_scale;
                drag_frac_x = numx - rx * drag_scale;
                drag_frac_y = numy - ry * drag_scale;
                drag_pending_x = 0;
                drag_pending_y = 0;
                if (rx < -32768) rx = -32768; else if (rx > 32767) rx = 32767;
                if (ry < -32768) ry = -32768; else if (ry > 32767) ry = 32767;
            }
            // Scroll flushes on the tick whenever a button is held - independent
            // of drag_active, so hold-and-scroll works before the drag registers.
            if (flush) {
                flush_wheel = drag_pending_wheel;
                flush_pan = drag_pending_pan;
                drag_pending_wheel = 0;
                drag_pending_pan = 0;
                if (flush_wheel < -32768) flush_wheel = -32768; else if (flush_wheel > 32767) flush_wheel = 32767;
                if (flush_pan < -32768) flush_pan = -32768; else if (flush_pan > 32767) flush_pan = 32767;
            }
            // Stationary transition: nudge 1px so the click carries motion. +1
            // as buttons engage, -1 as they release, so a press/release cancels.
            if (buttons_changed && rx == 0 && ry == 0) {
                rx = (__builtin_popcount(held_buttons) >= __builtin_popcount(prev_frame_buttons)) ? 1 : -1;
            }
            if (rx != 0 || ry != 0 || flush_wheel != 0 || flush_pan != 0 || buttons_changed) {
                queue_relative_movement((int16_t) rx, (int16_t) ry, (int16_t) flush_wheel, (int16_t) flush_pan, active_screen);
            }

            // Advance the internal cursor by the motion the host actually
            // rendered - the emitted relative counts scaled by the host's
            // speed-dependent gain this flush - so the absolute report sent
            // after release lands where the host already is, not ahead of it.
            int64_t flush_gain = drag_advance_gain(rx, ry, drag_base, drag_slope);
            cursor_x += rx * drag_scale * flush_gain / 1000;
            cursor_y += ry * drag_scale * flush_gain / 1000;

            // Follow the cursor across same-output screens so a window dragged
            // between local monitors keeps an accurate post-release position;
            // never switch computers mid-drag.
            int8_t drag_screen;
            if (within_bounds(cursor_x, cursor_y, drag_screen) && drag_screen != -1 &&
                screens.count(drag_screen) && screens.count(prev_active_screen) &&
                screens[drag_screen].output == screens[prev_active_screen].output) {
                active_screen = drag_screen;
            } else {
                int64_t min_x = screens[active_screen].x;
                int64_t max_x = screens[active_screen].x + screens[active_screen].w - 1;
                int64_t min_y = screens[active_screen].y;
                int64_t max_y = screens[active_screen].y + screens[active_screen].h - 1;
                if (cursor_x < min_x) cursor_x = min_x; else if (cursor_x > max_x) cursor_x = max_x;
                if (cursor_y < min_y) cursor_y = min_y; else if (cursor_y > max_y) cursor_y = max_y;
            }
            suppress_abs_mouse = true;
        }

        edge_push = 0;  // not crossing to another computer mid-drag

        if (held_buttons == 0) {
            if (drag_active) {
                // Real drag just ended: drop motion queued but not yet sent so
                // it can't drain after the button-up and shoot the cursor on.
                drop_unsent_relative_motion();
                if (active_screen != -1 && screens.count(active_screen)) {
                    int64_t min_x = screens[active_screen].x;
                    int64_t max_x = screens[active_screen].x + screens[active_screen].w - 1;
                    int64_t min_y = screens[active_screen].y;
                    int64_t max_y = screens[active_screen].y + screens[active_screen].h - 1;
                    if (cursor_x < min_x) cursor_x = min_x; else if (cursor_x > max_x) cursor_x = max_x;
                    if (cursor_y < min_y) cursor_y = min_y; else if (cursor_y > max_y) cursor_y = max_y;
                }
            }
            if (drag_active) {
                // Don't reposition the cursor while it's stationary right after
                // the drag; wait until the next movement so the correction is
                // hidden in the motion instead of snapping on release.
                defer_abs_after_drag = true;
            }
            drag_active = false;
            drag_travel = 0;
            drag_frac_x = 0;
            drag_frac_y = 0;
            drag_pending_x = 0;
            drag_pending_y = 0;
            drag_pending_wheel = 0;
            drag_pending_pan = 0;
        }
    }

    prev_frame_buttons = held_buttons;

    // Hold off the absolute reposition after a drag until the cursor actually
    // moves again (see defer_abs_after_drag above). A real movement this frame
    // clears the hold and lets the correction ride along with that motion.
    if (defer_abs_after_drag) {
        if (dx == 0 && dy == 0) {
            suppress_abs_mouse = true;
        } else {
            defer_abs_after_drag = false;
        }
    }

    if (active_screen != -1 && !suppress_abs_mouse) {
        int64_t local_x = (cursor_x - screens[active_screen].x) * 32768 / screens[active_screen].w;
        int64_t local_y = (cursor_y - screens[active_screen].y) * 32768 / screens[active_screen].h;
        // Clamp to the 16-bit absolute range. Without this, a cursor position
        // past the screen edge writes a value > 32767 that truncates in the
        // report field and wraps the pointer to the opposite edge.
        if (local_x < 0) local_x = 0; else if (local_x > 32767) local_x = 32767;
        if (local_y < 0) local_y = 0; else if (local_y > 32767) local_y = 32767;

        {
            usage_def_t& our_usage = our_usages_flat[MOUSE_X_USAGE];
            put_bits((uint8_t*) reports[our_usage.report_id], report_sizes[our_usage.report_id], our_usage.bitpos, our_usage.size, local_x);
        }
        {
            usage_def_t& our_usage = our_usages_flat[MOUSE_Y_USAGE];
            put_bits((uint8_t*) reports[our_usage.report_id], report_sizes[our_usage.report_id], our_usage.bitpos, our_usage.size, local_y);
        }
    }

    for (auto& [usage, accumulated_val] : accumulated) {
        if (accumulated_val == 0) {
            continue;
        }
        usage_def_t& our_usage = our_usages_flat[usage];
        int32_t existing_val = get_bits((uint8_t*) reports[our_usage.report_id], report_sizes[our_usage.report_id], our_usage.bitpos, our_usage.size);
        if (our_usage.logical_minimum < 0) {
            if (existing_val & (1 << (our_usage.size - 1))) {
                existing_val |= 0xFFFFFFFF << our_usage.size;
            }
        }
        int32_t truncated = accumulated_val / 1000;
        accumulated_val -= truncated * 1000;
        if (truncated != 0) {
            put_bits((uint8_t*) reports[our_usage.report_id], report_sizes[our_usage.report_id], our_usage.bitpos, our_usage.size, existing_val + truncated);
        }
    }

    // Crossing to the other computer: release whatever is still held on the
    // machine we just left. Outgoing reports route by their tagged screen's
    // output, so a held key's press already went to the old machine while its
    // release would now route to the new one - stranding the key "down" on the
    // old machine. A stranded modifier is the worst case: macOS reads
    // Control+click as a right-click, so every left-click on that machine comes
    // out as a secondary click until some later keyboard report happens to route
    // back (the "press a modifier to clear it" workaround). Release on the old
    // output and zero prev_reports so the queue loop below re-asserts any
    // still-held state to the new machine - a key held through the crossing then
    // follows the cursor instead of being lost.
    static int8_t output_anchor_screen = -1;
    if (output_anchor_screen != -1 && active_screen != -1 &&
        screens.count(output_anchor_screen) && screens.count(active_screen) &&
        screens[output_anchor_screen].output != screens[active_screen].output) {
        int8_t old_screen = output_anchor_screen;

        bool kbd_held = false;
        for (int i = 0; i < report_sizes[REPORT_ID_KEYBOARD]; i++) {
            if (reports[REPORT_ID_KEYBOARD][i]) { kbd_held = true; break; }
        }
        bool consumer_held = false;
        for (int i = 0; i < report_sizes[REPORT_ID_CONSUMER]; i++) {
            if (reports[REPORT_ID_CONSUMER][i]) { consumer_held = true; break; }
        }
        // Mouse buttons can only strand via an explicit screen-switch hotkey
        // pressed mid-click (the edge-crossing path won't switch computers while
        // a button is held). Release on the old machine; don't re-assert, since
        // dragging across computers isn't supported.
        bool button_held = reports[REPORT_ID_MOUSE][0] != 0;

        if (kbd_held) {
            queue_zero_report(REPORT_ID_KEYBOARD, old_screen);
            memset(prev_reports[REPORT_ID_KEYBOARD], 0, report_sizes[REPORT_ID_KEYBOARD]);
        }
        if (consumer_held) {
            queue_zero_report(REPORT_ID_CONSUMER, old_screen);
            memset(prev_reports[REPORT_ID_CONSUMER], 0, report_sizes[REPORT_ID_CONSUMER]);
        }
        if (button_held) {
            queue_zero_report(REPORT_ID_RELATIVE, old_screen);
        }
    }
    if (active_screen != -1) {
        output_anchor_screen = active_screen;
    }

    for (uint i = 0; i < report_ids.size(); i++) {  // XXX what order should we go in? maybe keyboard first so that mappings to ctrl-left click work as expected?
        uint8_t report_id = report_ids[i];
        if ((active_screen != -1) && needs_to_be_sent(report_id) &&
            !(suppress_abs_mouse && report_id == REPORT_ID_MOUSE)) {
            if (or_items == OR_BUFSIZE) {
                printf("overflow!\n");
                break;
            }
            uint8_t prev = (or_tail + OR_BUFSIZE - 1) % OR_BUFSIZE;
            if ((or_items > 0) &&
                (outgoing_reports[prev][0] == active_screen) &&
                (outgoing_reports[prev][1] == report_id) &&
                !differ_on_absolute(outgoing_reports[prev] + 2, reports[report_id], report_id)) {
                aggregate_relative(outgoing_reports[prev] + 2, reports[report_id], report_id);
            } else {
                outgoing_reports[or_tail][0] = active_screen;
                outgoing_reports[or_tail][1] = report_id;
                memcpy(outgoing_reports[or_tail] + 2, reports[report_id], report_sizes[report_id]);
                memcpy(prev_reports[report_id], reports[report_id], report_sizes[report_id]);
                or_tail = (or_tail + 1) % OR_BUFSIZE;
                or_items++;
            }
        }
        memset(reports[report_id], 0, report_sizes[report_id]);
    }
}

void send_report() {
    if (suspended || (or_items == 0)) {
        return;
    }

    uint8_t screen_idx = outgoing_reports[or_head][0];
    uint8_t report_id = outgoing_reports[or_head][1];

    uint8_t output = (screens.count(screen_idx) && screens[screen_idx].output != 0) ? 1 : 0;

    if (output == 0) {
        tud_hid_report(report_id, outgoing_reports[or_head] + 2, report_sizes[report_id]);
    } else {
        serial_write(outgoing_reports[or_head] + 1, report_sizes[report_id] + 1, FORWARDER_UART);
    }

    or_head = (or_head + 1) % OR_BUFSIZE;
    or_items--;

    reports_sent++;
}

inline void read_input(const uint8_t* report, int len, uint32_t source_usage, const usage_def_t& their_usage, uint16_t interface) {
    int32_t value = 0;
    if (their_usage.is_array) {
        for (uint i = 0; i < their_usage.count; i++) {
            if (get_bits(report, len, their_usage.bitpos + i * their_usage.size, their_usage.size) == their_usage.index) {
                value = 1;
                break;
            }
        }
    } else {
        value = get_bits(report, len, their_usage.bitpos, their_usage.size);
        if (their_usage.logical_minimum < 0) {
            if (value & (1 << (their_usage.size - 1))) {
                value |= 0xFFFFFFFF << their_usage.size;
            }
        }
    }

    if (their_usage.is_relative) {
        input_state[source_usage] = value;
    } else {
        if (value) {
            input_state[source_usage] |= 1 << interface_index[interface];
        } else {
            input_state[source_usage] &= ~(1 << interface_index[interface]);
        }
    }
}

void handle_received_report(const uint8_t* report, int len, uint16_t interface) {
    // Suppress the activity flicker while a deliberate blink sequence is
    // running so the user can count the pulses without interference.
    if (blink_transitions_remaining == 0) {
        led_state = !led_state;
        board_led_write(led_state);
    }
    reports_received++;
    {
        uint8_t in_output = (screens.count(active_screen) && screens[active_screen].output != 0) ? 1 : 0;
        last_input_per_output_us[in_output] = time_us_64();
    }

    mutex_enter_blocking(&their_usages_mutex);

    uint8_t report_id = 0;
    if (has_report_id_theirs[interface]) {
        report_id = report[0];
        report++;
        len--;
    }

    for (auto const& [their_usage, their_usage_def] : their_usages[interface][report_id]) {
        read_input(report, len, their_usage, their_usage_def, interface);
    }

    mutex_exit(&their_usages_mutex);
}

void rlencode(const std::set<uint32_t>& usages, std::vector<usage_rle_t>& output) {
    uint32_t start_usage = 0;
    uint32_t count = 0;
    for (auto const& usage : usages) {
        if (start_usage == 0) {
            start_usage = usage;
            count = 1;
            continue;
        }
        if (usage == start_usage + count) {
            count++;
        } else {
            output.push_back({ .usage = start_usage, .count = count });
            start_usage = usage;
            count = 1;
        }
    }
    if (start_usage != 0) {
        output.push_back({ .usage = start_usage, .count = count });
    }
}

void update_their_descriptor_derivates() {
    relative_usages.clear();
    relative_usage_set.clear();
    std::set<uint32_t> their_usages_set;
    for (auto const& [interface, report_id_usage_map] : their_usages) {
        for (auto const& [report_id, usage_map] : report_id_usage_map) {
            for (auto const& [usage, usage_def] : usage_map) {
                their_usages_set.insert(usage);
                if (usage_def.is_relative) {
                    relative_usages.push_back(usage);
                    relative_usage_set.insert(usage);
                }
            }
        }
    }

    their_usages_rle.clear();
    rlencode(their_usages_set, their_usages_rle);
}

void parse_our_descriptor() {
    bool has_report_id_ours;
    std::unordered_map<uint8_t, uint16_t> report_sizes_map = parse_descriptor(our_usages, has_report_id_ours, our_report_descriptor, our_report_descriptor_length);
    for (auto const& [report_id, size] : report_sizes_map) {
        report_sizes[report_id] = size;
        reports[report_id] = new uint8_t[size];
        memset(reports[report_id], 0, size);
        prev_reports[report_id] = new uint8_t[size];
        memset(prev_reports[report_id], 0, size);
        report_masks_relative[report_id] = new uint8_t[size];
        memset(report_masks_relative[report_id], 0, size);
        report_masks_absolute[report_id] = new uint8_t[size];
        memset(report_masks_absolute[report_id], 0, size);

        report_ids.push_back(report_id);
    }

    std::set<uint32_t> our_usages_set;
    for (auto const& [report_id, usage_map] : our_usages) {
        for (auto const& [usage, usage_def] : usage_map) {
            // Don't let the relative mouse report's X/Y overwrite the absolute
            // mouse's entries in the flat map. The relative report is only used
            // for boundary crossing and is written to directly.
            if (report_id != REPORT_ID_RELATIVE) {
                our_usages_flat[usage] = usage_def;
            }
            our_usages_set.insert(usage);

            if (usage_def.is_relative) {
                put_bits(report_masks_relative[report_id], report_sizes[report_id], usage_def.bitpos, usage_def.size, 0xFFFFFFFF);
            } else {
                put_bits(report_masks_absolute[report_id], report_sizes[report_id], usage_def.bitpos, usage_def.size, 0xFFFFFFFF);
            }
        }
    }

    rlencode(our_usages_set, our_usages_rle);
}

void print_stats() {
    uint64_t now = time_us_64();
    if (now > next_print) {
        printf("%ld %ld\n", reports_received, reports_sent);
        reports_received = 0;
        reports_sent = 0;
        while (next_print < now) {
            next_print += 1000000;
        }
    }
}

inline bool get_and_clear_tick_pending() {
    // atomicity not critical
    uint8_t tmp = tick_pending;
    tick_pending = false;
    return tmp;
}

void sof_handler(uint32_t frame_count) {
    tick_pending = true;
}

// Keep-awake jiggle, randomised so it doesn't carry the fixed-period,
// fixed-magnitude, pure-diagonal signature that betrays a hardware jiggler to
// idle-detection tooling. Magnitude, direction, and the gap between repeats are
// all randomised within bounds (see maybe_jiggle).
static const int16_t JIGGLE_MIN_PX = 6;     // smallest single-axis step
static const int16_t JIGGLE_MAX_PX = 16;    // largest single-axis step
static const int16_t JIGGLE_BOUND_PX = 24;  // soft box: pull back once the running offset reaches this
static const uint32_t JIGGLE_GAP_FLOOR_PCT = 85;  // a repeat may fire at most this much earlier than the interval
uint64_t last_jiggle_us = 0;
uint64_t next_jiggle_gap_us = 0;            // randomised gap until the next repeat (0 = use full interval)
static int16_t jiggle_accum_x = 0;          // net displacement since rest, kept near zero by the random walk
static int16_t jiggle_accum_y = 0;

// Tiny xorshift32 PRNG, seeded lazily off the running clock. It only needs to
// break the periodic/identical pattern of the jiggle, not resist analysis, so a
// non-cryptographic generator is enough.
static uint32_t jiggle_rng_state = 0;
static uint32_t jiggle_rand() {
    if (jiggle_rng_state == 0) {
        jiggle_rng_state = (uint32_t) time_us_64() | 1u;  // xorshift must never be seeded with 0
    }
    uint32_t x = jiggle_rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    jiggle_rng_state = x;
    return x;
}

// Uniform integer in [lo, hi].
static int32_t jiggle_rand_range(int32_t lo, int32_t hi) {
    return lo + (int32_t) (jiggle_rand() % (uint32_t) (hi - lo + 1));
}

// One axis of a jiggle step: random magnitude, random sign while the running
// offset is inside the box, forced back toward centre once it reaches the edge.
// The cursor random-walks within roughly +/- a few tens of px of where it
// started, so it never drifts across the screen over a long idle.
static int16_t jiggle_axis_step(int16_t accum) {
    int16_t mag = (int16_t) jiggle_rand_range(JIGGLE_MIN_PX, JIGGLE_MAX_PX);
    bool negative;
    if (accum >= JIGGLE_BOUND_PX) {
        negative = true;
    } else if (accum <= -JIGGLE_BOUND_PX) {
        negative = false;
    } else {
        negative = jiggle_rand() & 1u;
    }
    return negative ? (int16_t) -mag : mag;
}

// Nudge whichever machine the user is *not* driving so it doesn't sleep or
// lock. The target output must have had no real input for jiggle_interval
// seconds before the first nudge; subsequent nudges fire on a randomised gap in
// [interval*0.85, interval] so the repeat train doesn't read as a metronome yet
// never waits longer than the configured interval. Tracking idleness per-output
// means active use of the local machine doesn't suppress jiggles to the remote
// (and vice versa). Does nothing if jiggling is off, we're suspended, or there's
// no screen configured on the inactive output.
void maybe_jiggle() {
    if (jiggle_interval == 0 || suspended || !jiggle_enabled) {
        return;
    }

    uint64_t now = time_us_64();
    uint64_t idle_us = (uint64_t) jiggle_interval * 1000000;
    uint8_t active_output = (screens.count(active_screen) && screens[active_screen].output != 0) ? 1 : 0;
    uint8_t target_output = active_output == 0 ? 1 : 0;
    // Never resurrect an already-sleeping local host. If the jiggle targets the
    // USB-attached machine (output 0) and its bus is suspended, skip it -
    // delivering it would force a tud_remote_wakeup() and defeat the intent to
    // let that machine sleep. Only genuine user input should wake it. The remote
    // (output 1) is reached over serial and woken (or not) by the forwarder.
    if (target_output == 0 && tud_suspended()) {
        return;
    }
    // Gate the first nudge on a full idle interval since the last real input on
    // the target, so an in-use machine never gets jiggled.
    if (now - last_input_per_output_us[target_output] < idle_us) {
        return;
    }
    if (next_jiggle_gap_us == 0) {
        next_jiggle_gap_us = idle_us;  // first nudge lands at the configured interval
    }
    if (now - last_jiggle_us < next_jiggle_gap_us) {
        return;
    }
    last_jiggle_us = now;
    // Pick the gap to the next repeat: between 85% and 100% of the interval, so a
    // repeat fires at most 15% early and never later than configured.
    uint64_t floor_us = idle_us * JIGGLE_GAP_FLOOR_PCT / 100;
    next_jiggle_gap_us = floor_us + (jiggle_rand() % (idle_us - floor_us + 1));

    for (int i = 0; i < NSCREENS; i++) {
        if (screens.count(i) && screens[i].w > 0 && screens[i].output == target_output) {
            // Random small vector with independent X/Y (not a fixed diagonal),
            // accumulated so each step can be biased back toward the start. One
            // move per tick avoids queue_relative_movement coalescing it away.
            int16_t dx = jiggle_axis_step(jiggle_accum_x);
            int16_t dy = jiggle_axis_step(jiggle_accum_y);
            jiggle_accum_x += dx;
            jiggle_accum_y += dy;
            queue_relative_movement(dx, dy, 0, 0, i);
            break;
        }
    }
}

// === Forwarder typing / jiggler-activation sweep =============================
// Two cooperating state machines that emit USB HID reports to the *forwarder*
// (the secondary machine) outside the normal input pipeline:
//   - service_type_text(): replays an ASCII text buffer as keyboard reports.
//   - service_jiggle_sweep(): a one-shot cursor wiggle when the user toggles
//     the jiggler ON, so the toggle landing is visible on the remote machine.
// Both reuse the existing outgoing_reports ring buffer and rely on
// send_report() to dispatch by screen output, so a report whose screen_idx
// points at an output==1 (serial) screen lands on the forwarder UART.

// Find any configured screen whose output is the forwarder. Returns -1 if
// the current profile has no forwarder-side screen, in which case neither
// state machine has anywhere to send and we abort gracefully.
static int8_t find_forwarder_screen() {
    for (uint8_t i = 0; i < NSCREENS; i++) {
        if (screens.count(i) && screens[i].w > 0 && screens[i].output == 1) {
            return i;
        }
    }
    return -1;
}

// First configured local screen (output 0, the main computer this device is
// plugged into). Returns -1 if the current profile has no local screen. Used as
// the gesture target for confirmations shown to the user; relative motion only
// needs the screen to select the output, not a specific position.
static int8_t find_local_screen() {
    for (uint8_t i = 0; i < NSCREENS; i++) {
        if (screens.count(i) && screens[i].w > 0 && screens[i].output == 0) {
            return i;
        }
    }
    return -1;
}

// ---- Text typing -----------------------------------------------------------
// Capped at 4 KB so a runaway paste can't exhaust RAM or trap the user in a
// minutes-long replay loop; at ~71 chars/sec that's still ~57 seconds of typing.
// The host CLI mirrors this limit and truncates with a warning before sending.
static const uint16_t TYPE_BUFFER_SIZE = 4096;
static uint8_t type_buffer[TYPE_BUFFER_SIZE];
static uint16_t type_buffer_len = 0;
static uint16_t type_cursor = 0;
static bool type_active = false;
static uint8_t type_phase = 0;  // 0 = ready to press, 1 = waiting to release
static uint64_t type_next_us = 0;

// 7 ms press, 7 ms release: 14 ms per char => ~71 cps. Reliable across macOS,
// Linux and Windows at 1 ms USB polling, and fast enough that a URL or short
// snippet pastes in well under a second.
static const uint32_t TYPE_PRESS_INTERVAL_US = 7000;
static const uint32_t TYPE_RELEASE_INTERVAL_US = 7000;

// HID modifier byte bits (US keyboard usage page 0x07, usages 0xE0..0xE7).
static const uint8_t HID_MOD_LSHIFT = 0x02;

// Map a printable ASCII byte to a US-layout (keycode, modifier) pair. Returns
// false for bytes we can't represent (non-ASCII, most control chars); the
// caller skips those without delay. The destination machine's keyboard layout
// must be US for the punctuation row to land on the expected character - on
// non-US layouts the letters still type correctly but the symbols won't.
static bool ascii_to_hid(uint8_t c, uint8_t* keycode, uint8_t* modifier) {
    *modifier = 0;
    if (c >= 'a' && c <= 'z') { *keycode = 0x04 + (c - 'a'); return true; }
    if (c >= 'A' && c <= 'Z') { *keycode = 0x04 + (c - 'A'); *modifier = HID_MOD_LSHIFT; return true; }
    if (c >= '1' && c <= '9') { *keycode = 0x1e + (c - '1'); return true; }
    if (c == '0') { *keycode = 0x27; return true; }
    switch (c) {
        case ' ':  *keycode = 0x2c; return true;
        case '\t': *keycode = 0x2b; return true;
        case '\n': *keycode = 0x28; return true;  // Enter / Return
        case '-':  *keycode = 0x2d; return true;
        case '=':  *keycode = 0x2e; return true;
        case '[':  *keycode = 0x2f; return true;
        case ']':  *keycode = 0x30; return true;
        case '\\': *keycode = 0x31; return true;
        case ';':  *keycode = 0x33; return true;
        case '\'': *keycode = 0x34; return true;
        case '`':  *keycode = 0x35; return true;
        case ',':  *keycode = 0x36; return true;
        case '.':  *keycode = 0x37; return true;
        case '/':  *keycode = 0x38; return true;
        case '!':  *keycode = 0x1e; *modifier = HID_MOD_LSHIFT; return true;
        case '@':  *keycode = 0x1f; *modifier = HID_MOD_LSHIFT; return true;
        case '#':  *keycode = 0x20; *modifier = HID_MOD_LSHIFT; return true;
        case '$':  *keycode = 0x21; *modifier = HID_MOD_LSHIFT; return true;
        case '%':  *keycode = 0x22; *modifier = HID_MOD_LSHIFT; return true;
        case '^':  *keycode = 0x23; *modifier = HID_MOD_LSHIFT; return true;
        case '&':  *keycode = 0x24; *modifier = HID_MOD_LSHIFT; return true;
        case '*':  *keycode = 0x25; *modifier = HID_MOD_LSHIFT; return true;
        case '(':  *keycode = 0x26; *modifier = HID_MOD_LSHIFT; return true;
        case ')':  *keycode = 0x27; *modifier = HID_MOD_LSHIFT; return true;
        case '_':  *keycode = 0x2d; *modifier = HID_MOD_LSHIFT; return true;
        case '+':  *keycode = 0x2e; *modifier = HID_MOD_LSHIFT; return true;
        case '{':  *keycode = 0x2f; *modifier = HID_MOD_LSHIFT; return true;
        case '}':  *keycode = 0x30; *modifier = HID_MOD_LSHIFT; return true;
        case '|':  *keycode = 0x31; *modifier = HID_MOD_LSHIFT; return true;
        case ':':  *keycode = 0x33; *modifier = HID_MOD_LSHIFT; return true;
        case '"':  *keycode = 0x34; *modifier = HID_MOD_LSHIFT; return true;
        case '~':  *keycode = 0x35; *modifier = HID_MOD_LSHIFT; return true;
        case '<':  *keycode = 0x36; *modifier = HID_MOD_LSHIFT; return true;
        case '>':  *keycode = 0x37; *modifier = HID_MOD_LSHIFT; return true;
        case '?':  *keycode = 0x38; *modifier = HID_MOD_LSHIFT; return true;
    }
    return false;
}

// Queue a single keyboard report (modifier byte + bitmap of pressed keycodes)
// into outgoing_reports targeted at a forwarder-output screen. Body layout
// matches our_descriptor.cc's keyboard collection: byte 0 = 8 modifier bits,
// bytes 1..14 = one bit per keycode 0x04..0x73 (low bit of byte 1 = 0x04).
static void queue_forwarder_keyboard_report(uint8_t modifier, uint8_t keycode, uint8_t screen_idx) {
    if (or_items >= OR_BUFSIZE) return;
    outgoing_reports[or_tail][0] = screen_idx;
    outgoing_reports[or_tail][1] = REPORT_ID_KEYBOARD;
    memset(outgoing_reports[or_tail] + 2, 0, report_sizes[REPORT_ID_KEYBOARD]);
    outgoing_reports[or_tail][2] = modifier;
    if (keycode >= 0x04 && keycode <= 0x73) {
        uint8_t bit_index = keycode - 0x04;
        outgoing_reports[or_tail][3 + bit_index / 8] |= (1 << (bit_index % 8));
    }
    or_tail = (or_tail + 1) % OR_BUFSIZE;
    or_items++;
}

// Called from config.cc on each TYPE_TEXT feature report. Appends data into
// the buffer; the RESET flag clears any in-flight session first, and the GO
// flag arms the state machine to start replaying on the next service tick.
// Truncates silently rather than rejecting once the buffer cap is hit - the
// host CLI is responsible for sizing the input.
void handle_type_text_chunk(const uint8_t* data, uint8_t len, bool reset, bool go) {
    if (reset) {
        type_active = false;
        type_buffer_len = 0;
        type_cursor = 0;
        type_phase = 0;
    }
    if (len > 0 && type_buffer_len < TYPE_BUFFER_SIZE) {
        uint16_t room = TYPE_BUFFER_SIZE - type_buffer_len;
        uint16_t to_copy = (len < room) ? len : room;
        memcpy(type_buffer + type_buffer_len, data, to_copy);
        type_buffer_len += to_copy;
    }
    if (go && type_buffer_len > 0 && !type_active) {
        type_cursor = 0;
        type_phase = 0;
        type_active = true;
        type_next_us = time_us_64();
    }
}

// True while the host keyboard is holding Ctrl-C or Cmd-C (either side). Used as
// an escape hatch to abort an in-flight paste - e.g. it's landing in the wrong
// window or running long. Keys are source usages on page 0x07: 0x06 = 'C',
// 0xE0/0xE1 = L/R Control, 0xE3/0xE4 = L/R GUI (Cmd on macOS).
static bool type_text_abort_combo_held() {
    if (!input_state[0x00070006]) return false;  // 'C' not down
    return input_state[0x000700e0] || input_state[0x000700e1] ||  // Control
           input_state[0x000700e3] || input_state[0x000700e4];    // GUI / Cmd
}

static void service_type_text() {
    if (!type_active || suspended) return;

    // Checked every loop iteration (before the inter-keystroke wait) so a cancel
    // lands within a poll, not a keystroke interval.
    if (type_text_abort_combo_held()) {
        // If we're mid-keystroke (press sent, release pending) flush a release so
        // the second machine isn't left with a stuck key, then discard the rest.
        if (type_phase == 1) {
            int8_t fwd = find_forwarder_screen();
            if (fwd >= 0) {
                queue_forwarder_keyboard_report(0, 0, (uint8_t) fwd);
            }
        }
        type_active = false;
        type_buffer_len = 0;
        type_cursor = 0;
        type_phase = 0;
        return;
    }

    uint64_t now = time_us_64();
    if (now < type_next_us) return;

    int8_t fwd_screen = find_forwarder_screen();
    if (fwd_screen < 0) {
        // Current profile has no forwarder screen - abort cleanly.
        type_active = false;
        return;
    }
    // Don't pile reports on a full ring; just wait one tick.
    if (or_items >= OR_BUFSIZE) return;

    if (type_phase == 0) {
        if (type_cursor >= type_buffer_len) {
            type_active = false;
            return;
        }
        uint8_t c = type_buffer[type_cursor];
        uint8_t keycode = 0, modifier = 0;
        if (ascii_to_hid(c, &keycode, &modifier)) {
            queue_forwarder_keyboard_report(modifier, keycode, (uint8_t) fwd_screen);
            type_phase = 1;
            type_next_us = now + TYPE_PRESS_INTERVAL_US;
        } else {
            // Unrepresentable byte: advance without a press/release cycle. Same
            // tick keeps the loop progressing so we don't spend 14 ms per
            // skipped non-ASCII byte.
            type_cursor++;
        }
    } else {
        // Release: empty keyboard report clears modifier + all keycodes.
        queue_forwarder_keyboard_report(0, 0, (uint8_t) fwd_screen);
        type_cursor++;
        type_phase = 0;
        type_next_us = now + TYPE_RELEASE_INTERVAL_US;
    }
}

// ---- Cursor gesture player -------------------------------------------------
// A small generic player that traces a polyline of straight strokes as relative
// cursor movement on a chosen screen, one ~10 px step every ~10 ms (the cadence
// the original jiggler sweep used). Two independent players run concurrently:
//   [GESTURE_LOCAL]     -> a local screen (output 0, the main computer)
//   [GESTURE_FORWARDER] -> the forwarder screen (output 1, the second computer)
// so the jiggler-on confirmation can play on both machines at once, and the
// profile-number draw plays on the main computer. Relative bursts route to the
// right host via send_report()'s output check and never touch cursor_x/y, so the
// absolute report (which only re-sends on change) doesn't fight them.

struct gesture_stroke_t {
    int16_t dx;  // total x of this stroke, px (+ = right)
    int16_t dy;  // total y of this stroke, px (+ = down)
};

#define GESTURE_LEN(a) ((uint8_t) (sizeof(a) / sizeof((a)[0])))

static const int16_t  GESTURE_STEP_PX = 10;
static const uint32_t GESTURE_STEP_INTERVAL_US = 10000;

struct gesture_player_t {
    const gesture_stroke_t* strokes;  // nullptr = idle
    uint8_t  nstrokes;
    uint8_t  stroke_idx;
    uint16_t step;          // steps emitted in the current stroke
    uint16_t total_steps;   // steps the current stroke is split into
    int32_t  emitted_x;     // px already emitted in the current stroke
    int32_t  emitted_y;
    int8_t   screen;        // target screen (selects output)
    uint64_t next_us;
};

enum { GESTURE_LOCAL = 0, GESTURE_FORWARDER = 1, NGESTURE_PLAYERS = 2 };
static gesture_player_t gesture_players[NGESTURE_PLAYERS];

// Number of ~GESTURE_STEP_PX steps to render a stroke, at least one.
static uint16_t gesture_stroke_steps(const gesture_stroke_t& s) {
    int32_t ax = s.dx < 0 ? -s.dx : s.dx;
    int32_t ay = s.dy < 0 ? -s.dy : s.dy;
    int32_t span = ax > ay ? ax : ay;
    uint16_t steps = (uint16_t) (span / GESTURE_STEP_PX);
    return steps == 0 ? 1 : steps;
}

static void gesture_start(uint8_t idx, const gesture_stroke_t* strokes, uint8_t nstrokes, int8_t screen) {
    if (idx >= NGESTURE_PLAYERS || screen < 0 || strokes == nullptr || nstrokes == 0) {
        return;
    }
    gesture_player_t& p = gesture_players[idx];
    p.strokes = strokes;
    p.nstrokes = nstrokes;
    p.stroke_idx = 0;
    p.step = 0;
    p.total_steps = gesture_stroke_steps(strokes[0]);
    p.emitted_x = 0;
    p.emitted_y = 0;
    p.screen = screen;
    p.next_us = time_us_64();
}

static void service_gesture_player(gesture_player_t& p) {
    if (p.strokes == nullptr || suspended) return;
    // Never draw on (and so never wake) a sleeping local host: a report bound for
    // a suspended output-0 screen would force a remote wakeup in the main loop.
    // The forwarder (output 1) is reached over serial and is unaffected.
    uint8_t out = (screens.count(p.screen) && screens[p.screen].output != 0) ? 1 : 0;
    if (out == 0 && tud_suspended()) {
        p.strokes = nullptr;
        return;
    }
    uint64_t now = time_us_64();
    if (now < p.next_us) return;
    if (or_items >= OR_BUFSIZE) return;  // let the ring drain; keep the shape intact

    const gesture_stroke_t& s = p.strokes[p.stroke_idx];
    p.step++;
    // Walk toward the stroke endpoint by integer interpolation so rounding never
    // accumulates - the final step lands exactly on (dx, dy).
    int32_t target_x = (int32_t) s.dx * p.step / p.total_steps;
    int32_t target_y = (int32_t) s.dy * p.step / p.total_steps;
    int16_t ddx = (int16_t) (target_x - p.emitted_x);
    int16_t ddy = (int16_t) (target_y - p.emitted_y);
    p.emitted_x = target_x;
    p.emitted_y = target_y;
    if (ddx != 0 || ddy != 0) {
        queue_relative_movement(ddx, ddy, 0, 0, (uint8_t) p.screen);
    }
    p.next_us = now + GESTURE_STEP_INTERVAL_US;

    if (p.step >= p.total_steps) {
        p.stroke_idx++;
        if (p.stroke_idx >= p.nstrokes) {
            p.strokes = nullptr;  // done
            return;
        }
        p.step = 0;
        p.emitted_x = 0;
        p.emitted_y = 0;
        p.total_steps = gesture_stroke_steps(p.strokes[p.stroke_idx]);
    }
}

static void service_gestures() {
    for (uint8_t i = 0; i < NGESTURE_PLAYERS; i++) {
        service_gesture_player(gesture_players[i]);
    }
}

// Abort the local-machine gesture so it never fights the user. Called from
// process_mapping on real cursor movement (keyboard hotkeys - the gesture
// triggers - don't move the cursor, so they don't cancel it). The forwarder
// player keeps running: the user isn't driving that machine.
static void cancel_local_gesture() {
    gesture_players[GESTURE_LOCAL].strokes = nullptr;
}

// Jiggler-on confirmation: a ~250 px box traced down/up/right/left back to the
// start. Plays on the second computer (it is what gets jiggled) AND on the main
// computer so the user gets the confirmation on their own screen. Only fires on
// OFF -> ON toggles.
static const gesture_stroke_t JIGGLE_SWEEP[] = {
    { 0,  250 },   // down
    { 0, -250 },   // back up
    { 250,  0 },   // right
    { -250, 0 },   // back left
};

static void start_jiggle_activation_sweep() {
    gesture_start(GESTURE_FORWARDER, JIGGLE_SWEEP, GESTURE_LEN(JIGGLE_SWEEP), find_forwarder_screen());
    gesture_start(GESTURE_LOCAL, JIGGLE_SWEEP, GESTURE_LEN(JIGGLE_SWEEP), find_local_screen());
}

// Profile-number draw: when the active layout preset changes, trace its number
// (1..4) on the main computer with the cursor (no clicking) so the user can see
// which preset is now active. Strokes are deltas in a 200x400 px box; the first
// stroke of each digit moves from the post-switch centred cursor to the digit's
// start point (invisible positioning - there is no ink), the rest trace the
// glyph. Some digits include a short retrace, harmless without ink.
static const gesture_stroke_t DIGIT_1[] = {
    { 0, -200 },             // centre -> top
    { 0,  400 },             // straight down
};
static const gesture_stroke_t DIGIT_2[] = {
    { -100, -200 },          // centre -> top-left
    { 200, 0 },              // top bar, left -> right
    { 0, 200 },              // down the right side to the middle
    { -200, 200 },           // diagonal to bottom-left
    { 200, 0 },              // bottom bar, left -> right
};
static const gesture_stroke_t DIGIT_3[] = {
    { -100, -200 },          // centre -> top-left
    { 200, 0 },              // top bar
    { 0, 200 },              // down to the middle
    { -140, 0 },             // middle arm, in
    { 140, 0 },              // middle arm, back out (retrace)
    { 0, 200 },              // down to the bottom
    { -200, 0 },             // bottom bar
};
static const gesture_stroke_t DIGIT_4[] = {
    { 50, -200 },            // centre -> top of the right stem
    { -150, 260 },           // diagonal down-left
    { 200, 0 },              // crossbar, left -> right
    { -50, -260 },           // retrace back up to the top of the stem
    { 0, 400 },              // right stem, full height
};

static void start_profile_number_gesture(uint8_t profile_index) {
    int8_t screen = find_local_screen();
    if (screen < 0) return;
    const gesture_stroke_t* strokes = nullptr;
    uint8_t n = 0;
    switch (profile_index) {
        case 0: strokes = DIGIT_1; n = GESTURE_LEN(DIGIT_1); break;
        case 1: strokes = DIGIT_2; n = GESTURE_LEN(DIGIT_2); break;
        case 2: strokes = DIGIT_3; n = GESTURE_LEN(DIGIT_3); break;
        case 3: strokes = DIGIT_4; n = GESTURE_LEN(DIGIT_4); break;
        default: return;  // only 1..4 (NPROFILES) have a glyph
    }
    gesture_start(GESTURE_LOCAL, strokes, n, screen);
}

void forwarder_serial_init() {
    uart_init(FORWARDER_UART, FORWARDER_BAUDRATE);
    uart_set_translate_crlf(FORWARDER_UART, false);
    gpio_set_function(FORWARDER_TX_PIN, GPIO_FUNC_UART);
}

int main() {
    mutex_init(&their_usages_mutex);
    extra_init();
    forwarder_serial_init();
    parse_our_descriptor();
    load_config();
    board_init();
    tusb_init();

    // Force the host to re-enumerate on every boot. After an SWD flash the core
    // restarts but the USB line state can persist, so macOS keeps the stale
    // connection and the HID interface comes up half-dead until the cables are
    // power-cycled. An explicit detach/attach makes the host drop and re-enumerate
    // the device cleanly, so a flash no longer needs an unplug.
    tud_disconnect();
    sleep_ms(100);
    tud_connect();

    tud_sof_isr_set(sof_handler);

    next_print = time_us_64() + 1000000;
    last_input_per_output_us[0] = time_us_64();
    last_input_per_output_us[1] = time_us_64();

    static bool wakeup_sent = false;

    while (true) {
        if (read_report()) {
            process_mapping(get_and_clear_tick_pending());
        }
        tud_task();
        if (tud_hid_ready()) {
            if (get_and_clear_tick_pending()) {
                process_mapping(true);
            }
            send_report();
        } else if (tud_suspended() && or_items > 0) {
            // The local USB host is asleep. Drain reports bound for the remote
            // (output 1) over serial so it still gets jiggled, without disturbing
            // the local machine. Only issue a remote wakeup for a report actually
            // destined for the local host (output 0) - maybe_jiggle() won't queue
            // an output-0 report while suspended, so an output-0 report at the
            // head is genuine user input, where wake-on-input is wanted.
            uint8_t head_idx = outgoing_reports[or_head][0];
            uint8_t head_output = (screens.count(head_idx) && screens[head_idx].output != 0) ? 1 : 0;
            if (head_output == 1) {
                send_report();
            } else if (!wakeup_sent) {
                tud_remote_wakeup();
                wakeup_sent = true;
            }
        }
        if (!tud_suspended()) {
            wakeup_sent = false;
        }

        if (their_descriptor_updated) {
            update_their_descriptor_derivates();
            their_descriptor_updated = false;
        }
        if (need_to_persist_config) {
            persist_config();
            need_to_persist_config = false;
        }

        maybe_jiggle();
        service_blink();
        service_type_text();
        service_gestures();

        print_stats();
    }

    return 0;
}
