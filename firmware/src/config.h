#ifndef _CONFIG_H_
#define _CONFIG_H_

#include <stdint.h>

void load_config();
void persist_config();

// Make profile_slot[slot] the live profile and mirror it into globals.
// Re-derives reverse_mapping, screen bounds, and runtime cursor state. Safe to
// call from process_mapping in response to a hotkey edge. No-op if slot is out
// of range.
void activate_profile(uint8_t slot);

uint8_t get_active_profile();
uint8_t get_profile_count();

#endif
