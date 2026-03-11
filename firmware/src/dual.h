#ifndef _DUAL_H_
#define _DUAL_H_

#include <stdint.h>

enum class DualCommand : uint8_t {
    DEVICE_CONNECTED = 1,
    DEVICE_DISCONNECTED = 2,
    REPORT_RECEIVED = 3,
    REQUEST_B_INIT = 4,
    B_INIT = 5,
    RESTART = 6,
    REQUEST_DEVICE_LIST = 7,
};

struct __attribute__((packed)) device_connected_t {
    DualCommand command = DualCommand::DEVICE_CONNECTED;
    uint16_t vid;
    uint16_t pid;
    uint8_t dev_addr;
    uint8_t interface;
    uint8_t report_descriptor[0];
};

struct __attribute__((packed)) device_disconnected_t {
    DualCommand command = DualCommand::DEVICE_DISCONNECTED;
    uint8_t dev_addr;
    uint8_t interface;
};

struct __attribute__((packed)) report_received_t {
    DualCommand command = DualCommand::REPORT_RECEIVED;
    uint8_t dev_addr;
    uint8_t interface;
    uint8_t report[0];
};

struct __attribute__((packed)) request_b_init_t {
    DualCommand command = DualCommand::REQUEST_B_INIT;
};

struct __attribute__((packed)) b_init_t {
    DualCommand command = DualCommand::B_INIT;
    uint8_t interval_override;
};

struct __attribute__((packed)) restart_t {
    DualCommand command = DualCommand::RESTART;
};

// A -> B: re-send a DEVICE_CONNECTED for every HID instance currently mounted on
// B. B announces devices only from tuh_hid_mount_cb, so when A restarts on its
// own (e.g. an SWD/probe flash that resets A but not B) it comes up with no
// descriptors and can't parse B's input reports. A asks for this replay on boot
// and whenever it sees a report for an interface it has no descriptor for.
struct __attribute__((packed)) request_device_list_t {
    DualCommand command = DualCommand::REQUEST_DEVICE_LIST;
};

#endif
