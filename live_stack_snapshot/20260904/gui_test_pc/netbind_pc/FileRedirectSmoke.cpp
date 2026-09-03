#include <windows.h>

#include <cstdio>
#include <iostream>
#include <string>

namespace {

void usage() {
    std::wcerr
        << L"Usage:\n"
        << L"  FileRedirectSmoke.exe PATH\n"
        << L"  FileRedirectSmoke.exe --ansi PATH\n"
        << L"  FileRedirectSmoke.exe --fopen PATH\n"
        << L"  FileRedirectSmoke.exe --ntcreate PATH\n"
        << L"  FileRedirectSmoke.exe --ini PATH\n"
        << L"  FileRedirectSmoke.exe --late-load DLL PATH\n"
        << L"  FileRedirectSmoke.exe --reg SUBKEY VALUE_NAME\n";
}

bool narrow_ansi(const wchar_t *input, std::string &output) {
    int needed = WideCharToMultiByte(CP_ACP, 0, input, -1, nullptr, 0, nullptr, nullptr);
    if (needed <= 0) {
        return false;
    }
    output.assign(static_cast<size_t>(needed), '\0');
    int written = WideCharToMultiByte(CP_ACP, 0, input, -1, output.data(), needed, nullptr, nullptr);
    if (written <= 0) {
        output.clear();
        return false;
    }
    if (!output.empty() && output.back() == '\0') {
        output.pop_back();
    }
    return true;
}

int write_with_create_file(wchar_t *path, bool ansi) {
    HANDLE file = INVALID_HANDLE_VALUE;

    if (ansi) {
        std::string narrow;
        if (!narrow_ansi(path, narrow)) {
            std::wcerr << L"WideCharToMultiByte failed: " << GetLastError() << L"\n";
            return 5;
        }
        file = CreateFileA(
            narrow.c_str(),
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
    } else {
        file = CreateFileW(
            path,
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
    }
    if (file == INVALID_HANDLE_VALUE) {
        std::wcerr << (ansi ? L"CreateFileA failed: " : L"CreateFileW failed: ") << GetLastError() << L"\n";
        return 3;
    }

    const char data[] = "redirected\n";
    DWORD written = 0;
    BOOL ok = WriteFile(file, data, static_cast<DWORD>(sizeof(data) - 1), &written, nullptr);
    CloseHandle(file);
    if (!ok) {
        std::wcerr << L"WriteFile failed: " << GetLastError() << L"\n";
        return 4;
    }

    std::wcout << L"wrote " << written << L" bytes\n";
    return 0;
}

int write_with_fopen(wchar_t *path) {
    std::string narrow;
    if (!narrow_ansi(path, narrow)) {
        std::wcerr << L"WideCharToMultiByte failed: " << GetLastError() << L"\n";
        return 5;
    }

    FILE *file = std::fopen(narrow.c_str(), "wb");
    if (!file) {
        std::wcerr << L"fopen failed\n";
        return 6;
    }
    const char data[] = "redirected\n";
    size_t written = std::fwrite(data, 1, sizeof(data) - 1, file);
    std::fclose(file);
    if (written != sizeof(data) - 1) {
        std::wcerr << L"fwrite failed\n";
        return 7;
    }

    std::wcout << L"fopen wrote " << written << L" bytes\n";
    return 0;
}

using SmokeNtStatus = LONG;

struct SmokeUnicodeString {
    USHORT Length;
    USHORT MaximumLength;
    PWSTR Buffer;
};

struct SmokeObjectAttributes {
    ULONG Length;
    HANDLE RootDirectory;
    SmokeUnicodeString *ObjectName;
    ULONG Attributes;
    PVOID SecurityDescriptor;
    PVOID SecurityQualityOfService;
};

struct SmokeIoStatusBlock {
    union {
        SmokeNtStatus Status;
        PVOID Pointer;
    };
    ULONG_PTR Information;
};

using SmokeNtCreateFileFn = SmokeNtStatus(NTAPI *)(PHANDLE, ACCESS_MASK, SmokeObjectAttributes *, SmokeIoStatusBlock *, PLARGE_INTEGER, ULONG, ULONG, ULONG, ULONG, PVOID, ULONG);

int write_with_ntcreate(wchar_t *path) {
    wchar_t full_path[MAX_PATH * 4]{};
    DWORD length = GetFullPathNameW(path, static_cast<DWORD>(std::size(full_path)), full_path, nullptr);
    if (length == 0 || length >= std::size(full_path)) {
        std::wcerr << L"GetFullPathNameW failed: " << GetLastError() << L"\n";
        return 12;
    }

    std::wstring nt_path = LR"(\??\)" + std::wstring(full_path);
    SmokeUnicodeString object_name{};
    object_name.Length = static_cast<USHORT>(nt_path.size() * sizeof(wchar_t));
    object_name.MaximumLength = static_cast<USHORT>((nt_path.size() + 1) * sizeof(wchar_t));
    object_name.Buffer = nt_path.data();

    SmokeObjectAttributes attributes{};
    attributes.Length = sizeof(attributes);
    attributes.ObjectName = &object_name;
    attributes.Attributes = 0x40;  // OBJ_CASE_INSENSITIVE

    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    auto nt_create_file = reinterpret_cast<SmokeNtCreateFileFn>(GetProcAddress(ntdll, "NtCreateFile"));
    if (!nt_create_file) {
        std::wcerr << L"GetProcAddress NtCreateFile failed\n";
        return 13;
    }

    HANDLE file = INVALID_HANDLE_VALUE;
    SmokeIoStatusBlock io_status{};
    SmokeNtStatus status = nt_create_file(
        &file,
        GENERIC_WRITE | SYNCHRONIZE,
        &attributes,
        &io_status,
        nullptr,
        FILE_ATTRIBUTE_NORMAL,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        5,       // FILE_OVERWRITE_IF
        0x20 | 0x40,  // FILE_SYNCHRONOUS_IO_NONALERT | FILE_NON_DIRECTORY_FILE
        nullptr,
        0);
    if (status < 0 || file == INVALID_HANDLE_VALUE) {
        std::wcerr << L"NtCreateFile failed: 0x" << std::hex << status << L"\n";
        return 14;
    }

    const char data[] = "nt-redirected\n";
    DWORD written = 0;
    BOOL ok = WriteFile(file, data, static_cast<DWORD>(sizeof(data) - 1), &written, nullptr);
    CloseHandle(file);
    if (!ok) {
        std::wcerr << L"WriteFile failed after NtCreateFile: " << GetLastError() << L"\n";
        return 15;
    }

    std::wcout << L"ntcreate wrote " << written << L" bytes\n";
    return 0;
}

int write_with_ini_api(wchar_t *path) {
    if (!WritePrivateProfileStringW(L"System", L"LastQuitAccount", L"+85260132548", path)) {
        std::wcerr << L"WritePrivateProfileStringW failed: " << GetLastError() << L"\n";
        return 16;
    }

    wchar_t buffer[128]{};
    DWORD read = GetPrivateProfileStringW(L"System", L"LastQuitAccount", L"", buffer, static_cast<DWORD>(std::size(buffer)), path);
    if (read == 0 || wcscmp(buffer, L"+85260132548") != 0) {
        std::wcerr << L"GetPrivateProfileStringW unexpected value: " << buffer << L"\n";
        return 17;
    }

    std::wcout << L"ini api wrote and read " << buffer << L"\n";
    return 0;
}

int write_registry_value(const wchar_t *subkey, const wchar_t *value_name) {
    HKEY key = nullptr;
    DWORD disposition = 0;
    LSTATUS status = RegCreateKeyExW(
        HKEY_CURRENT_USER,
        subkey,
        0,
        nullptr,
        0,
        KEY_SET_VALUE,
        nullptr,
        &key,
        &disposition);
    if (status != ERROR_SUCCESS) {
        std::wcerr << L"RegCreateKeyExW failed: " << status << L"\n";
        return 8;
    }

    DWORD data = 1234;
    status = RegSetValueExW(key, value_name, 0, REG_DWORD, reinterpret_cast<const BYTE *>(&data), sizeof(data));
    RegCloseKey(key);
    if (status != ERROR_SUCCESS) {
        std::wcerr << L"RegSetValueExW failed: " << status << L"\n";
        return 9;
    }

    std::wcout << L"registry wrote value " << value_name << L"\n";
    return 0;
}

int write_with_late_loaded_dll(const wchar_t *dll_path, const wchar_t *path) {
    HMODULE module = LoadLibraryW(dll_path);
    if (!module) {
        std::wcerr << L"LoadLibraryW failed: " << GetLastError() << L"\n";
        return 10;
    }

    using write_late_file_fn = int(__stdcall *)(const wchar_t *);
    auto write_late_file = reinterpret_cast<write_late_file_fn>(GetProcAddress(module, "WriteLateFile"));
    if (!write_late_file) {
        std::wcerr << L"GetProcAddress WriteLateFile failed: " << GetLastError() << L"\n";
        FreeLibrary(module);
        return 11;
    }

    int rc = write_late_file(path);
    FreeLibrary(module);
    if (rc != 0) {
        std::wcerr << L"WriteLateFile failed rc=" << rc << L"\n";
        return rc;
    }

    std::wcout << L"late-loaded dll wrote file\n";
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t **argv) {
    if (argc < 2) {
        usage();
        return 2;
    }

    if (wcscmp(argv[1], L"--ansi") == 0) {
        if (argc < 3) {
            usage();
            return 2;
        }
        return write_with_create_file(argv[2], true);
    }

    if (wcscmp(argv[1], L"--fopen") == 0) {
        if (argc < 3) {
            usage();
            return 2;
        }
        return write_with_fopen(argv[2]);
    }

    if (wcscmp(argv[1], L"--ntcreate") == 0) {
        if (argc < 3) {
            usage();
            return 2;
        }
        return write_with_ntcreate(argv[2]);
    }

    if (wcscmp(argv[1], L"--ini") == 0) {
        if (argc < 3) {
            usage();
            return 2;
        }
        return write_with_ini_api(argv[2]);
    }

    if (wcscmp(argv[1], L"--late-load") == 0) {
        if (argc < 4) {
            usage();
            return 2;
        }
        return write_with_late_loaded_dll(argv[2], argv[3]);
    }

    if (wcscmp(argv[1], L"--reg") == 0) {
        if (argc < 4) {
            usage();
            return 2;
        }
        return write_registry_value(argv[2], argv[3]);
    }

    return write_with_create_file(argv[1], false);
}
