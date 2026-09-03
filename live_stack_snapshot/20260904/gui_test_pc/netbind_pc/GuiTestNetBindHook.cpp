#include <winsock2.h>
#include <mswsock.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <psapi.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cwctype>
#include <cstdint>
#include <string>
#include <vector>

namespace {

using connect_fn = int(WSAAPI *)(SOCKET, const sockaddr *, int);
using bind_fn = int(WSAAPI *)(SOCKET, const sockaddr *, int);
using WSAConnect_fn = int(WSAAPI *)(SOCKET, const sockaddr *, int, LPWSABUF, LPWSABUF, LPQOS, LPQOS);
using WSAIoctl_fn = int(WSAAPI *)(SOCKET, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPDWORD, LPWSAOVERLAPPED, LPWSAOVERLAPPED_COMPLETION_ROUTINE);
using sendto_fn = int(WSAAPI *)(SOCKET, const char *, int, int, const sockaddr *, int);
using WSASendTo_fn = int(WSAAPI *)(SOCKET, LPWSABUF, DWORD, LPDWORD, DWORD, const sockaddr *, int, LPWSAOVERLAPPED, LPWSAOVERLAPPED_COMPLETION_ROUTINE);
using CreateFileA_fn = HANDLE(WINAPI *)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
using CreateFileW_fn = HANDLE(WINAPI *)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
using DeleteFileA_fn = BOOL(WINAPI *)(LPCSTR);
using DeleteFileW_fn = BOOL(WINAPI *)(LPCWSTR);
using GetFileAttributesA_fn = DWORD(WINAPI *)(LPCSTR);
using GetFileAttributesW_fn = DWORD(WINAPI *)(LPCWSTR);
using GetFileAttributesExA_fn = BOOL(WINAPI *)(LPCSTR, GET_FILEEX_INFO_LEVELS, LPVOID);
using GetFileAttributesExW_fn = BOOL(WINAPI *)(LPCWSTR, GET_FILEEX_INFO_LEVELS, LPVOID);
using SetFileAttributesA_fn = BOOL(WINAPI *)(LPCSTR, DWORD);
using SetFileAttributesW_fn = BOOL(WINAPI *)(LPCWSTR, DWORD);
using CreateDirectoryA_fn = BOOL(WINAPI *)(LPCSTR, LPSECURITY_ATTRIBUTES);
using CreateDirectoryW_fn = BOOL(WINAPI *)(LPCWSTR, LPSECURITY_ATTRIBUTES);
using RemoveDirectoryA_fn = BOOL(WINAPI *)(LPCSTR);
using RemoveDirectoryW_fn = BOOL(WINAPI *)(LPCWSTR);
using MoveFileA_fn = BOOL(WINAPI *)(LPCSTR, LPCSTR);
using MoveFileW_fn = BOOL(WINAPI *)(LPCWSTR, LPCWSTR);
using MoveFileExA_fn = BOOL(WINAPI *)(LPCSTR, LPCSTR, DWORD);
using MoveFileExW_fn = BOOL(WINAPI *)(LPCWSTR, LPCWSTR, DWORD);
using CopyFileA_fn = BOOL(WINAPI *)(LPCSTR, LPCSTR, BOOL);
using CopyFileW_fn = BOOL(WINAPI *)(LPCWSTR, LPCWSTR, BOOL);
using GetPrivateProfileStringA_fn = DWORD(WINAPI *)(LPCSTR, LPCSTR, LPCSTR, LPSTR, DWORD, LPCSTR);
using GetPrivateProfileStringW_fn = DWORD(WINAPI *)(LPCWSTR, LPCWSTR, LPCWSTR, LPWSTR, DWORD, LPCWSTR);
using GetPrivateProfileIntA_fn = UINT(WINAPI *)(LPCSTR, LPCSTR, INT, LPCSTR);
using GetPrivateProfileIntW_fn = UINT(WINAPI *)(LPCWSTR, LPCWSTR, INT, LPCWSTR);
using WritePrivateProfileStringA_fn = BOOL(WINAPI *)(LPCSTR, LPCSTR, LPCSTR, LPCSTR);
using WritePrivateProfileStringW_fn = BOOL(WINAPI *)(LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR);
using FindFirstFileA_fn = HANDLE(WINAPI *)(LPCSTR, LPWIN32_FIND_DATAA);
using FindFirstFileW_fn = HANDLE(WINAPI *)(LPCWSTR, LPWIN32_FIND_DATAW);
using FindFirstFileExA_fn = HANDLE(WINAPI *)(LPCSTR, FINDEX_INFO_LEVELS, LPVOID, FINDEX_SEARCH_OPS, LPVOID, DWORD);
using FindFirstFileExW_fn = HANDLE(WINAPI *)(LPCWSTR, FINDEX_INFO_LEVELS, LPVOID, FINDEX_SEARCH_OPS, LPVOID, DWORD);
using LoadLibraryA_fn = HMODULE(WINAPI *)(LPCSTR);
using LoadLibraryW_fn = HMODULE(WINAPI *)(LPCWSTR);
using LoadLibraryExA_fn = HMODULE(WINAPI *)(LPCSTR, HANDLE, DWORD);
using LoadLibraryExW_fn = HMODULE(WINAPI *)(LPCWSTR, HANDLE, DWORD);
using GetProcAddress_fn = FARPROC(WINAPI *)(HMODULE, LPCSTR);
using NtStatus = LONG;

struct NtUnicodeString {
    USHORT Length;
    USHORT MaximumLength;
    PWSTR Buffer;
};

struct NtObjectAttributes {
    ULONG Length;
    HANDLE RootDirectory;
    NtUnicodeString *ObjectName;
    ULONG Attributes;
    PVOID SecurityDescriptor;
    PVOID SecurityQualityOfService;
};

using NtCreateFile_fn = NtStatus(NTAPI *)(PHANDLE, ACCESS_MASK, NtObjectAttributes *, PVOID, PLARGE_INTEGER, ULONG, ULONG, ULONG, ULONG, PVOID, ULONG);
using NtOpenFile_fn = NtStatus(NTAPI *)(PHANDLE, ACCESS_MASK, NtObjectAttributes *, PVOID, ULONG, ULONG);
using RegCreateKeyExW_fn = LSTATUS(WINAPI *)(HKEY, LPCWSTR, DWORD, LPWSTR, DWORD, REGSAM, const LPSECURITY_ATTRIBUTES, PHKEY, LPDWORD);
using RegCreateKeyExA_fn = LSTATUS(WINAPI *)(HKEY, LPCSTR, DWORD, LPSTR, DWORD, REGSAM, const LPSECURITY_ATTRIBUTES, PHKEY, LPDWORD);
using RegCreateKeyW_fn = LSTATUS(WINAPI *)(HKEY, LPCWSTR, PHKEY);
using RegCreateKeyA_fn = LSTATUS(WINAPI *)(HKEY, LPCSTR, PHKEY);
using RegOpenKeyExW_fn = LSTATUS(WINAPI *)(HKEY, LPCWSTR, DWORD, REGSAM, PHKEY);
using RegOpenKeyExA_fn = LSTATUS(WINAPI *)(HKEY, LPCSTR, DWORD, REGSAM, PHKEY);
using RegOpenKeyW_fn = LSTATUS(WINAPI *)(HKEY, LPCWSTR, PHKEY);
using RegOpenKeyA_fn = LSTATUS(WINAPI *)(HKEY, LPCSTR, PHKEY);
using RegDeleteKeyW_fn = LSTATUS(WINAPI *)(HKEY, LPCWSTR);
using RegDeleteKeyA_fn = LSTATUS(WINAPI *)(HKEY, LPCSTR);

connect_fn real_connect = nullptr;
bind_fn real_bind = nullptr;
WSAConnect_fn real_WSAConnect = nullptr;
WSAIoctl_fn real_WSAIoctl = nullptr;
LPFN_CONNECTEX real_ConnectEx = nullptr;
sendto_fn real_sendto = nullptr;
WSASendTo_fn real_WSASendTo = nullptr;
CreateFileA_fn real_CreateFileA = nullptr;
CreateFileW_fn real_CreateFileW = nullptr;
DeleteFileA_fn real_DeleteFileA = nullptr;
DeleteFileW_fn real_DeleteFileW = nullptr;
GetFileAttributesA_fn real_GetFileAttributesA = nullptr;
GetFileAttributesW_fn real_GetFileAttributesW = nullptr;
GetFileAttributesExA_fn real_GetFileAttributesExA = nullptr;
GetFileAttributesExW_fn real_GetFileAttributesExW = nullptr;
SetFileAttributesA_fn real_SetFileAttributesA = nullptr;
SetFileAttributesW_fn real_SetFileAttributesW = nullptr;
CreateDirectoryA_fn real_CreateDirectoryA = nullptr;
CreateDirectoryW_fn real_CreateDirectoryW = nullptr;
RemoveDirectoryA_fn real_RemoveDirectoryA = nullptr;
RemoveDirectoryW_fn real_RemoveDirectoryW = nullptr;
MoveFileA_fn real_MoveFileA = nullptr;
MoveFileW_fn real_MoveFileW = nullptr;
MoveFileExA_fn real_MoveFileExA = nullptr;
MoveFileExW_fn real_MoveFileExW = nullptr;
CopyFileA_fn real_CopyFileA = nullptr;
CopyFileW_fn real_CopyFileW = nullptr;
GetPrivateProfileStringA_fn real_GetPrivateProfileStringA = nullptr;
GetPrivateProfileStringW_fn real_GetPrivateProfileStringW = nullptr;
GetPrivateProfileIntA_fn real_GetPrivateProfileIntA = nullptr;
GetPrivateProfileIntW_fn real_GetPrivateProfileIntW = nullptr;
WritePrivateProfileStringA_fn real_WritePrivateProfileStringA = nullptr;
WritePrivateProfileStringW_fn real_WritePrivateProfileStringW = nullptr;
FindFirstFileA_fn real_FindFirstFileA = nullptr;
FindFirstFileW_fn real_FindFirstFileW = nullptr;
FindFirstFileExA_fn real_FindFirstFileExA = nullptr;
FindFirstFileExW_fn real_FindFirstFileExW = nullptr;
LoadLibraryA_fn real_LoadLibraryA = nullptr;
LoadLibraryW_fn real_LoadLibraryW = nullptr;
LoadLibraryExA_fn real_LoadLibraryExA = nullptr;
LoadLibraryExW_fn real_LoadLibraryExW = nullptr;
GetProcAddress_fn real_GetProcAddress = nullptr;
NtCreateFile_fn real_NtCreateFile = nullptr;
NtOpenFile_fn real_NtOpenFile = nullptr;
RegCreateKeyExW_fn real_RegCreateKeyExW = nullptr;
RegCreateKeyExA_fn real_RegCreateKeyExA = nullptr;
RegCreateKeyW_fn real_RegCreateKeyW = nullptr;
RegCreateKeyA_fn real_RegCreateKeyA = nullptr;
RegOpenKeyExW_fn real_RegOpenKeyExW = nullptr;
RegOpenKeyExA_fn real_RegOpenKeyExA = nullptr;
RegOpenKeyW_fn real_RegOpenKeyW = nullptr;
RegOpenKeyA_fn real_RegOpenKeyA = nullptr;
RegDeleteKeyW_fn real_RegDeleteKeyW = nullptr;
RegDeleteKeyA_fn real_RegDeleteKeyA = nullptr;

HMODULE self_module = nullptr;
IN_ADDR bind_addr{};
bool bind_enabled = false;
bool file_redirect_enabled = false;
bool registry_redirect_enabled = false;
std::wstring log_path;

struct FileRedirectRule {
    std::wstring from;
    std::wstring to;
    std::wstring from_cmp;
};

std::vector<FileRedirectRule> file_redirect_rules;
std::wstring redirect_from;
std::wstring redirect_to;
std::wstring redirect_from_cmp;
std::wstring process_dir_cmp;
std::wstring registry_from = L"Software\\CrossGate\\StarCG";
std::wstring registry_to;
std::wstring registry_from_cmp;
std::wstring registry_leaf_from = L"StarCG";
std::wstring registry_leaf_to;
std::wstring ready_event_name;
CRITICAL_SECTION log_lock;
std::atomic<long> bind_attempts{0};
std::atomic<long> file_redirects{0};
std::atomic<long> registry_redirects{0};
std::atomic<long> patch_count{0};
std::atomic<long> late_load_count{0};

bool safe_equals_ignore_case(const char *left, const char *right);
void resolve_real_functions();

void log_line(const std::wstring &message) {
    if (log_path.empty()) {
        return;
    }
    EnterCriticalSection(&log_lock);
    HANDLE file = CreateFileW(log_path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        SYSTEMTIME st{};
        GetLocalTime(&st);
        wchar_t prefix[64]{};
        swprintf_s(prefix, L"%04u-%02u-%02u %02u:%02u:%02u.%03u pid=%lu ",
                   st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, GetCurrentProcessId());
        std::wstring line = std::wstring(prefix) + message + L"\r\n";
        DWORD bytes = 0;
        WriteFile(file, line.c_str(), static_cast<DWORD>(line.size() * sizeof(wchar_t)), &bytes, nullptr);
        CloseHandle(file);
    }
    LeaveCriticalSection(&log_lock);
}

std::wstring strip_extended_prefix(std::wstring value) {
    if (value.rfind(LR"(\\?\)", 0) == 0) {
        return value.substr(4);
    }
    return value;
}

std::wstring normalize_path_for_compare(std::wstring value) {
    value = strip_extended_prefix(value);
    std::replace(value.begin(), value.end(), L'/', L'\\');
    while (value.size() > 3 && value.back() == L'\\') {
        value.pop_back();
    }
    std::transform(value.begin(), value.end(), value.begin(), [](wchar_t ch) {
        return static_cast<wchar_t>(std::towlower(ch));
    });
    return value;
}

std::wstring get_env_wstring(const wchar_t *name) {
    DWORD needed = GetEnvironmentVariableW(name, nullptr, 0);
    if (needed == 0) {
        return L"";
    }
    std::wstring value(static_cast<size_t>(needed), L'\0');
    DWORD written = GetEnvironmentVariableW(name, value.data(), needed);
    if (written == 0 || written >= needed) {
        return L"";
    }
    value.resize(written);
    return value;
}

void add_file_redirect_rule(std::wstring from, std::wstring to) {
    from = strip_extended_prefix(from);
    to = strip_extended_prefix(to);
    std::replace(from.begin(), from.end(), L'/', L'\\');
    std::replace(to.begin(), to.end(), L'/', L'\\');
    while (from.size() > 3 && from.back() == L'\\') {
        from.pop_back();
    }
    while (to.size() > 3 && to.back() == L'\\') {
        to.pop_back();
    }
    if (from.empty() || to.empty()) {
        return;
    }

    std::wstring from_cmp = normalize_path_for_compare(from);
    for (const auto &rule : file_redirect_rules) {
        if (rule.from_cmp == from_cmp && normalize_path_for_compare(rule.to) == normalize_path_for_compare(to)) {
            return;
        }
    }

    file_redirect_rules.push_back(FileRedirectRule{from, to, from_cmp});
    if (redirect_from.empty()) {
        redirect_from = from;
        redirect_to = to;
        redirect_from_cmp = from_cmp;
    }
    file_redirect_enabled = true;
}

bool path_has_prefix_boundary(const std::wstring &path, const std::wstring &prefix) {
    if (path.size() < prefix.size() || path.compare(0, prefix.size(), prefix) != 0) {
        return false;
    }
    return path.size() == prefix.size() || path[prefix.size()] == L'\\';
}

bool path_contains_account_file(const std::wstring &path) {
    std::wstring cmp = normalize_path_for_compare(path);
    if (cmp.size() >= 8 && (cmp.rfind(L"\\account") == cmp.size() - 8 || cmp == L"account")) {
        return true;
    }
    return cmp.size() >= 11 && cmp.rfind(L"\\config.ini") == cmp.size() - 11;
}

bool redirect_path(LPCWSTR input, std::wstring &output) {
    if (!file_redirect_enabled || !input || !*input) {
        return false;
    }

    std::wstring original = strip_extended_prefix(input);
    std::replace(original.begin(), original.end(), L'/', L'\\');
    std::wstring cmp = normalize_path_for_compare(original);
    for (const auto &rule : file_redirect_rules) {
        if (!path_has_prefix_boundary(cmp, rule.from_cmp)) {
            continue;
        }

        std::wstring suffix = original.substr(rule.from.size());
        output = rule.to + suffix;
        long count = ++file_redirects;
        if (count <= 120 || path_contains_account_file(output)) {
            log_line(L"redirect file path to " + output);
        }
        return true;
    }
    return false;
}

bool widen_ansi(LPCSTR input, std::wstring &output) {
    if (!input) {
        return false;
    }
    int needed = MultiByteToWideChar(CP_ACP, 0, input, -1, nullptr, 0);
    if (needed <= 0) {
        return false;
    }
    output.assign(static_cast<size_t>(needed), L'\0');
    int written = MultiByteToWideChar(CP_ACP, 0, input, -1, output.data(), needed);
    if (written <= 0) {
        output.clear();
        return false;
    }
    if (!output.empty() && output.back() == L'\0') {
        output.pop_back();
    }
    return true;
}

bool narrow_ansi(const std::wstring &input, std::string &output) {
    int needed = WideCharToMultiByte(CP_ACP, 0, input.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (needed <= 0) {
        return false;
    }
    output.assign(static_cast<size_t>(needed), '\0');
    int written = WideCharToMultiByte(CP_ACP, 0, input.c_str(), -1, output.data(), needed, nullptr, nullptr);
    if (written <= 0) {
        output.clear();
        return false;
    }
    if (!output.empty() && output.back() == '\0') {
        output.pop_back();
    }
    return true;
}

bool redirect_path_a(LPCSTR input, std::string &output) {
    std::wstring wide_input;
    std::wstring wide_redirected;
    if (!widen_ansi(input, wide_input) || !redirect_path(wide_input.c_str(), wide_redirected)) {
        return false;
    }
    return narrow_ansi(wide_redirected, output);
}

bool redirect_nt_object_path(NtObjectAttributes *attributes, NtObjectAttributes &redirected_attributes, NtUnicodeString &redirected_name, std::wstring &redirected_storage) {
    if (!attributes || !attributes->ObjectName || !attributes->ObjectName->Buffer || attributes->ObjectName->Length == 0) {
        return false;
    }
    if (attributes->RootDirectory) {
        return false;
    }

    std::wstring original(attributes->ObjectName->Buffer, attributes->ObjectName->Length / sizeof(wchar_t));
    std::wstring dos_path = original;
    std::wstring nt_prefix;

    if (dos_path.rfind(LR"(\??\)", 0) == 0) {
        nt_prefix = LR"(\??\)";
        dos_path = dos_path.substr(4);
    } else if (dos_path.rfind(LR"(\\?\)", 0) == 0) {
        nt_prefix = LR"(\??\)";
        dos_path = dos_path.substr(4);
    }

    std::wstring redirected_dos;
    if (!redirect_path(dos_path.c_str(), redirected_dos)) {
        return false;
    }

    redirected_storage = nt_prefix.empty() ? redirected_dos : nt_prefix + redirected_dos;
    redirected_name.Length = static_cast<USHORT>(redirected_storage.size() * sizeof(wchar_t));
    redirected_name.MaximumLength = static_cast<USHORT>((redirected_storage.size() + 1) * sizeof(wchar_t));
    redirected_name.Buffer = redirected_storage.data();
    redirected_attributes = *attributes;
    redirected_attributes.ObjectName = &redirected_name;
    return true;
}

std::wstring path_leaf(const std::wstring &path) {
    size_t pos = path.find_last_of(L"\\/");
    if (pos == std::wstring::npos) {
        return path;
    }
    return path.substr(pos + 1);
}

std::wstring normalize_registry_path(std::wstring value) {
    std::replace(value.begin(), value.end(), L'/', L'\\');
    while (!value.empty() && value.front() == L'\\') {
        value.erase(value.begin());
    }
    while (!value.empty() && value.back() == L'\\') {
        value.pop_back();
    }
    std::transform(value.begin(), value.end(), value.begin(), [](wchar_t ch) {
        return static_cast<wchar_t>(std::towlower(ch));
    });
    return value;
}

bool registry_has_prefix_boundary(const std::wstring &path, const std::wstring &prefix) {
    if (path.size() < prefix.size() || path.compare(0, prefix.size(), prefix) != 0) {
        return false;
    }
    return path.size() == prefix.size() || path[prefix.size()] == L'\\';
}

bool redirect_registry_path(LPCWSTR input, std::wstring &output) {
    if (!registry_redirect_enabled || !input || !*input) {
        return false;
    }

    std::wstring original = input;
    std::replace(original.begin(), original.end(), L'/', L'\\');
    while (!original.empty() && original.front() == L'\\') {
        original.erase(original.begin());
    }
    while (!original.empty() && original.back() == L'\\') {
        original.pop_back();
    }

    std::wstring cmp = normalize_registry_path(original);
    std::wstring crossgate_from = L"CrossGate\\" + registry_leaf_from;
    std::wstring crossgate_to = L"CrossGate\\" + registry_leaf_to;
    std::wstring crossgate_from_cmp = normalize_registry_path(crossgate_from);
    if (registry_has_prefix_boundary(cmp, registry_from_cmp)) {
        output = registry_to + original.substr(registry_from.size());
    } else if (cmp == normalize_registry_path(registry_leaf_from)) {
        output = registry_leaf_to;
    } else if (!registry_leaf_from.empty() && !registry_leaf_to.empty() && registry_has_prefix_boundary(cmp, crossgate_from_cmp)) {
        output = crossgate_to + original.substr(crossgate_from.size());
    } else {
        return false;
    }

    long count = ++registry_redirects;
    if (count <= 80) {
        log_line(L"redirect registry key to " + output);
    }
    return true;
}

bool redirect_registry_path_a(LPCSTR input, std::string &output) {
    std::wstring wide_input;
    std::wstring wide_redirected;
    if (!widen_ansi(input, wide_input) || !redirect_registry_path(wide_input.c_str(), wide_redirected)) {
        return false;
    }
    return narrow_ansi(wide_redirected, output);
}

bool is_loopback_destination(const sockaddr *addr) {
    if (!addr) {
        return false;
    }
    if (addr->sa_family == AF_INET) {
        auto *in = reinterpret_cast<const sockaddr_in *>(addr);
        uint32_t host = ntohl(in->sin_addr.s_addr);
        return ((host >> 24) == 127);
    }
    if (addr->sa_family == AF_INET6) {
        auto *in6 = reinterpret_cast<const sockaddr_in6 *>(addr);
        static const IN6_ADDR loopback = IN6ADDR_LOOPBACK_INIT;
        return memcmp(&in6->sin6_addr, &loopback, sizeof(IN6_ADDR)) == 0;
    }
    return false;
}

void log_bind_event(const std::wstring &message) {
    long attempt = ++bind_attempts;
    if (attempt <= 100) {
        log_line(message);
    }
}

bool bind_socket_if_needed(SOCKET socket, const sockaddr *dest) {
    if (!bind_enabled || socket == INVALID_SOCKET || !dest || dest->sa_family != AF_INET) {
        return true;
    }
    if (is_loopback_destination(dest)) {
        return true;
    }

    sockaddr_storage local{};
    int local_len = sizeof(local);
    if (getsockname(socket, reinterpret_cast<sockaddr *>(&local), &local_len) == 0) {
        if (local.ss_family == AF_INET) {
            auto *local4 = reinterpret_cast<sockaddr_in *>(&local);
            if (local4->sin_addr.s_addr == bind_addr.s_addr) {
                return true;
            }
            if (local4->sin_addr.s_addr != INADDR_ANY || local4->sin_port != 0) {
                wchar_t current_ip[64]{};
                InetNtopW(AF_INET, &local4->sin_addr, current_ip, 64);
                log_bind_event(L"blocked socket already bound to wrong address " + std::wstring(current_ip));
                WSASetLastError(WSAEADDRNOTAVAIL);
                return false;
            }
        }
    }

    if (!real_bind) {
        resolve_real_functions();
    }
    if (!real_bind) {
        log_bind_event(L"blocked socket because real bind is unavailable");
        WSASetLastError(WSAEFAULT);
        return false;
    }

    sockaddr_in bind_to{};
    bind_to.sin_family = AF_INET;
    bind_to.sin_port = 0;
    bind_to.sin_addr = bind_addr;
    if (real_bind(socket, reinterpret_cast<sockaddr *>(&bind_to), sizeof(bind_to)) == SOCKET_ERROR) {
        int err = WSAGetLastError();
        log_bind_event(L"blocked socket after bind failed err=" + std::to_wstring(err));
        WSASetLastError(err);
        return false;
    } else {
        wchar_t ip[64]{};
        InetNtopW(AF_INET, &bind_addr, ip, 64);
        log_bind_event(L"bound socket to " + std::wstring(ip));
    }
    return true;
}

void resolve_real_file_functions() {
    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    if (kernel32) {
        real_CreateFileA = reinterpret_cast<CreateFileA_fn>(GetProcAddress(kernel32, "CreateFileA"));
        real_CreateFileW = reinterpret_cast<CreateFileW_fn>(GetProcAddress(kernel32, "CreateFileW"));
        real_DeleteFileA = reinterpret_cast<DeleteFileA_fn>(GetProcAddress(kernel32, "DeleteFileA"));
        real_DeleteFileW = reinterpret_cast<DeleteFileW_fn>(GetProcAddress(kernel32, "DeleteFileW"));
        real_GetFileAttributesA = reinterpret_cast<GetFileAttributesA_fn>(GetProcAddress(kernel32, "GetFileAttributesA"));
        real_GetFileAttributesW = reinterpret_cast<GetFileAttributesW_fn>(GetProcAddress(kernel32, "GetFileAttributesW"));
        real_GetFileAttributesExA = reinterpret_cast<GetFileAttributesExA_fn>(GetProcAddress(kernel32, "GetFileAttributesExA"));
        real_GetFileAttributesExW = reinterpret_cast<GetFileAttributesExW_fn>(GetProcAddress(kernel32, "GetFileAttributesExW"));
        real_SetFileAttributesA = reinterpret_cast<SetFileAttributesA_fn>(GetProcAddress(kernel32, "SetFileAttributesA"));
        real_SetFileAttributesW = reinterpret_cast<SetFileAttributesW_fn>(GetProcAddress(kernel32, "SetFileAttributesW"));
        real_CreateDirectoryA = reinterpret_cast<CreateDirectoryA_fn>(GetProcAddress(kernel32, "CreateDirectoryA"));
        real_CreateDirectoryW = reinterpret_cast<CreateDirectoryW_fn>(GetProcAddress(kernel32, "CreateDirectoryW"));
        real_RemoveDirectoryA = reinterpret_cast<RemoveDirectoryA_fn>(GetProcAddress(kernel32, "RemoveDirectoryA"));
        real_RemoveDirectoryW = reinterpret_cast<RemoveDirectoryW_fn>(GetProcAddress(kernel32, "RemoveDirectoryW"));
        real_MoveFileA = reinterpret_cast<MoveFileA_fn>(GetProcAddress(kernel32, "MoveFileA"));
        real_MoveFileW = reinterpret_cast<MoveFileW_fn>(GetProcAddress(kernel32, "MoveFileW"));
        real_MoveFileExA = reinterpret_cast<MoveFileExA_fn>(GetProcAddress(kernel32, "MoveFileExA"));
        real_MoveFileExW = reinterpret_cast<MoveFileExW_fn>(GetProcAddress(kernel32, "MoveFileExW"));
        real_CopyFileA = reinterpret_cast<CopyFileA_fn>(GetProcAddress(kernel32, "CopyFileA"));
        real_CopyFileW = reinterpret_cast<CopyFileW_fn>(GetProcAddress(kernel32, "CopyFileW"));
        real_GetPrivateProfileStringA = reinterpret_cast<GetPrivateProfileStringA_fn>(GetProcAddress(kernel32, "GetPrivateProfileStringA"));
        real_GetPrivateProfileStringW = reinterpret_cast<GetPrivateProfileStringW_fn>(GetProcAddress(kernel32, "GetPrivateProfileStringW"));
        real_GetPrivateProfileIntA = reinterpret_cast<GetPrivateProfileIntA_fn>(GetProcAddress(kernel32, "GetPrivateProfileIntA"));
        real_GetPrivateProfileIntW = reinterpret_cast<GetPrivateProfileIntW_fn>(GetProcAddress(kernel32, "GetPrivateProfileIntW"));
        real_WritePrivateProfileStringA = reinterpret_cast<WritePrivateProfileStringA_fn>(GetProcAddress(kernel32, "WritePrivateProfileStringA"));
        real_WritePrivateProfileStringW = reinterpret_cast<WritePrivateProfileStringW_fn>(GetProcAddress(kernel32, "WritePrivateProfileStringW"));
        real_FindFirstFileA = reinterpret_cast<FindFirstFileA_fn>(GetProcAddress(kernel32, "FindFirstFileA"));
        real_FindFirstFileW = reinterpret_cast<FindFirstFileW_fn>(GetProcAddress(kernel32, "FindFirstFileW"));
        real_FindFirstFileExA = reinterpret_cast<FindFirstFileExA_fn>(GetProcAddress(kernel32, "FindFirstFileExA"));
        real_FindFirstFileExW = reinterpret_cast<FindFirstFileExW_fn>(GetProcAddress(kernel32, "FindFirstFileExW"));
        real_LoadLibraryA = reinterpret_cast<LoadLibraryA_fn>(GetProcAddress(kernel32, "LoadLibraryA"));
        real_LoadLibraryW = reinterpret_cast<LoadLibraryW_fn>(GetProcAddress(kernel32, "LoadLibraryW"));
        real_LoadLibraryExA = reinterpret_cast<LoadLibraryExA_fn>(GetProcAddress(kernel32, "LoadLibraryExA"));
        real_LoadLibraryExW = reinterpret_cast<LoadLibraryExW_fn>(GetProcAddress(kernel32, "LoadLibraryExW"));
        real_GetProcAddress = reinterpret_cast<GetProcAddress_fn>(GetProcAddress(kernel32, "GetProcAddress"));
    }

    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (ntdll) {
        real_NtCreateFile = reinterpret_cast<NtCreateFile_fn>(GetProcAddress(ntdll, "NtCreateFile"));
        real_NtOpenFile = reinterpret_cast<NtOpenFile_fn>(GetProcAddress(ntdll, "NtOpenFile"));
    }

    HMODULE advapi32 = GetModuleHandleW(L"advapi32.dll");
    if (!advapi32) {
        advapi32 = real_LoadLibraryW ? real_LoadLibraryW(L"advapi32.dll") : LoadLibraryW(L"advapi32.dll");
    }
    if (advapi32) {
        real_RegCreateKeyExW = reinterpret_cast<RegCreateKeyExW_fn>(GetProcAddress(advapi32, "RegCreateKeyExW"));
        real_RegCreateKeyExA = reinterpret_cast<RegCreateKeyExA_fn>(GetProcAddress(advapi32, "RegCreateKeyExA"));
        real_RegCreateKeyW = reinterpret_cast<RegCreateKeyW_fn>(GetProcAddress(advapi32, "RegCreateKeyW"));
        real_RegCreateKeyA = reinterpret_cast<RegCreateKeyA_fn>(GetProcAddress(advapi32, "RegCreateKeyA"));
        real_RegOpenKeyExW = reinterpret_cast<RegOpenKeyExW_fn>(GetProcAddress(advapi32, "RegOpenKeyExW"));
        real_RegOpenKeyExA = reinterpret_cast<RegOpenKeyExA_fn>(GetProcAddress(advapi32, "RegOpenKeyExA"));
        real_RegOpenKeyW = reinterpret_cast<RegOpenKeyW_fn>(GetProcAddress(advapi32, "RegOpenKeyW"));
        real_RegOpenKeyA = reinterpret_cast<RegOpenKeyA_fn>(GetProcAddress(advapi32, "RegOpenKeyA"));
        real_RegDeleteKeyW = reinterpret_cast<RegDeleteKeyW_fn>(GetProcAddress(advapi32, "RegDeleteKeyW"));
        real_RegDeleteKeyA = reinterpret_cast<RegDeleteKeyA_fn>(GetProcAddress(advapi32, "RegDeleteKeyA"));
    }
}

void resolve_real_functions() {
    resolve_real_file_functions();

    HMODULE ws2 = GetModuleHandleW(L"ws2_32.dll");
    if (!ws2) {
        ws2 = LoadLibraryW(L"ws2_32.dll");
    }
    if (!ws2) {
        return;
    }
    real_connect = reinterpret_cast<connect_fn>(GetProcAddress(ws2, "connect"));
    real_bind = reinterpret_cast<bind_fn>(GetProcAddress(ws2, "bind"));
    real_WSAConnect = reinterpret_cast<WSAConnect_fn>(GetProcAddress(ws2, "WSAConnect"));
    real_WSAIoctl = reinterpret_cast<WSAIoctl_fn>(GetProcAddress(ws2, "WSAIoctl"));
    real_sendto = reinterpret_cast<sendto_fn>(GetProcAddress(ws2, "sendto"));
    real_WSASendTo = reinterpret_cast<WSASendTo_fn>(GetProcAddress(ws2, "WSASendTo"));
}

void patch_module_iat_guarded(HMODULE module);
void patch_all_modules();
void patch_after_late_load(HMODULE module, const std::wstring &name);

extern "C" int WSAAPI hook_connect(SOCKET socket, const sockaddr *name, int namelen) {
    if (!real_connect) {
        resolve_real_functions();
    }
    if (!real_connect) {
        WSASetLastError(WSAEFAULT);
        return SOCKET_ERROR;
    }
    if (!bind_socket_if_needed(socket, name)) {
        return SOCKET_ERROR;
    }
    return real_connect(socket, name, namelen);
}

extern "C" int WSAAPI hook_bind(SOCKET socket, const sockaddr *name, int namelen) {
    if (!real_bind) {
        resolve_real_functions();
    }
    if (!real_bind) {
        WSASetLastError(WSAEFAULT);
        return SOCKET_ERROR;
    }
    if (!bind_enabled || !name || name->sa_family != AF_INET || namelen < static_cast<int>(sizeof(sockaddr_in))) {
        return real_bind(socket, name, namelen);
    }

    const auto *requested = reinterpret_cast<const sockaddr_in *>(name);
    if (is_loopback_destination(name) || requested->sin_addr.s_addr == bind_addr.s_addr) {
        return real_bind(socket, name, namelen);
    }
    if (requested->sin_addr.s_addr != INADDR_ANY) {
        wchar_t requested_ip[64]{};
        InetNtopW(AF_INET, &requested->sin_addr, requested_ip, 64);
        log_bind_event(L"blocked explicit bind to wrong address " + std::wstring(requested_ip));
        WSASetLastError(WSAEADDRNOTAVAIL);
        return SOCKET_ERROR;
    }

    sockaddr_in rewritten = *requested;
    rewritten.sin_addr = bind_addr;
    wchar_t ip[64]{};
    InetNtopW(AF_INET, &bind_addr, ip, 64);
    log_bind_event(L"rewrote wildcard bind to " + std::wstring(ip));
    return real_bind(socket, reinterpret_cast<const sockaddr *>(&rewritten), sizeof(rewritten));
}

extern "C" int WSAAPI hook_WSAConnect(SOCKET socket, const sockaddr *name, int namelen, LPWSABUF caller_data, LPWSABUF callee_data, LPQOS sqos, LPQOS gqos) {
    if (!real_WSAConnect) {
        resolve_real_functions();
    }
    if (!real_WSAConnect) {
        WSASetLastError(WSAEFAULT);
        return SOCKET_ERROR;
    }
    if (!bind_socket_if_needed(socket, name)) {
        return SOCKET_ERROR;
    }
    return real_WSAConnect(socket, name, namelen, caller_data, callee_data, sqos, gqos);
}

extern "C" BOOL PASCAL hook_ConnectEx(SOCKET socket, const sockaddr *name, int namelen, PVOID send_buffer, DWORD send_length, LPDWORD bytes_sent, LPOVERLAPPED overlapped) {
    if (!real_ConnectEx) {
        WSASetLastError(WSAEFAULT);
        return FALSE;
    }
    if (!bind_socket_if_needed(socket, name)) {
        return FALSE;
    }
    return real_ConnectEx(socket, name, namelen, send_buffer, send_length, bytes_sent, overlapped);
}

extern "C" int WSAAPI hook_WSAIoctl(SOCKET socket, DWORD code, LPVOID in_buffer, DWORD in_size, LPVOID out_buffer, DWORD out_size, LPDWORD bytes_returned, LPWSAOVERLAPPED overlapped, LPWSAOVERLAPPED_COMPLETION_ROUTINE completion) {
    if (!real_WSAIoctl) {
        resolve_real_functions();
    }
    if (!real_WSAIoctl) {
        WSASetLastError(WSAEFAULT);
        return SOCKET_ERROR;
    }

    int result = real_WSAIoctl(socket, code, in_buffer, in_size, out_buffer, out_size, bytes_returned, overlapped, completion);
    if (
        result == 0 &&
        bind_enabled &&
        code == SIO_GET_EXTENSION_FUNCTION_POINTER &&
        in_buffer &&
        in_size >= sizeof(GUID) &&
        out_buffer &&
        out_size >= sizeof(LPFN_CONNECTEX) &&
        IsEqualGUID(*reinterpret_cast<const GUID *>(in_buffer), WSAID_CONNECTEX)
    ) {
        auto *function = reinterpret_cast<LPFN_CONNECTEX *>(out_buffer);
        real_ConnectEx = *function;
        *function = &hook_ConnectEx;
        log_bind_event(L"redirected ConnectEx extension function");
    }
    return result;
}

extern "C" int WSAAPI hook_sendto(SOCKET socket, const char *buf, int len, int flags, const sockaddr *to, int tolen) {
    if (!real_sendto) {
        resolve_real_functions();
    }
    if (!real_sendto) {
        WSASetLastError(WSAEFAULT);
        return SOCKET_ERROR;
    }
    if (!bind_socket_if_needed(socket, to)) {
        return SOCKET_ERROR;
    }
    return real_sendto(socket, buf, len, flags, to, tolen);
}

extern "C" int WSAAPI hook_WSASendTo(SOCKET socket, LPWSABUF buffers, DWORD buffer_count, LPDWORD bytes_sent, DWORD flags, const sockaddr *to, int tolen, LPWSAOVERLAPPED overlapped, LPWSAOVERLAPPED_COMPLETION_ROUTINE completion) {
    if (!real_WSASendTo) {
        resolve_real_functions();
    }
    if (!real_WSASendTo) {
        WSASetLastError(WSAEFAULT);
        return SOCKET_ERROR;
    }
    if (!bind_socket_if_needed(socket, to)) {
        return SOCKET_ERROR;
    }
    return real_WSASendTo(socket, buffers, buffer_count, bytes_sent, flags, to, tolen, overlapped, completion);
}

extern "C" HANDLE WINAPI hook_CreateFileW(LPCWSTR file_name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES security, DWORD creation, DWORD flags, HANDLE template_file) {
    if (!real_CreateFileW) {
        resolve_real_functions();
    }
    if (!real_CreateFileW) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    std::wstring redirected;
    if (redirect_path(file_name, redirected)) {
        return real_CreateFileW(redirected.c_str(), access, share, security, creation, flags, template_file);
    }
    return real_CreateFileW(file_name, access, share, security, creation, flags, template_file);
}

extern "C" HANDLE WINAPI hook_CreateFileA(LPCSTR file_name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES security, DWORD creation, DWORD flags, HANDLE template_file) {
    if (!real_CreateFileA) {
        resolve_real_functions();
    }
    if (!real_CreateFileA) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return INVALID_HANDLE_VALUE;
    }
    std::string redirected;
    if (redirect_path_a(file_name, redirected)) {
        return real_CreateFileA(redirected.c_str(), access, share, security, creation, flags, template_file);
    }
    return real_CreateFileA(file_name, access, share, security, creation, flags, template_file);
}

extern "C" DWORD WINAPI hook_GetPrivateProfileStringW(LPCWSTR app_name, LPCWSTR key_name, LPCWSTR default_value, LPWSTR returned_string, DWORD size, LPCWSTR file_name) {
    if (!real_GetPrivateProfileStringW) {
        resolve_real_functions();
    }
    if (!real_GetPrivateProfileStringW) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return 0;
    }
    std::wstring redirected;
    return real_GetPrivateProfileStringW(app_name, key_name, default_value, returned_string, size, redirect_path(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" DWORD WINAPI hook_GetPrivateProfileStringA(LPCSTR app_name, LPCSTR key_name, LPCSTR default_value, LPSTR returned_string, DWORD size, LPCSTR file_name) {
    if (!real_GetPrivateProfileStringA) {
        resolve_real_functions();
    }
    if (!real_GetPrivateProfileStringA) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return 0;
    }
    std::string redirected;
    return real_GetPrivateProfileStringA(app_name, key_name, default_value, returned_string, size, redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" UINT WINAPI hook_GetPrivateProfileIntW(LPCWSTR app_name, LPCWSTR key_name, INT default_value, LPCWSTR file_name) {
    if (!real_GetPrivateProfileIntW) {
        resolve_real_functions();
    }
    if (!real_GetPrivateProfileIntW) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return static_cast<UINT>(default_value);
    }
    std::wstring redirected;
    return real_GetPrivateProfileIntW(app_name, key_name, default_value, redirect_path(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" UINT WINAPI hook_GetPrivateProfileIntA(LPCSTR app_name, LPCSTR key_name, INT default_value, LPCSTR file_name) {
    if (!real_GetPrivateProfileIntA) {
        resolve_real_functions();
    }
    if (!real_GetPrivateProfileIntA) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return static_cast<UINT>(default_value);
    }
    std::string redirected;
    return real_GetPrivateProfileIntA(app_name, key_name, default_value, redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" BOOL WINAPI hook_WritePrivateProfileStringW(LPCWSTR app_name, LPCWSTR key_name, LPCWSTR value, LPCWSTR file_name) {
    if (!real_WritePrivateProfileStringW) {
        resolve_real_functions();
    }
    if (!real_WritePrivateProfileStringW) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return FALSE;
    }
    std::wstring redirected;
    return real_WritePrivateProfileStringW(app_name, key_name, value, redirect_path(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" BOOL WINAPI hook_WritePrivateProfileStringA(LPCSTR app_name, LPCSTR key_name, LPCSTR value, LPCSTR file_name) {
    if (!real_WritePrivateProfileStringA) {
        resolve_real_functions();
    }
    if (!real_WritePrivateProfileStringA) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return FALSE;
    }
    std::string redirected;
    return real_WritePrivateProfileStringA(app_name, key_name, value, redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" NtStatus NTAPI hook_NtCreateFile(PHANDLE file_handle, ACCESS_MASK desired_access, NtObjectAttributes *object_attributes, PVOID io_status_block, PLARGE_INTEGER allocation_size, ULONG file_attributes, ULONG share_access, ULONG create_disposition, ULONG create_options, PVOID ea_buffer, ULONG ea_length) {
    if (!real_NtCreateFile) {
        resolve_real_functions();
    }
    if (!real_NtCreateFile) {
        return static_cast<NtStatus>(0xC0000002L);
    }
    NtObjectAttributes redirected_attributes{};
    NtUnicodeString redirected_name{};
    std::wstring redirected_storage;
    if (redirect_nt_object_path(object_attributes, redirected_attributes, redirected_name, redirected_storage)) {
        return real_NtCreateFile(file_handle, desired_access, &redirected_attributes, io_status_block, allocation_size, file_attributes, share_access, create_disposition, create_options, ea_buffer, ea_length);
    }
    return real_NtCreateFile(file_handle, desired_access, object_attributes, io_status_block, allocation_size, file_attributes, share_access, create_disposition, create_options, ea_buffer, ea_length);
}

extern "C" NtStatus NTAPI hook_NtOpenFile(PHANDLE file_handle, ACCESS_MASK desired_access, NtObjectAttributes *object_attributes, PVOID io_status_block, ULONG share_access, ULONG open_options) {
    if (!real_NtOpenFile) {
        resolve_real_functions();
    }
    if (!real_NtOpenFile) {
        return static_cast<NtStatus>(0xC0000002L);
    }
    NtObjectAttributes redirected_attributes{};
    NtUnicodeString redirected_name{};
    std::wstring redirected_storage;
    if (redirect_nt_object_path(object_attributes, redirected_attributes, redirected_name, redirected_storage)) {
        return real_NtOpenFile(file_handle, desired_access, &redirected_attributes, io_status_block, share_access, open_options);
    }
    return real_NtOpenFile(file_handle, desired_access, object_attributes, io_status_block, share_access, open_options);
}

extern "C" BOOL WINAPI hook_DeleteFileW(LPCWSTR file_name) {
    if (!real_DeleteFileW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_DeleteFileW(redirect_path(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" BOOL WINAPI hook_DeleteFileA(LPCSTR file_name) {
    if (!real_DeleteFileA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_DeleteFileA(redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" DWORD WINAPI hook_GetFileAttributesW(LPCWSTR file_name) {
    if (!real_GetFileAttributesW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_GetFileAttributesW(redirect_path(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" DWORD WINAPI hook_GetFileAttributesA(LPCSTR file_name) {
    if (!real_GetFileAttributesA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_GetFileAttributesA(redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name);
}

extern "C" BOOL WINAPI hook_GetFileAttributesExW(LPCWSTR file_name, GET_FILEEX_INFO_LEVELS level, LPVOID info) {
    if (!real_GetFileAttributesExW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_GetFileAttributesExW(redirect_path(file_name, redirected) ? redirected.c_str() : file_name, level, info);
}

extern "C" BOOL WINAPI hook_GetFileAttributesExA(LPCSTR file_name, GET_FILEEX_INFO_LEVELS level, LPVOID info) {
    if (!real_GetFileAttributesExA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_GetFileAttributesExA(redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name, level, info);
}

extern "C" BOOL WINAPI hook_SetFileAttributesW(LPCWSTR file_name, DWORD attributes) {
    if (!real_SetFileAttributesW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_SetFileAttributesW(redirect_path(file_name, redirected) ? redirected.c_str() : file_name, attributes);
}

extern "C" BOOL WINAPI hook_SetFileAttributesA(LPCSTR file_name, DWORD attributes) {
    if (!real_SetFileAttributesA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_SetFileAttributesA(redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name, attributes);
}

extern "C" BOOL WINAPI hook_CreateDirectoryW(LPCWSTR path_name, LPSECURITY_ATTRIBUTES security) {
    if (!real_CreateDirectoryW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_CreateDirectoryW(redirect_path(path_name, redirected) ? redirected.c_str() : path_name, security);
}

extern "C" BOOL WINAPI hook_CreateDirectoryA(LPCSTR path_name, LPSECURITY_ATTRIBUTES security) {
    if (!real_CreateDirectoryA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_CreateDirectoryA(redirect_path_a(path_name, redirected) ? redirected.c_str() : path_name, security);
}

extern "C" BOOL WINAPI hook_RemoveDirectoryW(LPCWSTR path_name) {
    if (!real_RemoveDirectoryW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_RemoveDirectoryW(redirect_path(path_name, redirected) ? redirected.c_str() : path_name);
}

extern "C" BOOL WINAPI hook_RemoveDirectoryA(LPCSTR path_name) {
    if (!real_RemoveDirectoryA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_RemoveDirectoryA(redirect_path_a(path_name, redirected) ? redirected.c_str() : path_name);
}

extern "C" BOOL WINAPI hook_MoveFileW(LPCWSTR existing_name, LPCWSTR new_name) {
    if (!real_MoveFileW) {
        resolve_real_functions();
    }
    std::wstring redirected_existing;
    std::wstring redirected_new;
    return real_MoveFileW(
        redirect_path(existing_name, redirected_existing) ? redirected_existing.c_str() : existing_name,
        redirect_path(new_name, redirected_new) ? redirected_new.c_str() : new_name);
}

extern "C" BOOL WINAPI hook_MoveFileA(LPCSTR existing_name, LPCSTR new_name) {
    if (!real_MoveFileA) {
        resolve_real_functions();
    }
    std::string redirected_existing;
    std::string redirected_new;
    return real_MoveFileA(
        redirect_path_a(existing_name, redirected_existing) ? redirected_existing.c_str() : existing_name,
        redirect_path_a(new_name, redirected_new) ? redirected_new.c_str() : new_name);
}

extern "C" BOOL WINAPI hook_MoveFileExW(LPCWSTR existing_name, LPCWSTR new_name, DWORD flags) {
    if (!real_MoveFileExW) {
        resolve_real_functions();
    }
    std::wstring redirected_existing;
    std::wstring redirected_new;
    return real_MoveFileExW(
        redirect_path(existing_name, redirected_existing) ? redirected_existing.c_str() : existing_name,
        redirect_path(new_name, redirected_new) ? redirected_new.c_str() : new_name,
        flags);
}

extern "C" BOOL WINAPI hook_MoveFileExA(LPCSTR existing_name, LPCSTR new_name, DWORD flags) {
    if (!real_MoveFileExA) {
        resolve_real_functions();
    }
    std::string redirected_existing;
    std::string redirected_new;
    return real_MoveFileExA(
        redirect_path_a(existing_name, redirected_existing) ? redirected_existing.c_str() : existing_name,
        redirect_path_a(new_name, redirected_new) ? redirected_new.c_str() : new_name,
        flags);
}

extern "C" BOOL WINAPI hook_CopyFileW(LPCWSTR existing_name, LPCWSTR new_name, BOOL fail_if_exists) {
    if (!real_CopyFileW) {
        resolve_real_functions();
    }
    std::wstring redirected_existing;
    std::wstring redirected_new;
    return real_CopyFileW(
        redirect_path(existing_name, redirected_existing) ? redirected_existing.c_str() : existing_name,
        redirect_path(new_name, redirected_new) ? redirected_new.c_str() : new_name,
        fail_if_exists);
}

extern "C" BOOL WINAPI hook_CopyFileA(LPCSTR existing_name, LPCSTR new_name, BOOL fail_if_exists) {
    if (!real_CopyFileA) {
        resolve_real_functions();
    }
    std::string redirected_existing;
    std::string redirected_new;
    return real_CopyFileA(
        redirect_path_a(existing_name, redirected_existing) ? redirected_existing.c_str() : existing_name,
        redirect_path_a(new_name, redirected_new) ? redirected_new.c_str() : new_name,
        fail_if_exists);
}

extern "C" HANDLE WINAPI hook_FindFirstFileW(LPCWSTR file_name, LPWIN32_FIND_DATAW find_data) {
    if (!real_FindFirstFileW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_FindFirstFileW(redirect_path(file_name, redirected) ? redirected.c_str() : file_name, find_data);
}

extern "C" HANDLE WINAPI hook_FindFirstFileA(LPCSTR file_name, LPWIN32_FIND_DATAA find_data) {
    if (!real_FindFirstFileA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_FindFirstFileA(redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name, find_data);
}

extern "C" HANDLE WINAPI hook_FindFirstFileExW(LPCWSTR file_name, FINDEX_INFO_LEVELS info_level, LPVOID find_data, FINDEX_SEARCH_OPS search_op, LPVOID search_filter, DWORD flags) {
    if (!real_FindFirstFileExW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_FindFirstFileExW(redirect_path(file_name, redirected) ? redirected.c_str() : file_name, info_level, find_data, search_op, search_filter, flags);
}

extern "C" HANDLE WINAPI hook_FindFirstFileExA(LPCSTR file_name, FINDEX_INFO_LEVELS info_level, LPVOID find_data, FINDEX_SEARCH_OPS search_op, LPVOID search_filter, DWORD flags) {
    if (!real_FindFirstFileExA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_FindFirstFileExA(redirect_path_a(file_name, redirected) ? redirected.c_str() : file_name, info_level, find_data, search_op, search_filter, flags);
}

extern "C" HMODULE WINAPI hook_LoadLibraryW(LPCWSTR file_name) {
    if (!real_LoadLibraryW) {
        resolve_real_functions();
    }
    HMODULE module = real_LoadLibraryW ? real_LoadLibraryW(file_name) : nullptr;
    patch_after_late_load(module, file_name ? std::wstring(file_name) : L"");
    return module;
}

extern "C" HMODULE WINAPI hook_LoadLibraryA(LPCSTR file_name) {
    if (!real_LoadLibraryA) {
        resolve_real_functions();
    }
    HMODULE module = real_LoadLibraryA ? real_LoadLibraryA(file_name) : nullptr;
    std::wstring wide_name;
    if (file_name) {
        widen_ansi(file_name, wide_name);
    }
    patch_after_late_load(module, wide_name);
    return module;
}

extern "C" HMODULE WINAPI hook_LoadLibraryExW(LPCWSTR file_name, HANDLE file, DWORD flags) {
    if (!real_LoadLibraryExW) {
        resolve_real_functions();
    }
    HMODULE module = real_LoadLibraryExW ? real_LoadLibraryExW(file_name, file, flags) : nullptr;
    patch_after_late_load(module, file_name ? std::wstring(file_name) : L"");
    return module;
}

extern "C" HMODULE WINAPI hook_LoadLibraryExA(LPCSTR file_name, HANDLE file, DWORD flags) {
    if (!real_LoadLibraryExA) {
        resolve_real_functions();
    }
    HMODULE module = real_LoadLibraryExA ? real_LoadLibraryExA(file_name, file, flags) : nullptr;
    std::wstring wide_name;
    if (file_name) {
        widen_ansi(file_name, wide_name);
    }
    patch_after_late_load(module, wide_name);
    return module;
}

extern "C" LSTATUS WINAPI hook_RegCreateKeyExW(HKEY key, LPCWSTR sub_key, DWORD reserved, LPWSTR cls, DWORD options, REGSAM sam, const LPSECURITY_ATTRIBUTES security, PHKEY result, LPDWORD disposition) {
    if (!real_RegCreateKeyExW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_RegCreateKeyExW ? real_RegCreateKeyExW(key, redirect_registry_path(sub_key, redirected) ? redirected.c_str() : sub_key, reserved, cls, options, sam, security, result, disposition) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegCreateKeyExA(HKEY key, LPCSTR sub_key, DWORD reserved, LPSTR cls, DWORD options, REGSAM sam, const LPSECURITY_ATTRIBUTES security, PHKEY result, LPDWORD disposition) {
    if (!real_RegCreateKeyExA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_RegCreateKeyExA ? real_RegCreateKeyExA(key, redirect_registry_path_a(sub_key, redirected) ? redirected.c_str() : sub_key, reserved, cls, options, sam, security, result, disposition) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegCreateKeyW(HKEY key, LPCWSTR sub_key, PHKEY result) {
    if (!real_RegCreateKeyW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_RegCreateKeyW ? real_RegCreateKeyW(key, redirect_registry_path(sub_key, redirected) ? redirected.c_str() : sub_key, result) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegCreateKeyA(HKEY key, LPCSTR sub_key, PHKEY result) {
    if (!real_RegCreateKeyA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_RegCreateKeyA ? real_RegCreateKeyA(key, redirect_registry_path_a(sub_key, redirected) ? redirected.c_str() : sub_key, result) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegOpenKeyExW(HKEY key, LPCWSTR sub_key, DWORD options, REGSAM sam, PHKEY result) {
    if (!real_RegOpenKeyExW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_RegOpenKeyExW ? real_RegOpenKeyExW(key, redirect_registry_path(sub_key, redirected) ? redirected.c_str() : sub_key, options, sam, result) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegOpenKeyExA(HKEY key, LPCSTR sub_key, DWORD options, REGSAM sam, PHKEY result) {
    if (!real_RegOpenKeyExA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_RegOpenKeyExA ? real_RegOpenKeyExA(key, redirect_registry_path_a(sub_key, redirected) ? redirected.c_str() : sub_key, options, sam, result) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegOpenKeyW(HKEY key, LPCWSTR sub_key, PHKEY result) {
    if (!real_RegOpenKeyW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_RegOpenKeyW ? real_RegOpenKeyW(key, redirect_registry_path(sub_key, redirected) ? redirected.c_str() : sub_key, result) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegOpenKeyA(HKEY key, LPCSTR sub_key, PHKEY result) {
    if (!real_RegOpenKeyA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_RegOpenKeyA ? real_RegOpenKeyA(key, redirect_registry_path_a(sub_key, redirected) ? redirected.c_str() : sub_key, result) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegDeleteKeyW(HKEY key, LPCWSTR sub_key) {
    if (!real_RegDeleteKeyW) {
        resolve_real_functions();
    }
    std::wstring redirected;
    return real_RegDeleteKeyW ? real_RegDeleteKeyW(key, redirect_registry_path(sub_key, redirected) ? redirected.c_str() : sub_key) : ERROR_PROC_NOT_FOUND;
}

extern "C" LSTATUS WINAPI hook_RegDeleteKeyA(HKEY key, LPCSTR sub_key) {
    if (!real_RegDeleteKeyA) {
        resolve_real_functions();
    }
    std::string redirected;
    return real_RegDeleteKeyA ? real_RegDeleteKeyA(key, redirect_registry_path_a(sub_key, redirected) ? redirected.c_str() : sub_key) : ERROR_PROC_NOT_FOUND;
}

extern "C" FARPROC WINAPI hook_GetProcAddress(HMODULE module, LPCSTR proc_name) {
    if (!real_GetProcAddress) {
        resolve_real_functions();
    }
    if (!real_GetProcAddress) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return nullptr;
    }
    if (!proc_name || (reinterpret_cast<ULONG_PTR>(proc_name) >> 16) == 0) {
        return real_GetProcAddress(module, proc_name);
    }

    if (safe_equals_ignore_case(proc_name, "LoadLibraryA")) {
        return reinterpret_cast<FARPROC>(&hook_LoadLibraryA);
    }
    if (safe_equals_ignore_case(proc_name, "LoadLibraryW")) {
        return reinterpret_cast<FARPROC>(&hook_LoadLibraryW);
    }
    if (safe_equals_ignore_case(proc_name, "LoadLibraryExA")) {
        return reinterpret_cast<FARPROC>(&hook_LoadLibraryExA);
    }
    if (safe_equals_ignore_case(proc_name, "LoadLibraryExW")) {
        return reinterpret_cast<FARPROC>(&hook_LoadLibraryExW);
    }
    if (safe_equals_ignore_case(proc_name, "GetProcAddress")) {
        return reinterpret_cast<FARPROC>(&hook_GetProcAddress);
    }
    HMODULE ws2 = GetModuleHandleW(L"ws2_32.dll");
    HMODULE wsock = GetModuleHandleW(L"wsock32.dll");
    if (bind_enabled && (module == ws2 || module == wsock)) {
        if (safe_equals_ignore_case(proc_name, "connect")) {
            return reinterpret_cast<FARPROC>(&hook_connect);
        }
        if (safe_equals_ignore_case(proc_name, "bind")) {
            return reinterpret_cast<FARPROC>(&hook_bind);
        }
        if (safe_equals_ignore_case(proc_name, "WSAConnect")) {
            return reinterpret_cast<FARPROC>(&hook_WSAConnect);
        }
        if (safe_equals_ignore_case(proc_name, "WSAIoctl")) {
            return reinterpret_cast<FARPROC>(&hook_WSAIoctl);
        }
        if (safe_equals_ignore_case(proc_name, "sendto")) {
            return reinterpret_cast<FARPROC>(&hook_sendto);
        }
        if (safe_equals_ignore_case(proc_name, "WSASendTo")) {
            return reinterpret_cast<FARPROC>(&hook_WSASendTo);
        }
    }

    if (file_redirect_enabled) {
        if (safe_equals_ignore_case(proc_name, "CreateFileA")) {
            return reinterpret_cast<FARPROC>(&hook_CreateFileA);
        }
        if (safe_equals_ignore_case(proc_name, "CreateFileW")) {
            return reinterpret_cast<FARPROC>(&hook_CreateFileW);
        }
        if (safe_equals_ignore_case(proc_name, "GetPrivateProfileStringA")) {
            return reinterpret_cast<FARPROC>(&hook_GetPrivateProfileStringA);
        }
        if (safe_equals_ignore_case(proc_name, "GetPrivateProfileStringW")) {
            return reinterpret_cast<FARPROC>(&hook_GetPrivateProfileStringW);
        }
        if (safe_equals_ignore_case(proc_name, "GetPrivateProfileIntA")) {
            return reinterpret_cast<FARPROC>(&hook_GetPrivateProfileIntA);
        }
        if (safe_equals_ignore_case(proc_name, "GetPrivateProfileIntW")) {
            return reinterpret_cast<FARPROC>(&hook_GetPrivateProfileIntW);
        }
        if (safe_equals_ignore_case(proc_name, "WritePrivateProfileStringA")) {
            return reinterpret_cast<FARPROC>(&hook_WritePrivateProfileStringA);
        }
        if (safe_equals_ignore_case(proc_name, "WritePrivateProfileStringW")) {
            return reinterpret_cast<FARPROC>(&hook_WritePrivateProfileStringW);
        }
        if (safe_equals_ignore_case(proc_name, "NtCreateFile")) {
            return reinterpret_cast<FARPROC>(&hook_NtCreateFile);
        }
        if (safe_equals_ignore_case(proc_name, "NtOpenFile")) {
            return reinterpret_cast<FARPROC>(&hook_NtOpenFile);
        }
        if (safe_equals_ignore_case(proc_name, "DeleteFileA")) {
            return reinterpret_cast<FARPROC>(&hook_DeleteFileA);
        }
        if (safe_equals_ignore_case(proc_name, "DeleteFileW")) {
            return reinterpret_cast<FARPROC>(&hook_DeleteFileW);
        }
        if (safe_equals_ignore_case(proc_name, "GetFileAttributesA")) {
            return reinterpret_cast<FARPROC>(&hook_GetFileAttributesA);
        }
        if (safe_equals_ignore_case(proc_name, "GetFileAttributesW")) {
            return reinterpret_cast<FARPROC>(&hook_GetFileAttributesW);
        }
        if (safe_equals_ignore_case(proc_name, "GetFileAttributesExA")) {
            return reinterpret_cast<FARPROC>(&hook_GetFileAttributesExA);
        }
        if (safe_equals_ignore_case(proc_name, "GetFileAttributesExW")) {
            return reinterpret_cast<FARPROC>(&hook_GetFileAttributesExW);
        }
        if (safe_equals_ignore_case(proc_name, "SetFileAttributesA")) {
            return reinterpret_cast<FARPROC>(&hook_SetFileAttributesA);
        }
        if (safe_equals_ignore_case(proc_name, "SetFileAttributesW")) {
            return reinterpret_cast<FARPROC>(&hook_SetFileAttributesW);
        }
        if (safe_equals_ignore_case(proc_name, "CreateDirectoryA")) {
            return reinterpret_cast<FARPROC>(&hook_CreateDirectoryA);
        }
        if (safe_equals_ignore_case(proc_name, "CreateDirectoryW")) {
            return reinterpret_cast<FARPROC>(&hook_CreateDirectoryW);
        }
        if (safe_equals_ignore_case(proc_name, "RemoveDirectoryA")) {
            return reinterpret_cast<FARPROC>(&hook_RemoveDirectoryA);
        }
        if (safe_equals_ignore_case(proc_name, "RemoveDirectoryW")) {
            return reinterpret_cast<FARPROC>(&hook_RemoveDirectoryW);
        }
        if (safe_equals_ignore_case(proc_name, "MoveFileA")) {
            return reinterpret_cast<FARPROC>(&hook_MoveFileA);
        }
        if (safe_equals_ignore_case(proc_name, "MoveFileW")) {
            return reinterpret_cast<FARPROC>(&hook_MoveFileW);
        }
        if (safe_equals_ignore_case(proc_name, "MoveFileExA")) {
            return reinterpret_cast<FARPROC>(&hook_MoveFileExA);
        }
        if (safe_equals_ignore_case(proc_name, "MoveFileExW")) {
            return reinterpret_cast<FARPROC>(&hook_MoveFileExW);
        }
        if (safe_equals_ignore_case(proc_name, "CopyFileA")) {
            return reinterpret_cast<FARPROC>(&hook_CopyFileA);
        }
        if (safe_equals_ignore_case(proc_name, "CopyFileW")) {
            return reinterpret_cast<FARPROC>(&hook_CopyFileW);
        }
        if (safe_equals_ignore_case(proc_name, "FindFirstFileA")) {
            return reinterpret_cast<FARPROC>(&hook_FindFirstFileA);
        }
        if (safe_equals_ignore_case(proc_name, "FindFirstFileW")) {
            return reinterpret_cast<FARPROC>(&hook_FindFirstFileW);
        }
        if (safe_equals_ignore_case(proc_name, "FindFirstFileExA")) {
            return reinterpret_cast<FARPROC>(&hook_FindFirstFileExA);
        }
        if (safe_equals_ignore_case(proc_name, "FindFirstFileExW")) {
            return reinterpret_cast<FARPROC>(&hook_FindFirstFileExW);
        }
    }

    if (registry_redirect_enabled) {
        if (safe_equals_ignore_case(proc_name, "RegCreateKeyExW")) {
            return reinterpret_cast<FARPROC>(&hook_RegCreateKeyExW);
        }
        if (safe_equals_ignore_case(proc_name, "RegCreateKeyExA")) {
            return reinterpret_cast<FARPROC>(&hook_RegCreateKeyExA);
        }
        if (safe_equals_ignore_case(proc_name, "RegCreateKeyW")) {
            return reinterpret_cast<FARPROC>(&hook_RegCreateKeyW);
        }
        if (safe_equals_ignore_case(proc_name, "RegCreateKeyA")) {
            return reinterpret_cast<FARPROC>(&hook_RegCreateKeyA);
        }
        if (safe_equals_ignore_case(proc_name, "RegOpenKeyExW")) {
            return reinterpret_cast<FARPROC>(&hook_RegOpenKeyExW);
        }
        if (safe_equals_ignore_case(proc_name, "RegOpenKeyExA")) {
            return reinterpret_cast<FARPROC>(&hook_RegOpenKeyExA);
        }
        if (safe_equals_ignore_case(proc_name, "RegOpenKeyW")) {
            return reinterpret_cast<FARPROC>(&hook_RegOpenKeyW);
        }
        if (safe_equals_ignore_case(proc_name, "RegOpenKeyA")) {
            return reinterpret_cast<FARPROC>(&hook_RegOpenKeyA);
        }
        if (safe_equals_ignore_case(proc_name, "RegDeleteKeyW")) {
            return reinterpret_cast<FARPROC>(&hook_RegDeleteKeyW);
        }
        if (safe_equals_ignore_case(proc_name, "RegDeleteKeyA")) {
            return reinterpret_cast<FARPROC>(&hook_RegDeleteKeyA);
        }
    }

    return real_GetProcAddress(module, proc_name);
}

bool equals_ignore_case(const char *left, const char *right) {
    if (!left || !right) {
        return false;
    }
    for (; *left && *right; ++left, ++right) {
        char a = static_cast<char>(tolower(static_cast<unsigned char>(*left)));
        char b = static_cast<char>(tolower(static_cast<unsigned char>(*right)));
        if (a != b) {
            return false;
        }
    }
    return *left == '\0' && *right == '\0';
}

bool safe_equals_ignore_case(const char *left, const char *right) {
    __try {
        return equals_ignore_case(left, right);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

void patch_module_iat_guarded(HMODULE module);
void patch_all_modules();
bool is_patchable_module(HMODULE module);

void patch_after_late_load(HMODULE module, const std::wstring &name) {
    if (!module) {
        return;
    }
    if (!is_patchable_module(module)) {
        return;
    }
    patch_module_iat_guarded(module);
    long count = ++late_load_count;
    if (count <= 80) {
        log_line(L"patched late-loaded module " + name + L" patches=" + std::to_wstring(patch_count.load()));
    }
}

bool contains_case_insensitive(const std::wstring &value, const std::wstring &needle) {
    return value.find(needle) != std::wstring::npos;
}

bool path_ends_with(const std::wstring &value, const std::wstring &suffix) {
    return value.size() >= suffix.size() && value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

bool is_patchable_module(HMODULE module) {
    if (!module || module == self_module) {
        return false;
    }
    wchar_t path_buffer[MAX_PATH * 4]{};
    DWORD length = GetModuleFileNameW(module, path_buffer, static_cast<DWORD>(std::size(path_buffer)));
    if (length == 0) {
        return false;
    }
    std::wstring path(path_buffer, length);
    std::transform(path.begin(), path.end(), path.begin(), [](wchar_t ch) {
        return static_cast<wchar_t>(std::towlower(ch));
    });
    std::wstring path_cmp = normalize_path_for_compare(path);

    if (
        path_ends_with(path, L"\\kernel32.dll") ||
        path_ends_with(path, L"\\kernelbase.dll") ||
        path_ends_with(path, L"\\ntdll.dll")
    ) {
        return false;
    }
    if (
        path_ends_with(path, L"\\ucrtbase.dll") ||
        path_ends_with(path, L"\\ucrtbased.dll") ||
        path_ends_with(path, L"\\msvcrt.dll") ||
        path_ends_with(path, L"\\msvcr100.dll") ||
        path_ends_with(path, L"\\msvcr110.dll") ||
        path_ends_with(path, L"\\msvcr120.dll") ||
        path_ends_with(path, L"\\msvcr140.dll") ||
        path_ends_with(path, L"\\vcruntime140.dll") ||
        path_ends_with(path, L"\\vcruntime140_1.dll")
    ) {
        return true;
    }

    return !process_dir_cmp.empty() && path_has_prefix_boundary(path_cmp, process_dir_cmp);
}

bool rva_in_image(DWORD rva, DWORD size, DWORD image_size) {
    if (!rva || !image_size || rva >= image_size) {
        return false;
    }
    return size <= image_size - rva;
}

void patch_thunk(void **slot, void *hook) {
    if (*slot == hook) {
        return;
    }
    DWORD old_protect = 0;
    if (VirtualProtect(slot, sizeof(void *), PAGE_READWRITE, &old_protect)) {
        *slot = hook;
        FlushInstructionCache(GetCurrentProcess(), slot, sizeof(void *));
        DWORD ignored = 0;
        VirtualProtect(slot, sizeof(void *), old_protect, &ignored);
        ++patch_count;
    }
}

void patch_module_iat(HMODULE module) {
    if (!module || module == self_module) {
        return;
    }
    auto *base = reinterpret_cast<std::uint8_t *>(module);
    auto *dos = reinterpret_cast<IMAGE_DOS_HEADER *>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0) {
        return;
    }
    auto *nt = reinterpret_cast<IMAGE_NT_HEADERS *>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) {
        return;
    }
    DWORD image_size = nt->OptionalHeader.SizeOfImage;
    if (!rva_in_image(static_cast<DWORD>(dos->e_lfanew), sizeof(IMAGE_NT_HEADERS), image_size)) {
        return;
    }
    auto &dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!rva_in_image(dir.VirtualAddress, sizeof(IMAGE_IMPORT_DESCRIPTOR), image_size)) {
        return;
    }
    auto *desc = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR *>(base + dir.VirtualAddress);
    DWORD max_desc = dir.Size / sizeof(IMAGE_IMPORT_DESCRIPTOR);
    if (max_desc == 0 || max_desc > 4096) {
        max_desc = 4096;
    }
    for (DWORD desc_index = 0; desc_index < max_desc && desc[desc_index].Name; ++desc_index) {
        IMAGE_IMPORT_DESCRIPTOR *current = &desc[desc_index];
        if (!current->OriginalFirstThunk || !current->FirstThunk) {
            continue;
        }
        if (
            !rva_in_image(current->Name, 1, image_size) ||
            !rva_in_image(current->OriginalFirstThunk, sizeof(IMAGE_THUNK_DATA), image_size) ||
            !rva_in_image(current->FirstThunk, sizeof(IMAGE_THUNK_DATA), image_size)
        ) {
            continue;
        }
        const char *dll_name = reinterpret_cast<const char *>(base + current->Name);
        bool socket_dll = safe_equals_ignore_case(dll_name, "ws2_32.dll") || safe_equals_ignore_case(dll_name, "wsock32.dll");
        auto *orig = reinterpret_cast<IMAGE_THUNK_DATA *>(base + current->OriginalFirstThunk);
        auto *first = reinterpret_cast<IMAGE_THUNK_DATA *>(base + current->FirstThunk);
        for (DWORD thunk_index = 0; thunk_index < 4096; ++thunk_index, ++orig, ++first) {
            DWORD orig_rva = current->OriginalFirstThunk + static_cast<DWORD>(thunk_index * sizeof(IMAGE_THUNK_DATA));
            DWORD first_rva = current->FirstThunk + static_cast<DWORD>(thunk_index * sizeof(IMAGE_THUNK_DATA));
            if (!rva_in_image(orig_rva, sizeof(IMAGE_THUNK_DATA), image_size) || !rva_in_image(first_rva, sizeof(IMAGE_THUNK_DATA), image_size)) {
                break;
            }
            if (!orig->u1.AddressOfData || !first->u1.Function) {
                break;
            }
            if (IMAGE_SNAP_BY_ORDINAL(orig->u1.Ordinal)) {
                if (socket_dll) {
                    WORD ordinal = static_cast<WORD>(IMAGE_ORDINAL(orig->u1.Ordinal));
                    void **slot = reinterpret_cast<void **>(&first->u1.Function);
                    if (ordinal == 2) {
                        patch_thunk(slot, reinterpret_cast<void *>(&hook_bind));
                    } else if (ordinal == 4) {
                        patch_thunk(slot, reinterpret_cast<void *>(&hook_connect));
                    } else if (ordinal == 20) {
                        patch_thunk(slot, reinterpret_cast<void *>(&hook_sendto));
                    }
                }
                continue;
            }
            DWORD import_rva = static_cast<DWORD>(orig->u1.AddressOfData);
            if (!rva_in_image(import_rva, sizeof(IMAGE_IMPORT_BY_NAME), image_size)) {
                continue;
            }
            auto *import = reinterpret_cast<IMAGE_IMPORT_BY_NAME *>(base + import_rva);
            const char *name = reinterpret_cast<const char *>(import->Name);
            void **slot = reinterpret_cast<void **>(&first->u1.Function);
            if (socket_dll && safe_equals_ignore_case(name, "connect")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_connect));
            } else if (socket_dll && safe_equals_ignore_case(name, "bind")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_bind));
            } else if (socket_dll && safe_equals_ignore_case(name, "WSAConnect")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_WSAConnect));
            } else if (socket_dll && safe_equals_ignore_case(name, "WSAIoctl")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_WSAIoctl));
            } else if (socket_dll && safe_equals_ignore_case(name, "sendto")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_sendto));
            } else if (socket_dll && safe_equals_ignore_case(name, "WSASendTo")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_WSASendTo));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "CreateFileA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_CreateFileA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "CreateFileW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_CreateFileW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "NtCreateFile")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_NtCreateFile));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "NtOpenFile")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_NtOpenFile));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "DeleteFileA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_DeleteFileA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "DeleteFileW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_DeleteFileW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetFileAttributesA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetFileAttributesA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetFileAttributesW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetFileAttributesW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetFileAttributesExA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetFileAttributesExA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetFileAttributesExW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetFileAttributesExW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "SetFileAttributesA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_SetFileAttributesA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "SetFileAttributesW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_SetFileAttributesW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "CreateDirectoryA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_CreateDirectoryA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "CreateDirectoryW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_CreateDirectoryW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "RemoveDirectoryA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RemoveDirectoryA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "RemoveDirectoryW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RemoveDirectoryW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "MoveFileA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_MoveFileA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "MoveFileW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_MoveFileW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "MoveFileExA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_MoveFileExA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "MoveFileExW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_MoveFileExW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "CopyFileA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_CopyFileA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "CopyFileW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_CopyFileW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetPrivateProfileStringA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetPrivateProfileStringA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetPrivateProfileStringW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetPrivateProfileStringW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetPrivateProfileIntA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetPrivateProfileIntA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "GetPrivateProfileIntW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetPrivateProfileIntW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "WritePrivateProfileStringA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_WritePrivateProfileStringA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "WritePrivateProfileStringW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_WritePrivateProfileStringW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "FindFirstFileA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_FindFirstFileA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "FindFirstFileW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_FindFirstFileW));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "FindFirstFileExA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_FindFirstFileExA));
            } else if (file_redirect_enabled && safe_equals_ignore_case(name, "FindFirstFileExW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_FindFirstFileExW));
            } else if (safe_equals_ignore_case(name, "LoadLibraryW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_LoadLibraryW));
            } else if (safe_equals_ignore_case(name, "LoadLibraryA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_LoadLibraryA));
            } else if (safe_equals_ignore_case(name, "LoadLibraryExW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_LoadLibraryExW));
            } else if (safe_equals_ignore_case(name, "LoadLibraryExA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_LoadLibraryExA));
            } else if (safe_equals_ignore_case(name, "GetProcAddress")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_GetProcAddress));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegCreateKeyExW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegCreateKeyExW));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegCreateKeyExA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegCreateKeyExA));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegCreateKeyW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegCreateKeyW));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegCreateKeyA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegCreateKeyA));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegOpenKeyExW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegOpenKeyExW));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegOpenKeyExA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegOpenKeyExA));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegOpenKeyW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegOpenKeyW));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegOpenKeyA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegOpenKeyA));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegDeleteKeyW")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegDeleteKeyW));
            } else if (registry_redirect_enabled && safe_equals_ignore_case(name, "RegDeleteKeyA")) {
                patch_thunk(slot, reinterpret_cast<void *>(&hook_RegDeleteKeyA));
            }
        }
    }
}

void patch_module_iat_guarded(HMODULE module) {
    __try {
        patch_module_iat(module);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        // Some loader-phase modules can have transient or protected import tables.
    }
}

void patch_all_modules() {
    HMODULE modules[1024]{};
    DWORD needed = 0;
    if (!EnumProcessModules(GetCurrentProcess(), modules, sizeof(modules), &needed)) {
        return;
    }
    size_t count = std::min<size_t>(needed / sizeof(HMODULE), std::size(modules));
    for (size_t i = 0; i < count; ++i) {
        if (is_patchable_module(modules[i])) {
            patch_module_iat_guarded(modules[i]);
        }
    }
}

void signal_ready_event() {
    if (ready_event_name.empty()) {
        return;
    }
    HANDLE event = OpenEventW(EVENT_MODIFY_STATE, FALSE, ready_event_name.c_str());
    if (event) {
        SetEvent(event);
        CloseHandle(event);
    }
}

DWORD WINAPI patch_thread(LPVOID) {
    for (int i = 0; i < 1200; ++i) {
        Sleep(500);
        patch_all_modules();
    }
    log_line(L"netbind patch loop done patches=" + std::to_wstring(patch_count.load()));
    return 0;
}

extern "C" __declspec(dllexport) DWORD WINAPI GuiTestNetBindPatchNow(LPVOID) {
    log_line(L"patch export entered");
    resolve_real_functions();
    log_line(L"real functions resolved");
    patch_all_modules();
    log_line(L"initial patch done patches=" + std::to_wstring(patch_count.load()));
    signal_ready_event();

    HANDLE thread = CreateThread(nullptr, 0, patch_thread, nullptr, 0, nullptr);
    if (thread) {
        CloseHandle(thread);
    } else {
        log_line(L"patch background thread create failed err=" + std::to_wstring(GetLastError()));
    }
    return 0;
}

void initialize() {
    InitializeCriticalSection(&log_lock);

    wchar_t process_path[MAX_PATH * 4]{};
    DWORD process_path_length = GetModuleFileNameW(nullptr, process_path, static_cast<DWORD>(std::size(process_path)));
    if (process_path_length > 0) {
        std::wstring process_dir(process_path, process_path_length);
        size_t slash = process_dir.find_last_of(L"\\/");
        if (slash != std::wstring::npos) {
            process_dir.resize(slash);
            process_dir_cmp = normalize_path_for_compare(process_dir);
        }
    }

    wchar_t ip[64]{};
    if (GetEnvironmentVariableW(L"GUI_TEST_PC_BIND_IP", ip, 64) > 0) {
        bind_enabled = InetPtonW(AF_INET, ip, &bind_addr) == 1;
    }

    wchar_t log[MAX_PATH * 4]{};
    if (GetEnvironmentVariableW(L"GUI_TEST_PC_NETBIND_LOG", log, static_cast<DWORD>(std::size(log))) > 0) {
        log_path = log;
    }

    wchar_t ready_event[MAX_PATH * 2]{};
    if (GetEnvironmentVariableW(L"GUI_TEST_PC_HOOK_READY_EVENT", ready_event, static_cast<DWORD>(std::size(ready_event))) > 0) {
        ready_event_name = ready_event;
    }

    std::wstring legacy_redirect_from = get_env_wstring(L"GUI_TEST_PC_FILE_REDIRECT_FROM");
    std::wstring legacy_redirect_to = get_env_wstring(L"GUI_TEST_PC_FILE_REDIRECT_TO");
    if (!legacy_redirect_from.empty() && !legacy_redirect_to.empty()) {
        add_file_redirect_rule(legacy_redirect_from, legacy_redirect_to);
    }

    int redirect_count = 64;
    std::wstring redirect_count_text = get_env_wstring(L"GUI_TEST_PC_FILE_REDIRECT_COUNT");
    if (!redirect_count_text.empty()) {
        redirect_count = _wtoi(redirect_count_text.c_str());
        if (redirect_count < 0) {
            redirect_count = 0;
        }
        if (redirect_count > 128) {
            redirect_count = 128;
        }
    }
    for (int i = 1; i <= redirect_count; ++i) {
        wchar_t from_name[96]{};
        wchar_t to_name[96]{};
        swprintf_s(from_name, L"GUI_TEST_PC_FILE_REDIRECT_FROM_%d", i);
        swprintf_s(to_name, L"GUI_TEST_PC_FILE_REDIRECT_TO_%d", i);
        std::wstring from = get_env_wstring(from_name);
        std::wstring to = get_env_wstring(to_name);
        if (!from.empty() && !to.empty()) {
            add_file_redirect_rule(from, to);
        }
    }

    wchar_t registry_from_buffer[MAX_PATH * 4]{};
    wchar_t registry_to_buffer[MAX_PATH * 4]{};
    if (GetEnvironmentVariableW(L"GUI_TEST_PC_REGISTRY_REDIRECT_FROM", registry_from_buffer, static_cast<DWORD>(std::size(registry_from_buffer))) > 0) {
        registry_from = strip_extended_prefix(registry_from_buffer);
        std::replace(registry_from.begin(), registry_from.end(), L'/', L'\\');
        while (!registry_from.empty() && registry_from.front() == L'\\') {
            registry_from.erase(registry_from.begin());
        }
        while (!registry_from.empty() && registry_from.back() == L'\\') {
            registry_from.pop_back();
        }
    }
    if (GetEnvironmentVariableW(L"GUI_TEST_PC_REGISTRY_REDIRECT_TO", registry_to_buffer, static_cast<DWORD>(std::size(registry_to_buffer))) > 0) {
        registry_to = strip_extended_prefix(registry_to_buffer);
        std::replace(registry_to.begin(), registry_to.end(), L'/', L'\\');
        while (!registry_to.empty() && registry_to.front() == L'\\') {
            registry_to.erase(registry_to.begin());
        }
        while (!registry_to.empty() && registry_to.back() == L'\\') {
            registry_to.pop_back();
        }
    }
    if (registry_to.empty() && file_redirect_enabled) {
        registry_leaf_to = path_leaf(redirect_to);
        if (!registry_leaf_to.empty()) {
            registry_to = L"Software\\CrossGate\\" + registry_leaf_to;
        }
    }
    registry_leaf_from = path_leaf(registry_from);
    registry_leaf_to = path_leaf(registry_to);
    registry_from_cmp = normalize_registry_path(registry_from);
    registry_redirect_enabled = !registry_from_cmp.empty() && !registry_to.empty() && !registry_leaf_from.empty() && !registry_leaf_to.empty();

    log_line(bind_enabled ? L"netbind hook loaded" : L"netbind hook loaded without valid bind ip");
    log_line(L"file redirect rules count=" + std::to_wstring(file_redirect_rules.size()));
    for (const auto &rule : file_redirect_rules) {
        log_line(L"  file redirect rule: " + rule.from + L" -> " + rule.to);
    }
    if (registry_redirect_enabled) {
        log_line(L"registry redirect enabled from " + registry_from + L" to " + registry_to);
        log_line(L"registry leaf from=" + registry_leaf_from + L" to=" + registry_leaf_to);
    } else {
        log_line(L"WARNING: registry redirect is DISABLED");
        log_line(L"  registry_from=" + registry_from);
        log_line(L"  registry_to=" + registry_to);
        log_line(L"  registry_leaf_from=" + registry_leaf_from);
        log_line(L"  registry_leaf_to=" + registry_leaf_to);
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        self_module = module;
        DisableThreadLibraryCalls(module);
        initialize();
    } else if (reason == DLL_PROCESS_DETACH) {
        DeleteCriticalSection(&log_lock);
    }
    return TRUE;
}
