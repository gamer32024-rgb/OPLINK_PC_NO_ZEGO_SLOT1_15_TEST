cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico CACHE STRING "Target board")
set(PICO_SDK_PATH "${CMAKE_CURRENT_LIST_DIR}/../third_party/pico-sdk" CACHE PATH "Pico SDK path")

include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")

project(pico_h_touch_firmware C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

add_executable(pico_h_phase0_smoke
    src/phase0_smoke.c
)

target_link_libraries(pico_h_phase0_smoke
    pico_stdlib
)

pico_enable_stdio_usb(pico_h_phase0_smoke 1)
pico_enable_stdio_uart(pico_h_phase0_smoke 0)
pico_add_extra_outputs(pico_h_phase0_smoke)

add_executable(pico_h_phase1_cdc
    src/phase1/main.c
    src/phase1/usb_descriptors.c
)

target_include_directories(pico_h_phase1_cdc PRIVATE
    src/phase1
)

target_link_libraries(pico_h_phase1_cdc
    pico_stdlib
    tinyusb_device
    tinyusb_board
)

pico_enable_stdio_usb(pico_h_phase1_cdc 0)
pico_enable_stdio_uart(pico_h_phase1_cdc 0)
pico_add_extra_outputs(pico_h_phase1_cdc)

add_executable(pico_h_phase2_hid_touch
    src/phase2/main.c
    src/phase2/usb_descriptors.c
)

target_include_directories(pico_h_phase2_hid_touch PRIVATE
    src/phase2
)

target_link_libraries(pico_h_phase2_hid_touch
    pico_stdlib
    tinyusb_device
    tinyusb_board
)

pico_enable_stdio_usb(pico_h_phase2_hid_touch 0)
pico_enable_stdio_uart(pico_h_phase2_hid_touch 0)
pico_add_extra_outputs(pico_h_phase2_hid_touch)

add_executable(pico_h_p3
    src/phase3/main.c
    src/phase3/usb_descriptors.c
)

set_target_properties(pico_h_p3 PROPERTIES
    OUTPUT_NAME pico_h_phase3_composite_touch
)

target_include_directories(pico_h_p3 PRIVATE
    src/phase3
)

target_link_libraries(pico_h_p3
    pico_stdlib
    pico_bootrom
    tinyusb_device
    tinyusb_board
)

pico_enable_stdio_usb(pico_h_p3 0)
pico_enable_stdio_uart(pico_h_p3 0)
pico_add_extra_outputs(pico_h_p3)
