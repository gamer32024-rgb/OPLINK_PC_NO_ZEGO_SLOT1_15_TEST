#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bsp/board_api.h"
#include "tusb.h"

#define FW_VERSION "0.1.0"
#define PROTO_VERSION 1
#define LINE_BUF_SIZE 128
#define RESPONSE_SIZE 160

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

static void handle_sequenced_command(char *cmd, char *seq_text, char *extra) {
    uint32_t seq = 0;
    if (!parse_u32(seq_text, &seq)) {
        send_err(0, "BAD_SEQ");
        return;
    }

    if (extra != NULL) {
        send_err(seq, "TOO_MANY_FIELDS");
        return;
    }

    if (has_last_ack && seq == last_seq) {
        cdc_write_line(last_ack);
        return;
    }

    if (has_last_ack && seq < last_seq) {
        send_err(seq, "OLD_SEQ");
        return;
    }

    if (strcmp(cmd, "PING") == 0) {
        save_and_send_ack(seq, "PONG");
    } else if (strcmp(cmd, "STATUS") == 0) {
        char body[RESPONSE_SIZE];
        snprintf(body, sizeof(body),
                 "mounted=%d cdc=1 hid=0 tip=0 last_seq=%lu",
                 tud_mounted() ? 1 : 0,
                 has_last_ack ? (unsigned long)last_seq : 0UL);
        char response[RESPONSE_SIZE];
        snprintf(response, sizeof(response), "STATE %lu %s", (unsigned long)seq, body);
        last_seq = seq;
        has_last_ack = true;
        snprintf(last_ack, sizeof(last_ack), "%s", response);
        cdc_write_line(response);
    } else if (strcmp(cmd, "RESET") == 0 || strcmp(cmd, "CANCEL") == 0) {
        save_and_send_ack(seq, "RESET tip=0");
    } else {
        send_err(seq, "BAD_CMD");
    }
}

static void process_line(char *line) {
    char *cmd = strtok(line, " \t");
    char *arg1 = strtok(NULL, " \t");
    char *arg2 = strtok(NULL, " \t");
    char *arg3 = strtok(NULL, " \t");

    if (!cmd) {
        return;
    }

    if (strcmp(cmd, "HELLO") == 0) {
        if (!arg1 || arg2) {
            send_err(0, "BAD_HELLO");
            return;
        }
        uint32_t proto = 0;
        if (!parse_u32(arg1, &proto) || proto != PROTO_VERSION) {
            send_err(0, "BAD_PROTO");
            return;
        }
        cdc_write_line("READY proto=1 fw=" FW_VERSION " hid=0");
        return;
    }

    handle_sequenced_command(cmd, arg1, arg2);
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
        cdc_task();
        led_blinking_task();
    }
}

void tud_mount_cb(void) {
    blink_interval_ms = BLINK_MOUNTED;
}

void tud_umount_cb(void) {
    blink_interval_ms = BLINK_NOT_MOUNTED;
    line_len = 0;
    line_overflow = false;
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
        cdc_write_line("READY proto=1 fw=" FW_VERSION " hid=0");
    }
}

void tud_cdc_rx_cb(uint8_t itf) {
    (void)itf;
}
