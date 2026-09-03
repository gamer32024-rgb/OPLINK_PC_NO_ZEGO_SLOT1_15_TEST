#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bsp/board_api.h"
#include "pico/bootrom.h"
#include "touch_reports.h"
#include "tusb.h"

#define FW_VERSION "0.4.1"
#define PROTO_VERSION 1
#define LINE_BUF_SIZE 128
#define RESPONSE_SIZE 160
#define BOOTSEL_DELAY_MS 150
#define TOUCH_QUEUE_CAPACITY 64

enum {
    BLINK_NOT_MOUNTED = 250,
    BLINK_MOUNTED = 1000,
    BLINK_SUSPENDED = 2500,
};

static uint32_t blink_interval_ms = BLINK_NOT_MOUNTED;
static char line_buf[LINE_BUF_SIZE];
static uint32_t line_len = 0;
static bool line_overflow = false;
static bool has_last_ack = false;
static uint32_t last_seq = 0;
static char last_ack[RESPONSE_SIZE];

static bool touch_pressed = false;
static uint16_t touch_x = TOUCH_LOGICAL_MAX / 2;
static uint16_t touch_y = TOUCH_LOGICAL_MAX / 2;
static touch_report_t touch_queue[TOUCH_QUEUE_CAPACITY];
static uint8_t touch_queue_head = 0;
static uint8_t touch_queue_tail = 0;
static uint8_t touch_queue_count = 0;
static bool mouse_pressed = false;
static uint16_t mouse_x = TOUCH_LOGICAL_MAX / 2;
static uint16_t mouse_y = TOUCH_LOGICAL_MAX / 2;
static mouse_report_t mouse_queue[TOUCH_QUEUE_CAPACITY];
static uint8_t mouse_queue_head = 0;
static uint8_t mouse_queue_tail = 0;
static uint8_t mouse_queue_count = 0;
static bool bootsel_pending = false;
static uint32_t bootsel_at_ms = 0;

static void cdc_write_line(char const *line) {
    tud_cdc_write_str(line);
    tud_cdc_write_str("\r\n");
    tud_cdc_write_flush();
}

static void save_and_send_ack(uint32_t seq, char const *body) {
    snprintf(last_ack, sizeof(last_ack), "ACK %lu %s", (unsigned long)seq, body);
    last_seq = seq;
    has_last_ack = true;
    cdc_write_line(last_ack);
}

static void send_err(uint32_t seq, char const *reason) {
    char response[RESPONSE_SIZE];
    snprintf(response, sizeof(response), "ERR %lu %s", (unsigned long)seq, reason);
    cdc_write_line(response);
}

static bool parse_u32(char const *text, uint32_t *out) {
    if (!text || !*text) {
        return false;
    }

    char *end = NULL;
    unsigned long value = strtoul(text, &end, 10);
    if (*end != '\0' || value > UINT32_MAX) {
        return false;
    }

    *out = (uint32_t)value;
    return true;
}

static bool parse_coordinate(char const *text, uint16_t *out) {
    uint32_t value = 0;
    if (!parse_u32(text, &value) || value > TOUCH_LOGICAL_MAX) {
        return false;
    }
    *out = (uint16_t)value;
    return true;
}

static uint16_t touch_scan_time(void) {
    // Windows requires Scan Time in 100 us units; uint16 rollover is expected.
    return (uint16_t)(board_millis() * 10u);
}

static bool enqueue_touch_report(bool pressed, uint16_t x, uint16_t y) {
    if (touch_queue_count >= TOUCH_QUEUE_CAPACITY) {
        return false;
    }

    touch_queue[touch_queue_tail] = (touch_report_t){
        .flags = pressed ? (TOUCH_FLAG_TIP_SWITCH | TOUCH_FLAG_IN_RANGE) : 0,
        .contact_id = 1,
        .x = x,
        .y = y,
        .scan_time = touch_scan_time(),
        .contact_count = pressed ? 1 : 0,
    };
    touch_queue_tail = (uint8_t)((touch_queue_tail + 1u) % TOUCH_QUEUE_CAPACITY);
    touch_queue_count++;
    return true;
}

static bool queue_touch_state(bool pressed, uint16_t x, uint16_t y) {
    if (!enqueue_touch_report(pressed, x, y)) {
        return false;
    }
    touch_pressed = pressed;
    touch_x = x;
    touch_y = y;
    return true;
}

static void cancel_touch(void) {
    touch_queue_head = 0;
    touch_queue_tail = 0;
    touch_queue_count = 0;
    touch_pressed = false;
    (void)enqueue_touch_report(false, touch_x, touch_y);
}

static bool queue_touch_release(void) {
    if (!enqueue_touch_report(false, touch_x, touch_y)) {
        return false;
    }
    touch_pressed = false;
    return true;
}

static bool enqueue_mouse_report(bool pressed, uint16_t x, uint16_t y) {
    if (mouse_queue_count >= TOUCH_QUEUE_CAPACITY) {
        return false;
    }

    mouse_queue[mouse_queue_tail] = (mouse_report_t){
        .buttons = pressed ? MOUSE_BUTTON_LEFT : 0,
        .x = x,
        .y = y,
    };
    mouse_queue_tail = (uint8_t)((mouse_queue_tail + 1u) % TOUCH_QUEUE_CAPACITY);
    mouse_queue_count++;
    return true;
}

static bool queue_mouse_state(bool pressed, uint16_t x, uint16_t y) {
    if (!enqueue_mouse_report(pressed, x, y)) {
        return false;
    }
    mouse_pressed = pressed;
    mouse_x = x;
    mouse_y = y;
    return true;
}

static void cancel_mouse(void) {
    mouse_queue_head = 0;
    mouse_queue_tail = 0;
    mouse_queue_count = 0;
    mouse_pressed = false;
    (void)enqueue_mouse_report(false, mouse_x, mouse_y);
}

static bool queue_mouse_release(void) {
    if (!enqueue_mouse_report(false, mouse_x, mouse_y)) {
        return false;
    }
    mouse_pressed = false;
    return true;
}

static void cancel_input(void) {
    cancel_touch();
    cancel_mouse();
}

static void hid_task(void) {
    if (!tud_hid_ready()) {
        return;
    }

    if (touch_queue_count > 0) {
        touch_report_t const *report = &touch_queue[touch_queue_head];
        if (tud_hid_report(REPORT_ID_TOUCH, report, sizeof(*report))) {
            touch_queue_head = (uint8_t)((touch_queue_head + 1u) % TOUCH_QUEUE_CAPACITY);
            touch_queue_count--;
        }
        return;
    }

    if (mouse_queue_count > 0) {
        mouse_report_t const *report = &mouse_queue[mouse_queue_head];
        if (tud_hid_report(REPORT_ID_MOUSE, report, sizeof(*report))) {
            mouse_queue_head = (uint8_t)((mouse_queue_head + 1u) % TOUCH_QUEUE_CAPACITY);
            mouse_queue_count--;
        }
    }
}

static bool check_sequence(uint32_t seq) {
    if (has_last_ack && seq == last_seq) {
        cdc_write_line(last_ack);
        return false;
    }
    if (has_last_ack && seq < last_seq) {
        send_err(seq, "OLD_SEQ");
        return false;
    }
    return true;
}

static void send_status(uint32_t seq) {
    char response[RESPONSE_SIZE];
    snprintf(response, sizeof(response),
             "STATE %lu mounted=%d cdc=1 hid=1 tip=%d mouse=%d x=%u y=%u mx=%u my=%u queued=%u last_seq=%lu",
             (unsigned long)seq,
             tud_mounted() ? 1 : 0,
             touch_pressed ? 1 : 0,
             mouse_pressed ? 1 : 0,
             (unsigned)touch_x,
             (unsigned)touch_y,
             (unsigned)mouse_x,
             (unsigned)mouse_y,
             (unsigned)(touch_queue_count + mouse_queue_count),
             has_last_ack ? (unsigned long)last_seq : 0UL);
    last_seq = seq;
    has_last_ack = true;
    snprintf(last_ack, sizeof(last_ack), "%s", response);
    cdc_write_line(response);
}

static void handle_command(char *cmd, char *seq_text, char *arg1, char *arg2, char *extra) {
    uint32_t seq = 0;
    if (!parse_u32(seq_text, &seq)) {
        send_err(0, "BAD_SEQ");
        return;
    }

    if (!check_sequence(seq)) {
        return;
    }

    if (strcmp(cmd, "PING") == 0) {
        if (arg1 || arg2 || extra) {
            send_err(seq, "BAD_ARGS");
            return;
        }
        save_and_send_ack(seq, "PONG");
        return;
    }

    if (strcmp(cmd, "STATUS") == 0) {
        if (arg1 || arg2 || extra) {
            send_err(seq, "BAD_ARGS");
            return;
        }
        send_status(seq);
        return;
    }

    if (strcmp(cmd, "RESET") == 0 || strcmp(cmd, "CANCEL") == 0) {
        if (arg1 || arg2 || extra) {
            send_err(seq, "BAD_ARGS");
            return;
        }
        cancel_input();
        save_and_send_ack(seq, "RESET tip=0");
        return;
    }

    if (strcmp(cmd, "BOOTSEL") == 0) {
        if (arg1 || arg2 || extra) {
            send_err(seq, "BAD_ARGS");
            return;
        }
        cancel_input();
        save_and_send_ack(seq, "BOOTSEL pending=1");
        bootsel_pending = true;
        bootsel_at_ms = board_millis() + BOOTSEL_DELAY_MS;
        return;
    }

    if (strcmp(cmd, "DOWN") == 0 || strcmp(cmd, "MOVE") == 0) {
        uint16_t x = 0;
        uint16_t y = 0;
        if (!arg1 || !arg2 || extra || !parse_coordinate(arg1, &x) || !parse_coordinate(arg2, &y)) {
            send_err(seq, "BAD_COORD");
            return;
        }
        if (strcmp(cmd, "MOVE") == 0 && !touch_pressed) {
            send_err(seq, "NO_CONTACT");
            return;
        }
        if (!queue_touch_state(true, x, y)) {
            send_err(seq, "HID_QUEUE_FULL");
            return;
        }
        save_and_send_ack(seq, strcmp(cmd, "DOWN") == 0 ? "DOWN queued=1" : "MOVE queued=1");
        return;
    }

    if (strcmp(cmd, "UP") == 0) {
        if (arg1 || arg2 || extra) {
            send_err(seq, "BAD_ARGS");
            return;
        }
        if (!queue_touch_release()) {
            send_err(seq, "HID_QUEUE_FULL");
            return;
        }
        save_and_send_ack(seq, "UP queued=1");
        return;
    }

    if (strcmp(cmd, "MDOWN") == 0 || strcmp(cmd, "MMOVE") == 0) {
        uint16_t x = 0;
        uint16_t y = 0;
        if (!arg1 || !arg2 || extra || !parse_coordinate(arg1, &x) || !parse_coordinate(arg2, &y)) {
            send_err(seq, "BAD_COORD");
            return;
        }
        if (strcmp(cmd, "MMOVE") == 0 && !mouse_pressed) {
            send_err(seq, "NO_MOUSE_BUTTON");
            return;
        }
        if (!queue_mouse_state(true, x, y)) {
            send_err(seq, "HID_QUEUE_FULL");
            return;
        }
        save_and_send_ack(seq, strcmp(cmd, "MDOWN") == 0 ? "MDOWN queued=1" : "MMOVE queued=1");
        return;
    }

    if (strcmp(cmd, "MUP") == 0) {
        if (arg1 || arg2 || extra) {
            send_err(seq, "BAD_ARGS");
            return;
        }
        if (!queue_mouse_release()) {
            send_err(seq, "HID_QUEUE_FULL");
            return;
        }
        save_and_send_ack(seq, "MUP queued=1");
        return;
    }

    send_err(seq, "BAD_CMD");
}

static void process_line(char *line) {
    char *cmd = strtok(line, " \t");
    char *arg1 = strtok(NULL, " \t");
    char *arg2 = strtok(NULL, " \t");
    char *arg3 = strtok(NULL, " \t");
    char *arg4 = strtok(NULL, " \t");

    if (!cmd) {
        return;
    }

    if (strcmp(cmd, "HELLO") == 0) {
        uint32_t proto = 0;
        if (!arg1 || arg2 || !parse_u32(arg1, &proto) || proto != PROTO_VERSION) {
            send_err(0, "BAD_HELLO");
            return;
        }
        has_last_ack = false;
        last_seq = 0;
        cdc_write_line("READY proto=1 fw=" FW_VERSION " hid=1");
        return;
    }

    handle_command(cmd, arg1, arg2, arg3, arg4);
}

static void cdc_task(void) {
    while (tud_cdc_available()) {
        uint8_t ch = 0;
        tud_cdc_read(&ch, 1);

        if (ch == '\r') {
            continue;
        }

        if (ch == '\n') {
            if (line_overflow) {
                send_err(0, "LINE_TOO_LONG");
            } else {
                line_buf[line_len] = '\0';
                process_line(line_buf);
            }
            line_len = 0;
            line_overflow = false;
            continue;
        }

        if (line_len + 1 >= LINE_BUF_SIZE) {
            line_overflow = true;
            continue;
        }

        line_buf[line_len++] = (char)ch;
    }
}

static void bootsel_task(void) {
    if (bootsel_pending && (int32_t)(board_millis() - bootsel_at_ms) >= 0) {
        reset_usb_boot(0, 0);
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

    cancel_input();

    if (board_init_after_tusb) {
        board_init_after_tusb();
    }

    while (true) {
        tud_task();
        cdc_task();
        hid_task();
        bootsel_task();
        led_blinking_task();
    }
}

void tud_mount_cb(void) {
    blink_interval_ms = BLINK_MOUNTED;
    cancel_input();
}

void tud_umount_cb(void) {
    blink_interval_ms = BLINK_NOT_MOUNTED;
    line_len = 0;
    line_overflow = false;
    cancel_input();
}

void tud_suspend_cb(bool remote_wakeup_en) {
    (void)remote_wakeup_en;
    blink_interval_ms = BLINK_SUSPENDED;
}

void tud_resume_cb(void) {
    blink_interval_ms = tud_mounted() ? BLINK_MOUNTED : BLINK_NOT_MOUNTED;
}

void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts) {
    (void)itf;
    (void)rts;
    if (dtr) {
        cdc_write_line("READY proto=1 fw=" FW_VERSION " hid=1");
    }
}

void tud_cdc_rx_cb(uint8_t itf) {
    (void)itf;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type, uint8_t *buffer,
                               uint16_t reqlen) {
    (void)instance;

    if (report_type == HID_REPORT_TYPE_FEATURE &&
        report_id == REPORT_ID_MAX_CONTACTS && reqlen >= 1) {
        buffer[0] = TOUCH_MAX_CONTACTS;
        return 1;
    }

    if (report_type == HID_REPORT_TYPE_INPUT && report_id == REPORT_ID_TOUCH &&
        reqlen >= sizeof(touch_report_t)) {
        touch_report_t report = {
            .flags = touch_pressed ? (TOUCH_FLAG_TIP_SWITCH | TOUCH_FLAG_IN_RANGE) : 0,
            .contact_id = 1,
            .x = touch_x,
            .y = touch_y,
            .scan_time = touch_scan_time(),
            .contact_count = touch_pressed ? 1 : 0,
        };
        memcpy(buffer, &report, sizeof(report));
        return sizeof(report);
    }

    if (report_type == HID_REPORT_TYPE_INPUT && report_id == REPORT_ID_MOUSE &&
        reqlen >= sizeof(mouse_report_t)) {
        mouse_report_t report = {
            .buttons = mouse_pressed ? MOUSE_BUTTON_LEFT : 0,
            .x = mouse_x,
            .y = mouse_y,
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
