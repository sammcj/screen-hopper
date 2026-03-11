#ifndef _REMAPPER_H_
#define _REMAPPER_H_

void set_mapping_from_config();
void handle_received_report(const uint8_t* report, int len, uint16_t interface);

void extra_init();
bool read_report();

void interval_override_updated();
void screens_updated();

void handle_type_text_chunk(const uint8_t* data, uint8_t len, bool reset, bool go);

#endif
