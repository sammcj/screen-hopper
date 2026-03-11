#include <bsp/board.h>
#include <tusb.h>

#include "hardware/gpio.h"
#include "hardware/watchdog.h"
#include "pico/stdlib.h"

#include "our_descriptor.h"
#include "serial.h"

#define FORWARDER_UART uart1
#define FORWARDER_RX_PIN 9

bool led_state = false;

// Wake-from-sleep recovery. The second computer's USB bus only suspends when
// that machine sleeps (during normal use the host keeps the bus active). After
// a long sleep the device can come back enumerated-but-dead on the host: the
// bus resumes but the host's HID stack for us is stale, so no forwarded input
// reaches it until a physical replug. Force a clean re-enumeration on resume,
// mirroring the boot-time detach/attach in remapper.cc that fixed the same
// "half-dead until replug" symptom on Pico A. Gate on suspend duration so only
// a genuine sleep (not a hypothetical brief OS suspend) triggers the bounce.
static const uint64_t REENUM_SUSPEND_THRESHOLD_US = 5000000;  // 5 s
static volatile uint64_t suspend_started_us = 0;
static volatile uint64_t last_suspend_duration_us = 0;
static volatile bool resume_pending = false;

// While the host is asleep, retry the remote wakeup on this interval instead of
// latching after a single attempt. A Mac in deep sleep often ignores the first
// device-initiated wakeup; latching meant a morning mouse-nudge could never wake
// the second computer until a power cycle. Only retried when Pico A is actually
// sending us frames (i.e. the user is trying to drive the remote).
static const uint64_t WAKEUP_RETRY_INTERVAL_US = 1000000;  // 1 s
static uint64_t last_wakeup_us = 0;

// Stuck-while-awake auto-recovery. The resume bounce above only fires on a clean
// tud_resume_cb(); a VBUS brown-out during the second computer's sleep transition
// can leave us enumerated-but-dead with no suspend/resume callback at all. If
// Pico A is feeding us frames (so the remote is in use) but the HID endpoint
// stays undeliverable while the bus is NOT suspended, escalate: first a
// re-enumeration bounce, then a cold watchdog reboot - the automatic equivalent
// of the manual power cycle that was previously the only fix.
static const uint64_t RECENT_RX_WINDOW_US = 1500000;  // frames seen this recently => remote in use
static const uint64_t RECOVERY_STEP_US = 3000000;     // 3 s stuck before each escalation
static uint64_t last_serial_rx_us = 0;
static uint64_t undeliverable_since_us = 0;
static bool bounce_tried = false;

// Only relay reports whose ID this device actually advertises. A garbled serial
// frame, or a Pico A running newer firmware with a report ID this forwarder's
// descriptor doesn't declare, would otherwise be passed to tud_hid_report() and
// can stall the HID interface on the remote host - killing all forwarded input.
static bool known_report_id(uint8_t report_id) {
    return report_id == REPORT_ID_MOUSE ||
           report_id == REPORT_ID_KEYBOARD ||
           report_id == REPORT_ID_CONSUMER ||
           report_id == REPORT_ID_RELATIVE;
}

void serial_callback(const uint8_t* data, uint16_t len) {
    // Any frame from Pico A means the remote is being driven. Stamp it so the
    // main-loop recovery knows delivery is wanted right now.
    last_serial_rx_us = time_us_64();

    if (tud_suspended()) {
        if (last_serial_rx_us - last_wakeup_us >= WAKEUP_RETRY_INTERVAL_US) {
            tud_remote_wakeup();
            last_wakeup_us = last_serial_rx_us;
        }
        return;
    }
    if (len < 2 || !known_report_id(data[0])) {
        return;
    }
    if (!tud_hid_ready()) {
        // Bus is up but the endpoint won't take a report; drop it and let the
        // main loop's stuck-detector escalate to a bounce/reboot.
        return;
    }
    tud_hid_report(data[0], data + 1, len - 1);
    board_led_write(led_state);
    led_state = !led_state;
}

void forwarder_serial_init() {
    uart_init(FORWARDER_UART, FORWARDER_BAUDRATE);
    uart_set_translate_crlf(FORWARDER_UART, false);
    gpio_set_function(FORWARDER_RX_PIN, GPIO_FUNC_UART);
}

// Invoked from tud_task() (not ISR) when the USB bus suspends/resumes, so it is
// safe to read the clock and set flags here. The heavy re-enumeration work is
// deferred to the main loop rather than done in the callback.
void tud_suspend_cb(bool remote_wakeup_en) {
    (void) remote_wakeup_en;
    suspend_started_us = time_us_64();
}

void tud_resume_cb() {
    last_suspend_duration_us = time_us_64() - suspend_started_us;
    resume_pending = true;
}

// Escalating recovery when remote input is being delivered to us but the host
// endpoint is stuck. Returns nothing; reboots the device if the bounce fails.
static void service_stuck_recovery() {
    uint64_t now = time_us_64();
    bool remote_in_use = (now - last_serial_rx_us) < RECENT_RX_WINDOW_US;

    if (tud_suspended() || !remote_in_use || tud_hid_ready()) {
        undeliverable_since_us = 0;
        bounce_tried = false;
        return;
    }

    if (undeliverable_since_us == 0) {
        undeliverable_since_us = now;
        return;
    }
    if (now - undeliverable_since_us < RECOVERY_STEP_US) {
        return;
    }

    if (!bounce_tried) {
        tud_disconnect();
        sleep_ms(100);
        tud_connect();
        bounce_tried = true;
        undeliverable_since_us = now;  // give the re-enumeration a full window
    } else {
        // Bounce didn't restore delivery: cold-restart into the normal image.
        watchdog_reboot(0, 0, 0);
    }
}

int main() {
    board_init();
    tusb_init();

    // Force the host to re-enumerate on boot, same as remapper.cc. A warm reboot
    // (e.g. VBUS browning out as the second computer enters/leaves deep sleep)
    // can leave the USB line state asserted, so the host keeps the stale
    // connection and the HID interface comes up half-dead until a replug. An
    // explicit detach/attach makes the host drop and re-enumerate us cleanly.
    tud_disconnect();
    sleep_ms(100);
    tud_connect();

    forwarder_serial_init();

    while (true) {
        serial_read(serial_callback, FORWARDER_UART);
        tud_task();
        if (resume_pending) {
            resume_pending = false;
            // Only bounce after a real sleep, not a brief suspend.
            if (last_suspend_duration_us > REENUM_SUSPEND_THRESHOLD_US) {
                tud_disconnect();
                sleep_ms(100);
                tud_connect();
            }
        }
        service_stuck_recovery();
    }

    return 0;
}

void tud_hid_set_report_cb(uint8_t itf, uint8_t report_id, hid_report_type_t report_type, uint8_t const* buffer, uint16_t bufsize) {
}

uint16_t tud_hid_get_report_cb(uint8_t itf, uint8_t report_id, hid_report_type_t report_type, uint8_t* buffer, uint16_t reqlen) {
    return 0;
}
