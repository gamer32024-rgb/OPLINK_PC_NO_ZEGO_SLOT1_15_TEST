#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "bsp/board_api.h"
#include "tusb.h"
#include "touch_reports.h"

enum {
    BLINK_NOT_MOUNTED = 250,
    BLINK_MOUNTED = 1000,
    BLINK_SUSPENDED = 2500,
};

static uint32_t blink_interval_ms = BLINK_NOT_MOUNTED;
static bool sent_initial_release = false;

static uint16_t scan_time = 0;

static void send_touch_release_once(void) {
    if (sent_initial_release || !tud_hid_ready()) {
        return;
    }

    touch_report_t report = {
        .flags = 0,
        .contact_id = 1,
        .x = TOUCH_LOGICAL_MAX / 2,
        .y = TOUCH_LOGICAL_MAX / 2,
        .scan_time = scan_time++,
        .contact_count = 0,
    };

    if (tud_hid_report(REPORT_ID_TOUCH, &report, sizeof(report))) {
        sent_initial_release = true;
    }
}

static void led_blinking_task(void) {
    static uint32_t start_ms = 0;
    static bool led_state = false;

    if (board_millis() - start_ms < blink_interval_ms) {
        return;
    }
    start_ms += blink_interval_ms;

    board_led_write(led_state);
    led_state = !led_state;
}

int main(void) {
    board_init();

    tusb_rhport_init_t dev_init = {
        .role = TUSB_ROLE_DEVICE,
        .speed = TUSB_SPEED_AUTO,
    };
    tusb_init(BOARD_TUD_RHPORT, &dev_init);

    if (board_init_after_tusb) {
        board_init_after_tusb();
    }

    while (true) {
        tud_task();
        send_touch_release_once();
        led_blinking_task();
    }
}

void tud_mount_cb(void) {
    blink_interval_ms = BLINK_MOUNTED;
    sent_initial_release = false;
}

void tud_umount_cb(void) {
    blink_interval_ms = BLINK_NOT_MOUNTED;
    sent_initial_release = false;
}

void tud_suspend_cb(bool remote_wakeup_en) {
    (void)remote_wakeup_en;
    blink_interval_ms = BLINK_SUSPENDED;
}

void tud_resume_cb(void) {
    blink_interval_ms = tud_mounted() ? BLINK_MOUNTED : BLINK_NOT_MOUNTED;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type, uint8_t *buffer,
                               uint16_t reqlen) {
    (void)instance;

    if (report_type == HID_REPORT_TYPE_FEATURE &&
        report_id == REPORT_ID_MAX_CONTACTS &&
        reqlen >= 1) {
        buffer[0] = TOUCH_MAX_CONTACTS;
        return 1;
    }

    if (report_type == HID_REPORT_TYPE_INPUT &&
        report_id == REPORT_ID_TOUCH &&
        reqlen >= sizeof(touch_report_t)) {
        touch_report_t report = {
            .flags = 0,
            .contact_id = 1,
            .x = TOUCH_LOGICAL_MAX / 2,
            .y = TOUCH_LOGICAL_MAX / 2,
            .scan_time = scan_time++,
            .contact_count = 0,
        };
        memcpy(buffer, &report, sizeof(report));
        return sizeof(report);
    }

    return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                           hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize) {
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)bufsize;
}
