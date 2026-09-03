#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <iostream>
#include <string>

using bind_fn = int(WSAAPI *)(SOCKET, const sockaddr *, int);

int wmain(int argc, wchar_t **argv) {
    bool dynamic_bind = argc == 2 && std::wstring(argv[1]) == L"--dynamic";

    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
        return 2;
    }

    SOCKET socket_handle = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (socket_handle == INVALID_SOCKET) {
        WSACleanup();
        return 3;
    }

    int bind_result = SOCKET_ERROR;
    if (dynamic_bind) {
        HMODULE ws2 = GetModuleHandleW(L"ws2_32.dll");
        bind_fn bind_call = ws2 ? reinterpret_cast<bind_fn>(GetProcAddress(ws2, "bind")) : nullptr;
        if (!bind_call) {
            closesocket(socket_handle);
            WSACleanup();
            return 4;
        }
        sockaddr_in requested{};
        requested.sin_family = AF_INET;
        requested.sin_addr.s_addr = INADDR_ANY;
        requested.sin_port = 0;
        bind_result = bind_call(socket_handle, reinterpret_cast<const sockaddr *>(&requested), sizeof(requested));
    } else {
        sockaddr_in requested{};
        requested.sin_family = AF_INET;
        requested.sin_addr.s_addr = INADDR_ANY;
        requested.sin_port = 0;
        bind_result = bind(socket_handle, reinterpret_cast<const sockaddr *>(&requested), sizeof(requested));
    }
    if (bind_result == SOCKET_ERROR) {
        std::wcerr << L"bind failed: " << WSAGetLastError() << L"\n";
        closesocket(socket_handle);
        WSACleanup();
        return 5;
    }

    sockaddr_in actual{};
    int actual_length = sizeof(actual);
    if (getsockname(socket_handle, reinterpret_cast<sockaddr *>(&actual), &actual_length) == SOCKET_ERROR) {
        closesocket(socket_handle);
        WSACleanup();
        return 6;
    }

    wchar_t address[64]{};
    InetNtopW(AF_INET, &actual.sin_addr, address, static_cast<DWORD>(std::size(address)));
    std::wcout << address << L"\n";

    closesocket(socket_handle);
    WSACleanup();
    return 0;
}
