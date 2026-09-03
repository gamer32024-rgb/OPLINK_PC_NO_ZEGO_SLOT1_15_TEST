#include <stdint.h>
#include <string.h>

#include "bsp/board_api.h"
#include "tusb.h"
#include "touch_reports.h"

#define USB_VID 0xCafe
#define USB_PID 0x4020
#define USB_BCD 0x0200

tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = USB_BCD,
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&desc_device;
}

uint8_t const desc_hid_report[] = {
    0x05, 0x0D,                    // Usage Page (Digitizers)
    0x09, 0x04,                    // Usage (Touch Screen)
    0xA1, 0x01,                    // Collection (Application)
    0x85, REPORT_ID_TOUCH,         //   Report ID (Touch)
    0x09, 0x22,                    //   Usage (Finger)
    0xA1, 0x02,                    //   Collection (Logical)
    0x09, 0x42,                    //     Usage (Tip Switch)
    0x09, 0x32,                    //     Usage (In Range)
    0x15, 0x00,                    //     Logical Minimum (0)
    0x25, 0x01,                    //     Logical Maximum (1)
    0x75, 0x01,                    //     Report Size (1)
    0x95, 0x02,                    //     Report Count (2)
    0x81, 0x02,                    //     Input (Data,Var,Abs)
    0x95, 0x06,                    //     Report Count (6)
    0x81, 0x03,                    //     Input (Const,Var,Abs)
    0x75, 0x08,                    //     Report Size (8)
    0x95, 0x01,                    //     Report Count (1)
    0x09, 0x51,                    //     Usage (Contact Identifier)
    0x25, 0x7F,                    //     Logical Maximum (127)
    0x81, 0x02,                    //     Input (Data,Var,Abs)
    0x05, 0x01,                    //     Usage Page (Generic Desktop)
    0x26, 0xFF, 0x7F,              //     Logical Maximum (32767)
    0x46, 0xFF, 0x7F,              //     Physical Maximum (32767)
    0x75, 0x10,                    //     Report Size (16)
    0x95, 0x02,                    //     Report Count (2)
    0x09, 0x30,                    //     Usage (X)
    0x09, 0x31,                    //     Usage (Y)
    0x81, 0x02,                    //     Input (Data,Var,Abs)
    0x05, 0x0D,                    //     Usage Page (Digitizers)
    0x09, 0x56,                    //     Usage (Scan Time)
    0x75, 0x10,                    //     Report Size (16)
    0x95, 0x01,                    //     Report Count (1)
    0x27, 0xFF, 0xFF, 0x00, 0x00,  //     Logical Maximum (65535)
    0x81, 0x02,                    //     Input (Data,Var,Abs)
    0xC0,                          //   End Collection
    0x09, 0x54,                    //   Usage (Contact Count)
    0x15, 0x00,                    //   Logical Minimum (0)
    0x25, 0x01,                    //   Logical Maximum (1)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x01,                    //   Report Count (1)
    0x81, 0x02,                    //   Input (Data,Var,Abs)
    0x85, REPORT_ID_MAX_CONTACTS,  //   Report ID (Max Contacts)
    0x09, 0x55,                    //   Usage (Contact Count Maximum)
    0x25, 0x01,                    //   Logical Maximum (1)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x01,                    //   Report Count (1)
    0xB1, 0x02,                    //   Feature (Data,Var,Abs)
    0xC0,                          // End Collection
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    (void)instance;
    return desc_hid_report;
}

enum {
    ITF_NUM_HID = 0,
    ITF_NUM_TOTAL,
};

#define EPNUM_HID 0x81
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_HID_DESC_LEN)

uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
    TUD_HID_DESCRIPTOR(ITF_NUM_HID, 4, HID_ITF_PROTOCOL_NONE,
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
    STRID_HID,
};

char const *string_desc_arr[] = {
    (const char[]){0x09, 0x04},
    "Codex Pico",
    "Pico H Touch Phase2 HID",
    NULL,
    "Pico H HID Touchscreen",
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
