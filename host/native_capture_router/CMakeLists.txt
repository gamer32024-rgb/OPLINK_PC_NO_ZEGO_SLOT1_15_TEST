cmake_minimum_required(VERSION 3.24)

project(OPLINKCaptureRouter LANGUAGES CXX)

add_executable(oplink_capture_router
    src/main.cpp
)

target_compile_features(oplink_capture_router PRIVATE cxx_std_20)
target_compile_definitions(oplink_capture_router PRIVATE
    NOMINMAX
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    _WIN32_WINNT=0x0A00
    WINVER=0x0A00
)

if(MSVC)
    target_compile_options(oplink_capture_router PRIVATE
        /W4
        /EHsc
        /permissive-
        /utf-8
        /Zc:__cplusplus
    )
endif()

target_link_libraries(oplink_capture_router PRIVATE
    d3d11
    dwmapi
    dxgi
    runtimeobject
    user32
    windowsapp
)
