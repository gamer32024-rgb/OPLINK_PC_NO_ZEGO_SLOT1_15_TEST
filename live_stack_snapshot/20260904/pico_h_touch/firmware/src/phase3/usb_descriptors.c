#include <stdint.h>
#include <string.h>

#include "bsp/board_api.h"
#include "touch_reports.h"
#include "tusb.h"

#define USB_VID 0xCafe
#define USB_PID 0x4021
#define USB_BCD 0x0200

tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = USB_BCD,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = 0x0102,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&desc_device;
}

uint8_t const desc_hid_report[] = {
    0x05, 0x0D,
    0x09, 0x04,
    0xA1, 0x01,
    0x85, REPORT_ID_TOUCH,
    0x09, 0x22,
    0xA1, 0x02,
    0x09, 0x42,
    0x09, 0x32,
    0x15, 0x00,
    0x25, 0x01,
    0x75, 0x01,
    0x95, 0x02,
    0x81, 0x02,
    0x95, 0x06,
    0x81, 0x03,
    0x75, 0x08,
    0x95, 0x01,
    0x09, 0x51,
    0x25, 0x7F,
    0x81, 0x02,
    0x05, 0x01,
    0x26, 0xFF, 0x7F,
    0x46, 0xFF, 0x7F,
    0x75, 0x10,
    0x95, 0x02,
    0x09, 0x30,
    0x09, 0x31,
    0x81, 0x02,
    0x05, 0x0D,
    0x55, 0x0C,
    0x66, 0x01, 0x10,
    0x47, 0xFF, 0xFF, 0x00, 0x00,
    0x27, 0xFF, 0xFF, 0x00, 0x00,
    0x09, 0x56,
    0x75, 0x10,
    0x95, 0x01,
    0x27, 0xFF, 0xFF, 0x00, 0x00,
    0x81, 0x02,
    0xC0,
    0x09, 0x54,
    0x15, 0x00,
    0x25, 0x01,
    0x75, 0x08,
    0x95, 0x01,
    0x81, 0x02,
    0x85, REPORT_ID_MAX_CONTACTS,
    0x09, 0x55,
    0x25, 0x01,
    0x75, 0x08,
    0x95, 0x01,
    0xB1, 0x02,
    0xC0,

    0x05, 0x01,
    0x55, 0x00,
    0x65, 0x00,
    0x09, 0x02,
    0xA1, 0x01,
    0x85, REPORT_ID_MOUSE,
    0x09, 0x01,
    0xA1, 0x00,
    0x05, 0x09,
    0x19, 0x01,
    0x29, 0x03,
    0x15, 0x00,
    0x25, 0x01,
    0x95, 0x03,
    0x75, 0x01,
    0x81, 0x02,
    0x95, 0x01,
    0x75, 0x05,
    0x81, 0x03,
    0x05, 0x01,
    0x09, 0x30,
    0x09, 0x31,
    0x15, 0x00,
    0x26, 0xFF, 0x7F,
    0x35, 0x00,
    0x46, 0xFF, 0x7F,
    0x75, 0x10,
    0x95, 0x02,
    0x81, 0x02,
    0xC0,
    0xC0,
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    (void)instance;
    return desc_hid_report;
}

enum {
    ITF_NUM_CDC = 0,
    ITF_NUM_CDC_DATA,
    ITF_NUM_HID,
    ITF_NUM_TOTAL,
};

#define EPNUM_CDC_NOTIF 0x81
#define EPNUM_CDC_OUT 0x02
#define EPNUM_CDC_IN 0x82
#define EPNUM_HID 0x83
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_HID_DESC_LEN)

uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 4, EPNUM_CDC_NOTIF, 8, EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),
    TUD_HID_DESCRIPTOR(ITF_NUM_HID, 5, HID_ITF_PROTOCOL_NONE,
                       sizeof(desc_hid_report), EPNUM_HID,
                       CFG_TUD_HID_EP_BUFSIZE, 5),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return desc_configuration;
}

enum {
    STRID_LANGID = 0,
    STRID_MANUFACTURER,
    STRID_PRODUCT,
    STRID_SERIAL,
    STRID_CDC,
    STRID_HID,
};

char const *string_desc_arr[] = {
    (const char[]){0x09, 0x04},
    "Codex Pico",
    "Pico H Touch and Absolute Mouse",
    NULL,
    "Pico H CDC Command",
    "Pico H HID Touchscreen and Mouse",
};

static uint16_t desc_str[33];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    size_t chr_count = 0;

    if (index == STRID_LANGID) {
        memcpy(&desc_str[1], string_desc_arr[0], 2);
        chr_count = 1;
    } else if (index == STRID_SERIAL) {
        chr_count = board_usb_get_serial(desc_str + 1, 32);
    } else {
        if (index >= (sizeof(string_desc_arr) / sizeof(string_desc_arr[0]))) {
            return NULL;
        }
        const char *str = string_desc_arr[index];
        chr_count = strlen(str);
        if (chr_count > 32) {
            chr_count = 32;
        }
        for (size_t i = 0; i < chr_count; ++i) {
            desc_str[1 + i] = (uint8_t)str[i];
        }
    }

    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
    return desc_str;
}
