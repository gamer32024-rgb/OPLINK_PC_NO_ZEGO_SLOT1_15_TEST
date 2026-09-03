#ifndef PICO_H_TOUCH_PHASE3_REPORTS_H_
#define PICO_H_TOUCH_PHASE3_REPORTS_H_

#include <stdint.h>

#define REPORT_ID_TOUCH 1
#define REPORT_ID_MAX_CONTACTS 2
#define REPORT_ID_MOUSE 3

#define TOUCH_FLAG_TIP_SWITCH 0x01
#define TOUCH_FLAG_IN_RANGE 0x02
#define TOUCH_LOGICAL_MAX 32767u
#define TOUCH_MAX_CONTACTS 1u
#define MOUSE_BUTTON_LEFT 0x01u

typedef struct __attribute__((packed)) {
    uint8_t flags;
    uint8_t contact_id;
    uint16_t x;
    uint16_t y;
    uint16_t scan_time;
    uint8_t contact_count;
} touch_report_t;

typedef struct __attribute__((packed)) {
    uint8_t buttons;
    uint16_t x;
    uint16_t y;
} mouse_report_t;

#endif
