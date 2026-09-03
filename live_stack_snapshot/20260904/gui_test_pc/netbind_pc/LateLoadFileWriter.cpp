#include <windows.h>

extern "C" __declspec(dllexport) int __stdcall WriteLateFile(const wchar_t *path) {
    HANDLE file = CreateFileW(
        path,
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return 12;
    }

    const char data[] = "late redirected\n";
    DWORD written = 0;
    BOOL ok = WriteFile(file, data, static_cast<DWORD>(sizeof(data) - 1), &written, nullptr);
    CloseHandle(file);
    return ok ? 0 : 13;
}
