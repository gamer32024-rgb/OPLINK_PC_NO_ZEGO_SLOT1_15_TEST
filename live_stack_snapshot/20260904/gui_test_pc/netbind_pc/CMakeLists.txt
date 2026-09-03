cmake_minimum_required(VERSION 3.20)

project(GuiTestPcNetBind LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(GuiTestNetBindLauncher WIN32 GuiTestNetBindLauncher.cpp)
target_compile_definitions(GuiTestNetBindLauncher PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
if(MSVC)
    target_link_options(GuiTestNetBindLauncher PRIVATE "/SUBSYSTEM:WINDOWS" "/ENTRY:wmainCRTStartup")
endif()

add_executable(FileRedirectSmoke FileRedirectSmoke.cpp)
target_compile_definitions(FileRedirectSmoke PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)

add_executable(NetBindSmoke NetBindSmoke.cpp)
target_compile_definitions(NetBindSmoke PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(NetBindSmoke PRIVATE Ws2_32)

add_library(LateLoadFileWriter SHARED LateLoadFileWriter.cpp)
target_compile_definitions(LateLoadFileWriter PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)

add_library(GuiTestNetBindHook64 SHARED GuiTestNetBindHook.cpp)
target_compile_definitions(GuiTestNetBindHook64 PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(GuiTestNetBindHook64 PRIVATE Ws2_32 Psapi)
