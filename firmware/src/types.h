#ifndef _TYPES_H_
#define _TYPES_H_

#include <stdint.h>

enum class ConfigCommand : int8_t {
    NO_COMMAND = 0,
    RESET_INTO_BOOTSEL = 1,
    SET_CONFIG = 2,
    GET_CONFIG = 3,
    CLEAR_MAPPING = 4,
    ADD_MAPPING = 5,
    GET_MAPPING = 6,
    PERSIST_CONFIG = 7,
    GET_OUR_USAGES = 8,
    GET_THEIR_USAGES = 9,
    SUSPEND = 10,
    RESUME = 11,
    SET_SCREEN = 12,
    GET_SCREEN = 13,
    // Profile-aware additions (CONFIG_VERSION 8+):
    SELECT_PROFILE = 14,        // route subsequent SET_*/GET_*/CLEAR_MAPPING to a slot
    SET_ACTIVE_PROFILE = 15,    // mirror a slot into live state immediately
    SET_PROFILE_COUNT = 16,     // how many slots are valid (1..NPROFILES)
    GET_DEVICE_INFO = 17,       // returns nprofiles/max_mappings/active/profile_count
    // CONFIG_VERSION 8+ (continued):
    TYPE_TEXT = 18,             // append ASCII bytes to the type buffer and
                                // optionally start replay through the forwarder
};

struct usage_def_t {
    uint8_t report_id;
    uint8_t size;
    uint16_t bitpos;
    bool is_relative;
    bool is_array = false;
    int32_t logical_minimum;
    uint32_t index = 0;  // for arrays
    uint32_t count = 0;  // for arrays
};

struct map_source_t {
    uint32_t usage;
    int32_t scaling = 1000;  // * 1000
    bool sticky = false;
    uint8_t layer = 0;
};

struct usage_rle_t {
    uint32_t usage;
    uint32_t count;
};

struct __attribute__((packed)) set_feature_t {
    uint8_t version;
    ConfigCommand command;
    uint8_t data[26];
    uint32_t crc32;
};

struct __attribute__((packed)) get_feature_t {
    uint8_t data[28];
    uint32_t crc32;
};

struct __attribute__((packed)) mapping_config_t {
    uint32_t target_usage;
    uint32_t source_usage;
    int32_t scaling;  // * 1000
    uint8_t layer;
    uint8_t flags;
};

enum class ConstraintMode : int8_t {
    NO_CONSTRAINT = 0,
    BOUNDING_BOX = 1,
    VISIBLE = 2,
};

struct __attribute__((packed)) screen_def_t {
    uint32_t x;
    uint32_t y;
    uint32_t w;
    uint32_t h;
    uint16_t sensitivity;  // mouse sensitivity * 1000. Narrowed from uint32
                           // (realistic values are a few thousand) to free 2
                           // bytes for drag_curve_k without growing the struct.
    uint8_t  output;       // 0 = USB (this computer), 1 = serial (forwarder)
    uint16_t scale;        // internal units per pixel on this screen's machine;
                           // 0 = fall back to global coord_scale (drag speed).
    uint16_t drag_gain;    // CONFIG_VERSION 11: affine-gain BASE * 1000 (host px
                           // per emitted relative count at v->0). The host renders
                           // a speed-dependent number of px per count during a drag
                           // (macOS accel); advancing the internal cursor by the
                           // wrong amount leaves it off the real cursor and the
                           // post-release absolute report snaps. Per-output: read
                           // from the active screen, whose `output` selects host.
                           // (Was the saturating-curve plateau in v9/v10.)
    uint16_t drag_curve_k; // CONFIG_VERSION 11: affine-gain SLOPE * 1e4 (px per
                           // count, per unit v). Effective per-flush gain is
                           //   gain(v) = drag_gain/1000 + (drag_curve_k/1e4)*v
                           // clamped to [0, DRAG_GAIN_CAP_MILLI/1000], v=|rx|+|ry|.
                           // base=slope=0 -> flat 1.0. Measured local Mac ~260/430.
                           // (Was the saturating-curve knee (knee_px*10)^2 in v10.)
};

#define NSCREENS 6

struct __attribute__((packed)) persist_config_t {
    uint8_t version;
    uint8_t flags;
    uint32_t partial_scroll_timeout;
    uint32_t mapping_count;
    uint8_t interval_override;
    ConstraintMode constraint_mode;
    uint32_t offscreen_sensitivity;
    uint32_t coord_scale;   // internal units per pixel; 0 = drag-relative disabled
    uint32_t edge_resistance;  // internal units of push needed to cross to the
                               // other computer (different output); 0 = instant
    uint16_t jiggle_interval;  // seconds between automatic jiggles of the
                               // inactive machine to stop it locking; 0 = off
    screen_def_t screens[NSCREENS];
};

struct __attribute__((packed)) get_config_t {
    uint8_t version;
    uint8_t flags;
    uint32_t partial_scroll_timeout;
    uint16_t mapping_count;      // narrowed from uint32_t (max ~292 mappings fit
                                 // the flash sector) to free 2 bytes for
    uint16_t our_usage_count;    // jiggle_interval below; get_config_t is full at
    uint16_t their_usage_count;  // CONFIG_SIZE, so every byte is accounted for
    uint8_t interval_override;
    ConstraintMode constraint_mode;
    uint32_t offscreen_sensitivity;
    uint32_t coord_scale;
    uint32_t edge_resistance;
    uint16_t jiggle_interval;
};

struct __attribute__((packed)) set_config_t {
    uint8_t flags;
    uint32_t partial_scroll_timeout;
    uint8_t interval_override;
    ConstraintMode constraint_mode;
    uint32_t offscreen_sensitivity;
    uint32_t coord_scale;
    uint32_t edge_resistance;
    uint16_t jiggle_interval;
};

struct __attribute__((packed)) get_indexed_t {
    uint32_t requested_index;
};

struct __attribute__((packed)) crc32_t {
    uint32_t crc32;
};

#define NUSAGES_IN_PACKET 3

struct __attribute__((packed)) usages_list_t {
    usage_rle_t usages[NUSAGES_IN_PACKET];
};

struct __attribute__((packed)) set_screen_t {
    uint8_t index;
    screen_def_t screen;
};

// Profile storage. The persisted flash sector holds up to NPROFILES independent
// profiles; one of them is the "active" profile whose contents are mirrored
// into the live globals (screens, config_mappings, scalars). A separate
// "io target slot" decides which profile a SET_CONFIG/ADD_MAPPING/SET_SCREEN
// command writes into and which one GET_CONFIG/GET_MAPPING/GET_SCREEN reads
// from; by default it equals the active profile so the legacy single-config
// flow keeps working.
#define NPROFILES 4
#define MAX_MAPPINGS_PER_PROFILE 32

struct __attribute__((packed)) profile_slot_t {
    persist_config_t header;
    mapping_config_t mappings[MAX_MAPPINGS_PER_PROFILE];
};

struct __attribute__((packed)) device_persist_header_t {
    uint8_t version;
    uint8_t flags;
    uint8_t active_profile;
    uint8_t profile_count;
    uint8_t _reserved[12];
};

struct __attribute__((packed)) select_profile_t {
    uint8_t slot;
};

struct __attribute__((packed)) set_active_profile_t {
    uint8_t slot;
};

struct __attribute__((packed)) set_profile_count_t {
    uint8_t count;
};

// Payload for ConfigCommand::TYPE_TEXT. The host CLI chunks the buffer into
// these (24 bytes per chunk), sending the first one with the RESET flag to
// clear any prior buffer and the last one with the GO flag to start replay.
// A single-chunk send sets both flags at once.
#define TYPE_TEXT_FLAG_RESET 0x01
#define TYPE_TEXT_FLAG_GO    0x02

struct __attribute__((packed)) type_text_t {
    uint8_t flags;
    uint8_t len;
    uint8_t data[24];
};

struct __attribute__((packed)) device_info_t {
    uint8_t version;        // matches CONFIG_VERSION; lets the host detect
                            // an old firmware that ignored GET_DEVICE_INFO
                            // and returned an all-zero response.
    uint8_t nprofiles;
    uint8_t max_mappings_per_profile;
    uint8_t active_profile;
    uint8_t profile_count;
    uint8_t io_target_slot;
};

#endif
