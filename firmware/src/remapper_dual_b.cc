#include <bsp/board.h>
#include <tusb.h>

#include "hardware/watchdog.h"
#include "pico/stdio.h"
#include "pico/time.h"

#include "dual.h"
#include "interval_override.h"
#include "serial.h"

bool led_state;
uint8_t buffer[SERIAL_MAX_PAYLOAD_SIZE + sizeof(device_connected_t)];
bool initialized = false;

// Cache of the HID instances currently mounted on B so we can replay their
// descriptors to A on demand (REQUEST_DEVICE_LIST). B only announces a device
// from tuh_hid_mount_cb, so without this an A-only restart leaves A unable to
// parse B's reports. Sized to CFG_TUH_HID (the max simultaneous HID instances);
// a descriptor that wouldn't fit one serial frame is dropped (the live mount
// send is bounded by the same limit, so any device that mounts also fits here).
#define MAX_CACHED_DEVICES CFG_TUH_HID
#define MAX_CACHED_DESC_LEN (SERIAL_MAX_PAYLOAD_SIZE - (uint16_t) sizeof(device_connected_t))

struct cached_device_t {
    bool in_use;
    uint16_t vid;
    uint16_t pid;
    uint8_t dev_addr;
    uint8_t instance;
    uint16_t desc_len;
    uint8_t desc[MAX_CACHED_DESC_LEN];
};
static cached_device_t device_cache[MAX_CACHED_DEVICES];

void send_device_connected(uint16_t vid, uint16_t pid, uint8_t dev_addr, uint8_t instance, const uint8_t* desc, uint16_t desc_len) {
    device_connected_t* msg = (device_connected_t*) buffer;
    msg->command = DualCommand::DEVICE_CONNECTED;
    msg->vid = vid;
    msg->pid = pid;
    msg->dev_addr = dev_addr;
    msg->interface = instance;
    memcpy(msg->report_descriptor, desc, desc_len);
    serial_write((uint8_t*) msg, desc_len + sizeof(device_connected_t));
}

void cache_device(uint16_t vid, uint16_t pid, uint8_t dev_addr, uint8_t instance, const uint8_t* desc, uint16_t desc_len) {
    if (desc_len > MAX_CACHED_DESC_LEN) {
        return;
    }
    int slot = -1;
    for (int i = 0; i < MAX_CACHED_DEVICES; i++) {
        if (device_cache[i].in_use && device_cache[i].dev_addr == dev_addr && device_cache[i].instance == instance) {
            slot = i;
            break;
        }
    }
    if (slot < 0) {
        for (int i = 0; i < MAX_CACHED_DEVICES; i++) {
            if (!device_cache[i].in_use) {
                slot = i;
                break;
            }
        }
    }
    if (slot < 0) {
        return;
    }
    device_cache[slot].in_use = true;
    device_cache[slot].vid = vid;
    device_cache[slot].pid = pid;
    device_cache[slot].dev_addr = dev_addr;
    device_cache[slot].instance = instance;
    device_cache[slot].desc_len = desc_len;
    memcpy(device_cache[slot].desc, desc, desc_len);
}

void uncache_device(uint8_t dev_addr, uint8_t instance) {
    for (int i = 0; i < MAX_CACHED_DEVICES; i++) {
        if (device_cache[i].in_use && device_cache[i].dev_addr == dev_addr && device_cache[i].instance == instance) {
            device_cache[i].in_use = false;
        }
    }
}

void send_cached_device_list() {
    for (int i = 0; i < MAX_CACHED_DEVICES; i++) {
        if (device_cache[i].in_use) {
            send_device_connected(device_cache[i].vid, device_cache[i].pid, device_cache[i].dev_addr, device_cache[i].instance, device_cache[i].desc, device_cache[i].desc_len);
        }
    }
}

void serial_callback(const uint8_t* data, uint16_t len) {
    switch ((DualCommand) data[0]) {
        case DualCommand::B_INIT:
            interval_override = ((b_init_t*) data)->interval_override;
            initialized = true;
            break;
        case DualCommand::RESTART:
            watchdog_reboot(0, 0, 0);
            break;
        case DualCommand::REQUEST_DEVICE_LIST:
            send_cached_device_list();
            break;
        default:
            break;
    }
}

void request_b_init() {
    request_b_init_t msg;
    serial_write((uint8_t*) &msg, sizeof(msg));
}

int main() {
    serial_init();
    board_init();

    while (!initialized) {
        request_b_init();
        serial_read(serial_callback);
    }

    tusb_init();

    while (true) {
        tuh_task();
        serial_read(serial_callback);
    }

    return 0;
}

void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const* report, uint16_t len) {
    led_state = !led_state;
    board_led_write(led_state);

    report_received_t* msg = (report_received_t*) buffer;
    msg->command = DualCommand::REPORT_RECEIVED;
    msg->dev_addr = dev_addr;
    msg->interface = instance;
    memcpy(msg->report, report, len);
    serial_write((uint8_t*) msg, len + sizeof(report_received_t));
    tuh_hid_receive_report(dev_addr, instance);
}

void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const* desc_report, uint16_t desc_len) {
    printf("tuh_hid_mount_cb\n");
    stdio_flush();
    uint16_t vid;
    uint16_t pid;
    tuh_vid_pid_get(dev_addr, &vid, &pid);
    cache_device(vid, pid, dev_addr, instance, desc_report, desc_len);
    send_device_connected(vid, pid, dev_addr, instance, desc_report, desc_len);
    tuh_hid_receive_report(dev_addr, instance);
}

void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
    printf("tuh_hid_umount_cb %d %d\n", dev_addr, instance);
    stdio_flush();
    uncache_device(dev_addr, instance);
    device_disconnected_t msg;
    msg.dev_addr = dev_addr;
    msg.interface = instance;
    serial_write((uint8_t*) &msg, sizeof(msg));
}
