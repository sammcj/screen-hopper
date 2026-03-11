#include <set>

#include "pico/time.h"

#include "descriptor_parser.h"
#include "dual.h"
#include "interval_override.h"
#include "remapper.h"
#include "serial.h"

void send_b_init() {
    b_init_t msg;
    msg.interval_override = interval_override;
    serial_write((uint8_t*) &msg, sizeof(msg));
}

// Interfaces (dev_addr<<8 | interface) B has announced to us, mirrored from the
// same DEVICE_CONNECTED/DEVICE_DISCONNECTED messages that drive their_usages.
// Lets us detect, without touching the descriptor mutex on the hot path, that a
// report arrived for an interface we have no descriptor for.
static std::set<uint16_t> known_interfaces;

// Throttle so a burst of unparseable reports (or boot retries) can't flood the
// A<->B link with replay requests.
static const uint32_t RESYNC_MIN_INTERVAL_US = 250000;  // <= 4 requests/sec
static const uint8_t BOOT_RESYNC_MAX_ATTEMPTS = 20;     // ~5 s of boot nudging
static uint64_t next_resync_request_us = 0;
static uint8_t boot_resync_attempts = 0;

static void send_request_device_list() {
    request_device_list_t msg;
    serial_write((uint8_t*) &msg, sizeof(msg));
}

// Ask B to replay its mounted-device list, no more often than the throttle
// interval. Returns true if a request was actually sent.
static bool request_device_list_throttled(uint64_t now) {
    if (now < next_resync_request_us) {
        return false;
    }
    send_request_device_list();
    next_resync_request_us = now + RESYNC_MIN_INTERVAL_US;
    return true;
}

// On boot A has no descriptors and B won't re-announce its devices on its own
// (it only does so on a physical mount), so nudge B to replay until we've heard
// about at least one device - or give up after a bounded number of tries, since
// B may simply have nothing attached. Later desyncs are caught by the per-report
// self-heal in serial_callback.
void service_b_resync() {
    if (!known_interfaces.empty()) {
        return;
    }
    if (boot_resync_attempts >= BOOT_RESYNC_MAX_ATTEMPTS) {
        return;
    }
    if (request_device_list_throttled(time_us_64())) {
        boot_resync_attempts++;
    }
}

void serial_callback(const uint8_t* data, uint16_t len) {
    switch ((DualCommand) data[0]) {
        case DualCommand::DEVICE_CONNECTED: {
            device_connected_t* msg = (device_connected_t*) data;
            uint16_t interface = (uint16_t) (msg->dev_addr << 8) | msg->interface;
            parse_descriptor(msg->vid, msg->pid, msg->report_descriptor, len - sizeof(device_connected_t), interface);
            known_interfaces.insert(interface);
            break;
        }
        case DualCommand::DEVICE_DISCONNECTED: {
            device_disconnected_t* msg = (device_disconnected_t*) data;
            clear_descriptor_data(msg->dev_addr);
            // clear_descriptor_data drops every interface on this dev_addr; mirror that.
            for (auto it = known_interfaces.begin(); it != known_interfaces.end();) {
                if ((*it >> 8) == msg->dev_addr) {
                    it = known_interfaces.erase(it);
                } else {
                    ++it;
                }
            }
            break;
        }
        case DualCommand::REPORT_RECEIVED: {
            report_received_t* msg = (report_received_t*) data;
            uint16_t interface = (uint16_t) (msg->dev_addr << 8) | msg->interface;
            if (!known_interfaces.count(interface)) {
                // Input for an interface we have no descriptor for - A likely
                // restarted while B kept running, so B never re-announced it.
                // Ask B to replay (throttled); handle_received_report no-ops
                // until the descriptor arrives.
                request_device_list_throttled(time_us_64());
            }
            handle_received_report(msg->report, len - sizeof(report_received_t), interface);
            break;
        }
        case DualCommand::REQUEST_B_INIT:
            send_b_init();
            break;
        default:
            break;
    }
}

void extra_init() {
    serial_init();
}

bool read_report() {
    service_b_resync();
    return serial_read(serial_callback);
}

void interval_override_updated() {
    restart_t msg;
    serial_write((uint8_t*) &msg, sizeof(msg));
}
