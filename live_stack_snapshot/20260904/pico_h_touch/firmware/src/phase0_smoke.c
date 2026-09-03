#include <stdio.h>

#include "pico/stdlib.h"
#include "pico/stdio_usb.h"

#ifndef PICO_DEFAULT_LED_PIN
#error "PICO_DEFAULT_LED_PIN is not defined. Build with -DPICO_BOARD=pico for Pico/Pico H."
#endif

int main(void) {
    stdio_init_all();

    const uint led_pin = PICO_DEFAULT_LED_PIN;
    gpio_init(led_pin);
    gpio_set_dir(led_pin, GPIO_OUT);

    uint32_t seq = 0;

    while (true) {
        gpio_put(led_pin, 1);
        sleep_ms(250);
        gpio_put(led_pin, 0);
        sleep_ms(750);

        if (stdio_usb_connected()) {
            printf("PICO_H_PHASE0_SMOKE seq=%lu board=pico_h target=rp2040\n",
                   (unsigned long)seq++);
        }
    }
}
