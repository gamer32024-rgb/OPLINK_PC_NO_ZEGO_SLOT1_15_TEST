#include <windows.h>
#include <tlhelp32.h>

#include <cstdint>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct RedirectPair {
    std::wstring from;
    std::wstring to;
};

std::wstring quote_arg(const std::wstring &value) {
    std::wstring out = L"\"";
    unsigned backslashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') {
            ++backslashes;
            continue;
        }
        if (ch == L'"') {
            out.append(backslashes * 2 + 1, L'\\');
            out.push_back(ch);
            backslashes = 0;
            continue;
        }
        if (backslashes) {
            out.append(backslashes, L'\\');
            backslashes = 0;
        }
        out.push_back(ch);
    }
    if (backslashes) {
        out.append(backslashes * 2, L'\\');
    }
    out.push_back(L'"');
    return out;
}

std::wstring join_command_line(const std::vector<std::wstring> &args) {
    std::wstring command;
    for (const auto &arg : args) {
        if (!command.empty()) {
            command.push_back(L' ');
        }
        command += quote_arg(arg);
    }
    return command;
}

std::wstring trim_trailing_separators(std::wstring value) {
    while (!value.empty() && (value.back() == L'\\' || value.back() == L'/')) {
        value.pop_back();
    }
    return value;
}

std::wstring path_leaf(std::wstring value) {
    value = trim_trailing_separators(value);
    const size_t pos = value.find_last_of(L"\\/");
    return (pos == std::wstring::npos) ? value : value.substr(pos + 1);
}

std::vector<wchar_t> build_environment(
    const std::wstring &bind_ip,
    const std::wstring &log_path,
    const std::vector<RedirectPair> &redirect_pairs,
    const std::wstring &ready_event_name) {
    LPWCH env = GetEnvironmentStringsW();
    std::vector<wchar_t> block;
    if (env) {
        for (LPWCH p = env; *p != L'\0';) {
            size_t len = wcslen(p);
            block.insert(block.end(), p, p + len + 1);
            p += len + 1;
        }
        FreeEnvironmentStringsW(env);
    }

    auto append_var = [&](const std::wstring &key, const std::wstring &value) {
        std::wstring entry = key + L"=" + value;
        block.insert(block.end(), entry.begin(), entry.end());
        block.push_back(L'\0');
    };

    append_var(L"GUI_TEST_PC_BIND_IP", bind_ip);
    append_var(L"GUI_TEST_PC_NETBIND_LOG", log_path);
    if (!redirect_pairs.empty()) {
        append_var(L"GUI_TEST_PC_FILE_REDIRECT_FROM", redirect_pairs[0].from);
        append_var(L"GUI_TEST_PC_FILE_REDIRECT_TO", redirect_pairs[0].to);
        append_var(L"GUI_TEST_PC_FILE_REDIRECT_COUNT", std::to_wstring(redirect_pairs.size()));
        for (size_t i = 0; i < redirect_pairs.size(); ++i) {
            std::wstring suffix = std::to_wstring(i + 1);
            append_var(L"GUI_TEST_PC_FILE_REDIRECT_FROM_" + suffix, redirect_pairs[i].from);
            append_var(L"GUI_TEST_PC_FILE_REDIRECT_TO_" + suffix, redirect_pairs[i].to);
        }
        const std::wstring product_leaf = path_leaf(redirect_pairs[0].to);
        if (!product_leaf.empty()) {
            append_var(L"GUI_TEST_PC_REGISTRY_REDIRECT_FROM", L"Software\\CrossGate\\StarCG");
            append_var(L"GUI_TEST_PC_REGISTRY_REDIRECT_TO", L"Software\\CrossGate\\" + product_leaf);
        }
    }
    if (!ready_event_name.empty()) {
        append_var(L"GUI_TEST_PC_HOOK_READY_EVENT", ready_event_name);
    }
    block.push_back(L'\0');
    return block;
}

std::filesystem::path module_dir() {
    std::wstring buffer(MAX_PATH, L'\0');
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    while (length == buffer.size() && GetLastError() == ERROR_INSUFFICIENT_BUFFER) {
        buffer.resize(buffer.size() * 2);
        length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    }
    buffer.resize(length);
    return std::filesystem::path(buffer).parent_path();
}

bool inject_dll(HANDLE process, const std::wstring &dll_path, HMODULE &remote_module, std::wstring &error) {
    const size_t bytes = (dll_path.size() + 1) * sizeof(wchar_t);
    void *remote = VirtualAllocEx(process, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) {
        error = L"VirtualAllocEx failed";
        return false;
    }

    SIZE_T written = 0;
    if (!WriteProcessMemory(process, remote, dll_path.c_str(), bytes, &written) || written != bytes) {
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        error = L"WriteProcessMemory failed";
        return false;
    }

    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    auto load_library = reinterpret_cast<LPTHREAD_START_ROUTINE>(GetProcAddress(kernel32, "LoadLibraryW"));
    if (!load_library) {
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        error = L"LoadLibraryW not found";
        return false;
    }

    HANDLE thread = CreateRemoteThread(process, nullptr, 0, load_library, remote, 0, nullptr);
    if (!thread) {
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        error = L"CreateRemoteThread failed";
        return false;
    }

    DWORD wait = WaitForSingleObject(thread, 10000);
    DWORD exit_code = 0;
    GetExitCodeThread(thread, &exit_code);
    CloseHandle(thread);
    VirtualFreeEx(process, remote, 0, MEM_RELEASE);

    if (wait != WAIT_OBJECT_0 || exit_code == 0) {
        error = L"remote LoadLibraryW failed";
        return false;
    }
    remote_module = reinterpret_cast<HMODULE>(static_cast<uintptr_t>(exit_code));
    return true;
}

HMODULE find_remote_module(DWORD process_id, const std::wstring &dll_path) {
    std::wstring dll_name = std::filesystem::path(dll_path).filename().wstring();
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, process_id);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return nullptr;
    }

    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    HMODULE result = nullptr;
    if (Module32FirstW(snapshot, &entry)) {
        do {
            if (_wcsicmp(entry.szModule, dll_name.c_str()) == 0 || _wcsicmp(entry.szExePath, dll_path.c_str()) == 0) {
                result = reinterpret_cast<HMODULE>(entry.modBaseAddr);
                break;
            }
            entry.dwSize = sizeof(entry);
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return result;
}

bool run_remote_export(HANDLE process, HMODULE remote_module, const std::wstring &dll_path, const char *export_name, std::wstring &error) {
    HMODULE local_module = LoadLibraryW(dll_path.c_str());
    if (!local_module) {
        error = L"local LoadLibraryW failed for export lookup";
        return false;
    }

    FARPROC local_proc = GetProcAddress(local_module, export_name);
    if (!local_proc) {
        FreeLibrary(local_module);
        error = L"export not found";
        return false;
    }

    auto offset = reinterpret_cast<std::uintptr_t>(local_proc) - reinterpret_cast<std::uintptr_t>(local_module);
    auto remote_proc = reinterpret_cast<LPTHREAD_START_ROUTINE>(reinterpret_cast<std::uintptr_t>(remote_module) + offset);
    HANDLE thread = CreateRemoteThread(process, nullptr, 0, remote_proc, nullptr, 0, nullptr);
    FreeLibrary(local_module);
    if (!thread) {
        error = L"CreateRemoteThread for export failed";
        return false;
    }

    DWORD wait = WaitForSingleObject(thread, 10000);
    DWORD exit_code = 0;
    GetExitCodeThread(thread, &exit_code);
    CloseHandle(thread);
    if (wait != WAIT_OBJECT_0) {
        error = L"remote export wait failed";
        return false;
    }
    return true;
}

void usage() {
    std::wcerr << L"Usage: GuiTestNetBindLauncher.exe --bind-ip IP [--cwd DIR] [--log PATH] [--dll PATH] [--redirect-pair FROM TO ...] -- EXE [ARGS...]\n";
}

}  // namespace

int wmain(int argc, wchar_t **argv) {
    std::wstring bind_ip;
    std::wstring cwd;
    std::wstring log_path;
    std::wstring dll_path;
    std::wstring redirect_from;
    std::wstring redirect_to;
    std::vector<RedirectPair> redirect_pairs;
    std::vector<std::wstring> child_args;

    for (int i = 1; i < argc; ++i) {
        std::wstring arg = argv[i];
        if (arg == L"--") {
            for (++i; i < argc; ++i) {
                child_args.emplace_back(argv[i]);
            }
            break;
        }
        if (arg == L"--bind-ip" && i + 1 < argc) {
            bind_ip = argv[++i];
        } else if (arg == L"--cwd" && i + 1 < argc) {
            cwd = argv[++i];
        } else if (arg == L"--log" && i + 1 < argc) {
            log_path = argv[++i];
        } else if (arg == L"--dll" && i + 1 < argc) {
            dll_path = argv[++i];
        } else if (arg == L"--redirect-from" && i + 1 < argc) {
            redirect_from = argv[++i];
        } else if (arg == L"--redirect-to" && i + 1 < argc) {
            redirect_to = argv[++i];
        } else if (arg == L"--redirect-pair" && i + 2 < argc) {
            RedirectPair pair{argv[++i], argv[++i]};
            redirect_pairs.push_back(pair);
        } else {
            usage();
            return 2;
        }
    }

    if (bind_ip.empty() || child_args.empty()) {
        usage();
        return 2;
    }
    if (redirect_from.empty() != redirect_to.empty()) {
        usage();
        return 2;
    }
    if (!redirect_from.empty() && !redirect_to.empty()) {
        redirect_pairs.insert(redirect_pairs.begin(), RedirectPair{redirect_from, redirect_to});
    }
    for (const auto &pair : redirect_pairs) {
        if (pair.from.empty() || pair.to.empty()) {
            usage();
            return 2;
        }
    }

    if (cwd.empty()) {
        cwd = std::filesystem::path(child_args[0]).parent_path().wstring();
    }
    if (log_path.empty()) {
        log_path = (module_dir() / L"GuiTestNetBindHook.log").wstring();
    }
    if (dll_path.empty()) {
        dll_path = (module_dir() / L"GuiTestNetBindHook64.dll").wstring();
    }
    if (!std::filesystem::exists(dll_path)) {
        std::wcerr << L"netbind hook dll not found: " << dll_path << L"\n";
        return 3;
    }

    std::wstring command = join_command_line(child_args);
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');
    std::wstring ready_event_name = L"Local\\GuiTestNetBindHookReady_" + std::to_wstring(GetCurrentProcessId()) + L"_" + std::to_wstring(GetTickCount64());
    HANDLE ready_event = CreateEventW(nullptr, TRUE, FALSE, ready_event_name.c_str());
    if (!ready_event) {
        std::wcerr << L"CreateEventW failed: " << GetLastError() << L"\n";
        return 6;
    }
    auto env = build_environment(bind_ip, log_path, redirect_pairs, ready_event_name);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    BOOL ok = CreateProcessW(
        child_args[0].c_str(),
        mutable_command.data(),
        nullptr,
        nullptr,
        FALSE,
        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
        env.data(),
        cwd.empty() ? nullptr : cwd.c_str(),
        &si,
        &pi);

    if (!ok) {
        std::wcerr << L"CreateProcessW failed: " << GetLastError() << L"\n";
        return 4;
    }

    std::wstring error;
    HMODULE remote_module = nullptr;
    if (!inject_dll(pi.hProcess, dll_path, remote_module, error)) {
        TerminateProcess(pi.hProcess, 100);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        CloseHandle(ready_event);
        std::wcerr << L"netbind injection failed: " << error << L" (" << GetLastError() << L")\n";
        return 5;
    }
    HMODULE enumerated_module = find_remote_module(pi.dwProcessId, dll_path);
    if (!enumerated_module) {
        TerminateProcess(pi.hProcess, 100);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        CloseHandle(ready_event);
        std::wcerr << L"netbind injection failed: injected dll module not found\n";
        return 5;
    }
    remote_module = enumerated_module;

    if (!run_remote_export(pi.hProcess, remote_module, dll_path, "GuiTestNetBindPatchNow", error)) {
        TerminateProcess(pi.hProcess, 101);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        CloseHandle(ready_event);
        std::wcerr << L"netbind patch init failed: " << error << L" (" << GetLastError() << L")\n";
        return 7;
    }

    WaitForSingleObject(ready_event, 3000);
    CloseHandle(ready_event);
    ResumeThread(pi.hThread);
    std::wcout << L"started pid=" << pi.dwProcessId << L" bind_ip=" << bind_ip << L"\n";
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
