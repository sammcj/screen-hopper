#include <unordered_set>
#include <cstring>

#include <bsp/board.h>
#include <tusb.h>

#include <pico/bootrom.h>
#include <pico/stdlib.h>

#include <hardware/flash.h>

#include "baked_config.h"
#include "config.h"
#include "crc.h"
#include "globals.h"
#include "interval_override.h"
#include "our_descriptor.h"
#include "remapper.h"
#include "types.h"

// CONFIG_VERSION 8 introduces the multi-profile persisted layout:
//   [device_persist_header_t][profile_slot_t × NPROFILES][padding][crc32]
// fitted into a single 4 KB flash sector. Each slot carries a full
// persist_config_t + a fixed-capacity mappings array so slot indexing is O(1).
// CONFIG_VERSION 9 narrows screen_def_t.scale to uint16 and adds a per-screen
// uint16 drag_gain (host pointer tracking gain); screen_def_t stays 25 bytes so
// no wire payload or sector size changed.
// CONFIG_VERSION 11 reinterprets the two drag fields as an AFFINE gain model
// (drag_gain = base*1000, drag_curve_k = slope*1e4) instead of the v10 saturating
// curve. screen_def_t layout is unchanged (still 25 bytes); only the meaning of
// those two uint16s changes. See drag_advance_gain() in remapper.cc.
const uint8_t CONFIG_VERSION = 11;

const uint8_t SCREEN_HOPPER_DEVICE_FLAGS = 0x00;

#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)
#endif
const uint32_t CONFIG_OFFSET_IN_FLASH = (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE);
const uint8_t* FLASH_CONFIG_IN_MEMORY = (((uint8_t*) XIP_BASE) + CONFIG_OFFSET_IN_FLASH);

const uint8_t CONFIG_FLAG_UNMAPPED_PASSTHROUGH = 0x01;

ConfigCommand last_config_command = ConfigCommand::NO_COMMAND;
uint32_t requested_index = 0;

// Profile storage held in RAM. The host-visible "device state" is mirrored to
// the flash sector on PERSIST_CONFIG. The active slot's contents are also
// mirrored into the live globals (screens, config_mappings, scalars) - see
// mirror_slot_to_live().
static profile_slot_t pending_profiles[NPROFILES];
static uint8_t active_profile = 0;
static uint8_t profile_count = 1;
static uint8_t io_target_slot = 0;

uint8_t get_active_profile() { return active_profile; }
uint8_t get_profile_count() { return profile_count; }

bool checksum_ok(const uint8_t* buffer, uint16_t data_size) {
    return crc32(buffer, data_size - 4) == ((crc32_t*) (buffer + data_size - 4))->crc32;
}

bool wire_version_ok(const uint8_t* buffer) {
    return ((set_feature_t*) buffer)->version == CONFIG_VERSION;
}

static bool sector_version_ok(const uint8_t* buffer) {
    return ((device_persist_header_t*) buffer)->version == CONFIG_VERSION;
}

// Initialise an empty profile slot with the same defaults globals.cc used
// before profiles existed: unmapped passthrough on, two side-by-side 16:9
// screens (output 0 and output 1). This is only the fallback when a fresh
// board has neither a persisted sector nor a baked default - any real config
// overwrites these via try_load_sector().
static void zero_slot(profile_slot_t* slot) {
    memset(slot, 0, sizeof(*slot));
    slot->header.version = CONFIG_VERSION;
    slot->header.flags = CONFIG_FLAG_UNMAPPED_PASSTHROUGH;
    slot->header.partial_scroll_timeout = 1000000;
    slot->header.constraint_mode = ConstraintMode::VISIBLE;
    slot->header.offscreen_sensitivity = 4000;
    slot->header.screens[0] = (screen_def_t){
        .x = 0, .y = 0, .w = 16000000, .h = 9000000,
        .sensitivity = 4000, .output = 0, .scale = 0, .drag_gain = 1000,
        .drag_curve_k = 0,
    };
    slot->header.screens[1] = (screen_def_t){
        .x = 16000000, .y = 0, .w = 16000000, .h = 9000000,
        .sensitivity = 4000, .output = 1, .scale = 0, .drag_gain = 1000,
        .drag_curve_k = 0,
    };
}

static void init_default_profiles() {
    for (uint8_t i = 0; i < NPROFILES; i++) {
        zero_slot(&pending_profiles[i]);
    }
    active_profile = 0;
    profile_count = 1;
    io_target_slot = 0;
}

// Mirror pending_profiles[slot] into the live globals: the scalar settings,
// the screens map (only entries with w > 0 are inserted; others are left zeroed
// so the firmware never indexes screen geometry that the profile doesn't
// define), and the config_mappings vector. Caller is responsible for invoking
// screens_updated() and set_mapping_from_config() afterwards so derived state
// (bounds, reverse_mapping, screen_switching_usages, etc.) is rebuilt.
static void mirror_slot_to_live(uint8_t slot) {
    const profile_slot_t& s = pending_profiles[slot];
    const persist_config_t& h = s.header;

    unmapped_passthrough = (h.flags & CONFIG_FLAG_UNMAPPED_PASSTHROUGH) != 0;
    partial_scroll_timeout = h.partial_scroll_timeout;
    uint8_t prev_interval_override = interval_override;
    interval_override = h.interval_override;
    constraint_mode = h.constraint_mode;
    screens[-1].sensitivity = h.offscreen_sensitivity;
    coord_scale = h.coord_scale;
    edge_resistance = h.edge_resistance;
    jiggle_interval = h.jiggle_interval;

    for (uint8_t i = 0; i < NSCREENS; i++) {
        screens[i] = h.screens[i];
    }

    config_mappings.clear();
    uint32_t count = h.mapping_count;
    if (count > MAX_MAPPINGS_PER_PROFILE) count = MAX_MAPPINGS_PER_PROFILE;
    for (uint32_t i = 0; i < count; i++) {
        config_mappings.push_back(s.mappings[i]);
    }

    if (prev_interval_override != interval_override) {
        interval_override_updated();
    }
}

// Validate and adopt a persisted/baked sector. The buffer must be exactly
// FLASH_SECTOR_SIZE and have a valid trailing CRC and matching version.
static bool try_load_sector(const uint8_t* buffer) {
    if (!checksum_ok(buffer, FLASH_SECTOR_SIZE)) return false;
    if (!sector_version_ok(buffer)) return false;

    const device_persist_header_t* hdr = (const device_persist_header_t*) buffer;
    uint8_t count = hdr->profile_count;
    if (count == 0) count = 1;
    if (count > NPROFILES) count = NPROFILES;
    uint8_t active = hdr->active_profile;
    if (active >= count) active = 0;

    const profile_slot_t* slots = (const profile_slot_t*) (buffer + sizeof(device_persist_header_t));
    for (uint8_t i = 0; i < NPROFILES; i++) {
        pending_profiles[i] = slots[i];
        // Clamp the per-slot mapping_count so a corrupt count can't overrun
        // the fixed mappings array on activation.
        if (pending_profiles[i].header.mapping_count > MAX_MAPPINGS_PER_PROFILE) {
            pending_profiles[i].header.mapping_count = MAX_MAPPINGS_PER_PROFILE;
        }
    }

    profile_count = count;
    active_profile = active;
    io_target_slot = active;
    return true;
}

void load_config() {
    init_default_profiles();

    // Persisted-over-USB sector wins; baked default is the fallback so a freshly
    // flashed board comes up configured. A build with no baked sector falls
    // through to init_default_profiles()'s single empty profile.
    if (try_load_sector(FLASH_CONFIG_IN_MEMORY)) {
        // loaded from flash
#if HAVE_BAKED_CONFIG
    } else if (try_load_sector(baked_config)) {
        // loaded from baked default
#endif
    }

    mirror_slot_to_live(active_profile);
    screens_updated();
    set_mapping_from_config();
}

void activate_profile(uint8_t slot) {
    if (slot >= profile_count) return;
    active_profile = slot;
    io_target_slot = slot;
    mirror_slot_to_live(slot);
    screens_updated();
    set_mapping_from_config();
}

// Copy the active profile's current live globals back into
// pending_profiles[active_profile]. We do this opportunistically on writes to
// the active slot (SET_CONFIG / ADD_MAPPING / etc. already update live state
// directly; this keeps the slot buffer in sync so a later PERSIST_CONFIG flushes
// the same state to flash) and unconditionally at PERSIST_CONFIG time as a
// belt-and-braces step.
static void capture_live_to_active_slot() {
    profile_slot_t& s = pending_profiles[active_profile];
    persist_config_t& h = s.header;
    h.version = CONFIG_VERSION;
    h.flags = unmapped_passthrough ? CONFIG_FLAG_UNMAPPED_PASSTHROUGH : 0;
    h.partial_scroll_timeout = partial_scroll_timeout;
    h.mapping_count = config_mappings.size() > MAX_MAPPINGS_PER_PROFILE
                          ? MAX_MAPPINGS_PER_PROFILE
                          : (uint32_t) config_mappings.size();
    h.interval_override = interval_override;
    h.constraint_mode = constraint_mode;
    h.offscreen_sensitivity = screens[-1].sensitivity;
    h.coord_scale = coord_scale;
    h.edge_resistance = edge_resistance;
    h.jiggle_interval = jiggle_interval;
    for (uint8_t i = 0; i < NSCREENS; i++) {
        h.screens[i] = screens[i];
    }
    memset(s.mappings, 0, sizeof(s.mappings));
    for (uint32_t i = 0; i < h.mapping_count; i++) {
        s.mappings[i] = config_mappings[i];
    }
}

void fill_get_config(get_config_t* config) {
    // Slot-aware reads: when the host has SELECTed a non-active slot it expects
    // to see *that* slot's scalars, not the live globals. The active slot keeps
    // returning live values so settings adjusted via per-field commands are
    // visible immediately without a SELECT round trip.
    if (io_target_slot == active_profile) {
        config->version = CONFIG_VERSION;
        config->flags = unmapped_passthrough ? CONFIG_FLAG_UNMAPPED_PASSTHROUGH : 0;
        config->partial_scroll_timeout = partial_scroll_timeout;
        config->mapping_count = config_mappings.size();
        config->interval_override = interval_override;
        config->constraint_mode = constraint_mode;
        config->offscreen_sensitivity = screens[-1].sensitivity;
        config->coord_scale = coord_scale;
        config->edge_resistance = edge_resistance;
        config->jiggle_interval = jiggle_interval;
    } else {
        const persist_config_t& h = pending_profiles[io_target_slot].header;
        config->version = CONFIG_VERSION;
        config->flags = h.flags;
        config->partial_scroll_timeout = h.partial_scroll_timeout;
        config->mapping_count = h.mapping_count;
        config->interval_override = h.interval_override;
        config->constraint_mode = h.constraint_mode;
        config->offscreen_sensitivity = h.offscreen_sensitivity;
        config->coord_scale = h.coord_scale;
        config->edge_resistance = h.edge_resistance;
        config->jiggle_interval = h.jiggle_interval;
    }
    config->our_usage_count = our_usages_rle.size();
    config->their_usage_count = their_usages_rle.size();
}

static void fill_device_info(device_info_t* info) {
    info->version = CONFIG_VERSION;
    info->nprofiles = NPROFILES;
    info->max_mappings_per_profile = MAX_MAPPINGS_PER_PROFILE;
    info->active_profile = active_profile;
    info->profile_count = profile_count;
    info->io_target_slot = io_target_slot;
}

void persist_config() {
    capture_live_to_active_slot();

    static uint8_t buffer[FLASH_SECTOR_SIZE];
    memset(buffer, 0, sizeof(buffer));

    device_persist_header_t* hdr = (device_persist_header_t*) buffer;
    hdr->version = CONFIG_VERSION;
    hdr->flags = SCREEN_HOPPER_DEVICE_FLAGS;
    hdr->active_profile = active_profile;
    hdr->profile_count = profile_count;

    profile_slot_t* slots = (profile_slot_t*) (buffer + sizeof(device_persist_header_t));
    for (uint8_t i = 0; i < NPROFILES; i++) {
        slots[i] = pending_profiles[i];
    }

    ((crc32_t*) (buffer + FLASH_SECTOR_SIZE - 4))->crc32 = crc32(buffer, FLASH_SECTOR_SIZE - 4);

    uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(CONFIG_OFFSET_IN_FLASH, FLASH_SECTOR_SIZE);
    flash_range_program(CONFIG_OFFSET_IN_FLASH, buffer, FLASH_SECTOR_SIZE);
    restore_interrupts(ints);
}

void tud_mount_cb() {
    // reset hi-res scroll for when we reboot from Windows into Linux
    resolution_multiplier = 0;
}

uint16_t tud_hid_get_report_cb(uint8_t itf, uint8_t report_id, hid_report_type_t report_type, uint8_t* buffer, uint16_t reqlen) {
    if (report_id == REPORT_ID_MULTIPLIER && reqlen >= 1) {
        memcpy(buffer, &resolution_multiplier, 1);
        return 1;
    }
    if (report_id == REPORT_ID_CONFIG && reqlen >= CONFIG_SIZE) {
        get_feature_t* config_buffer = (get_feature_t*) buffer;
        memset(config_buffer, 0, sizeof(get_feature_t));
        switch (last_config_command) {
            case ConfigCommand::GET_CONFIG: {
                fill_get_config((get_config_t*) config_buffer);
                break;
            }
            case ConfigCommand::GET_MAPPING: {
                mapping_config_t* mapping_config = (mapping_config_t*) config_buffer;
                if (io_target_slot == active_profile) {
                    if (requested_index < config_mappings.size()) {
                        *mapping_config = config_mappings[requested_index];
                    }
                } else {
                    const profile_slot_t& s = pending_profiles[io_target_slot];
                    if (requested_index < s.header.mapping_count && requested_index < MAX_MAPPINGS_PER_PROFILE) {
                        *mapping_config = s.mappings[requested_index];
                    }
                }
                break;
            }
            case ConfigCommand::GET_OUR_USAGES: {
                usages_list_t* returned_usages = (usages_list_t*) config_buffer;
                for (uint32_t i = 0; (i < NUSAGES_IN_PACKET) && (requested_index + i < our_usages_rle.size()); i++) {
                    returned_usages->usages[i] = our_usages_rle[requested_index + i];
                }
                break;
            }
            case ConfigCommand::GET_THEIR_USAGES: {
                usages_list_t* returned_usages = (usages_list_t*) config_buffer;
                for (uint32_t i = 0; (i < NUSAGES_IN_PACKET) && (requested_index + i < their_usages_rle.size()); i++) {
                    returned_usages->usages[i] = their_usages_rle[requested_index + i];
                }
                break;
            }
            case ConfigCommand::GET_SCREEN: {
                screen_def_t* returned_screen = (screen_def_t*) config_buffer;
                if (requested_index < NSCREENS) {
                    if (io_target_slot == active_profile) {
                        *returned_screen = screens[requested_index];
                    } else {
                        *returned_screen = pending_profiles[io_target_slot].header.screens[requested_index];
                    }
                }
                break;
            }
            case ConfigCommand::GET_DEVICE_INFO: {
                fill_device_info((device_info_t*) config_buffer);
                break;
            }
            default:
                break;
        }
        config_buffer->crc32 = crc32((uint8_t*) config_buffer, CONFIG_SIZE - 4);
        return CONFIG_SIZE;
    }

    return 0;
}

// Apply scalar SET_CONFIG fields to the target slot's pending buffer. When
// targeting the active slot, also push them into the live globals so changes
// take effect immediately (existing single-config behaviour).
static void apply_set_config_to_slot(uint8_t slot, const set_config_t* config) {
    persist_config_t& h = pending_profiles[slot].header;
    h.flags = config->flags;
    h.partial_scroll_timeout = config->partial_scroll_timeout;
    h.interval_override = config->interval_override;
    h.constraint_mode = config->constraint_mode;
    h.offscreen_sensitivity = config->offscreen_sensitivity;
    h.coord_scale = config->coord_scale;
    h.edge_resistance = config->edge_resistance;
    h.jiggle_interval = config->jiggle_interval;

    if (slot == active_profile) {
        unmapped_passthrough = (config->flags & CONFIG_FLAG_UNMAPPED_PASSTHROUGH) != 0;
        partial_scroll_timeout = config->partial_scroll_timeout;
        uint8_t prev_interval_override = interval_override;
        interval_override = config->interval_override;
        if (prev_interval_override != interval_override) {
            interval_override_updated();
        }
        constraint_mode = config->constraint_mode;
        screens[-1].sensitivity = config->offscreen_sensitivity;
        coord_scale = config->coord_scale;
        edge_resistance = config->edge_resistance;
        jiggle_interval = config->jiggle_interval;
        set_mapping_from_config();
    }
}

static void clear_slot_mappings(uint8_t slot) {
    pending_profiles[slot].header.mapping_count = 0;
    memset(pending_profiles[slot].mappings, 0, sizeof(pending_profiles[slot].mappings));
    if (slot == active_profile) {
        config_mappings.clear();
        set_mapping_from_config();
    }
}

static void add_slot_mapping(uint8_t slot, const mapping_config_t* m) {
    profile_slot_t& s = pending_profiles[slot];
    if (s.header.mapping_count >= MAX_MAPPINGS_PER_PROFILE) {
        return;  // silently drop overflow; host should respect MAX_MAPPINGS_PER_PROFILE
    }
    s.mappings[s.header.mapping_count++] = *m;
    if (slot == active_profile) {
        config_mappings.push_back(*m);
        set_mapping_from_config();
    }
}

static void set_slot_screen(uint8_t slot, uint8_t index, const screen_def_t* screen) {
    if (index >= NSCREENS) return;
    pending_profiles[slot].header.screens[index] = *screen;
    if (slot == active_profile) {
        screens[index] = *screen;
        screens_updated();
    }
}

void tud_hid_set_report_cb(uint8_t itf, uint8_t report_id, hid_report_type_t report_type, uint8_t const* buffer, uint16_t bufsize) {
    if (report_id == REPORT_ID_MULTIPLIER && bufsize >= 1) {
        memcpy(&resolution_multiplier, buffer, 1);
    }
    if (report_id == REPORT_ID_CONFIG && bufsize >= CONFIG_SIZE) {
        if (checksum_ok(buffer, CONFIG_SIZE) && wire_version_ok(buffer)) {
            set_feature_t* config_buffer = (set_feature_t*) buffer;
            last_config_command = config_buffer->command;
            switch (config_buffer->command) {
                case ConfigCommand::RESET_INTO_BOOTSEL:
                    reset_usb_boot(0, 0);
                    break;
                case ConfigCommand::SET_CONFIG: {
                    set_config_t* config = (set_config_t*) config_buffer->data;
                    apply_set_config_to_slot(io_target_slot, config);
                    break;
                }
                case ConfigCommand::CLEAR_MAPPING:
                    clear_slot_mappings(io_target_slot);
                    break;
                case ConfigCommand::ADD_MAPPING: {
                    mapping_config_t* mapping_config = (mapping_config_t*) config_buffer->data;
                    add_slot_mapping(io_target_slot, mapping_config);
                    break;
                }
                case ConfigCommand::GET_MAPPING:
                case ConfigCommand::GET_OUR_USAGES:
                case ConfigCommand::GET_THEIR_USAGES:
                case ConfigCommand::GET_SCREEN: {
                    get_indexed_t* get_indexed = (get_indexed_t*) config_buffer->data;
                    requested_index = get_indexed->requested_index;
                    break;
                }
                case ConfigCommand::PERSIST_CONFIG:
                    need_to_persist_config = true;
                    break;
                case ConfigCommand::SUSPEND:
                    suspended = true;
                    break;
                case ConfigCommand::RESUME:
                    suspended = false;
                    break;
                case ConfigCommand::SET_SCREEN: {
                    set_screen_t* set_screen = (set_screen_t*) config_buffer->data;
                    set_slot_screen(io_target_slot, set_screen->index, &set_screen->screen);
                    break;
                }
                case ConfigCommand::SELECT_PROFILE: {
                    select_profile_t* sel = (select_profile_t*) config_buffer->data;
                    if (sel->slot < NPROFILES) {
                        io_target_slot = sel->slot;
                    }
                    break;
                }
                case ConfigCommand::SET_ACTIVE_PROFILE: {
                    set_active_profile_t* sap = (set_active_profile_t*) config_buffer->data;
                    if (sap->slot < NPROFILES) {
                        if (sap->slot >= profile_count) {
                            profile_count = sap->slot + 1;
                        }
                        active_profile = sap->slot;
                        io_target_slot = sap->slot;
                        mirror_slot_to_live(sap->slot);
                        screens_updated();
                        set_mapping_from_config();
                    }
                    break;
                }
                case ConfigCommand::SET_PROFILE_COUNT: {
                    set_profile_count_t* spc = (set_profile_count_t*) config_buffer->data;
                    uint8_t count = spc->count;
                    if (count < 1) count = 1;
                    if (count > NPROFILES) count = NPROFILES;
                    profile_count = count;
                    if (active_profile >= profile_count) {
                        active_profile = 0;
                        mirror_slot_to_live(0);
                        screens_updated();
                        set_mapping_from_config();
                    }
                    break;
                }
                case ConfigCommand::GET_DEVICE_INFO:
                    // No payload to consume; the actual data is returned via
                    // the subsequent GET feature report (handled in
                    // tud_hid_get_report_cb's GET_DEVICE_INFO case).
                    break;
                case ConfigCommand::TYPE_TEXT: {
                    type_text_t* tt = (type_text_t*) config_buffer->data;
                    bool reset = (tt->flags & TYPE_TEXT_FLAG_RESET) != 0;
                    bool go = (tt->flags & TYPE_TEXT_FLAG_GO) != 0;
                    uint8_t len = tt->len;
                    if (len > sizeof(tt->data)) {
                        len = sizeof(tt->data);
                    }
                    handle_type_text_chunk(tt->data, len, reset, go);
                    break;
                }
                default:
                    break;
            }
        }
    }
}
