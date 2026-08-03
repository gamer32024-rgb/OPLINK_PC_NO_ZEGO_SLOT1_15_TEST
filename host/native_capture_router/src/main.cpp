#include <windows.h>
#include <d3d11.h>
#include <dwmapi.h>
#include <dxgi.h>
#include <fcntl.h>
#include <io.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>

#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/base.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace std::chrono_literals;
using winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool;
using winrt::Windows::Graphics::Capture::GraphicsCaptureItem;
using winrt::Windows::Graphics::Capture::GraphicsCaptureSession;
using winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;
using winrt::Windows::Graphics::DirectX::DirectXPixelFormat;

namespace {

constexpr int kMinimumSlot = 1;
constexpr int kMaximumSlot = 15;
constexpr size_t kStagingTextureCount = 3;
constexpr int kGeometryTolerancePixels = 8;

std::mutex g_event_mutex;

int64_t unix_time_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

std::string json_escape(std::string_view value) {
    std::ostringstream output;
    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                output << "\\\"";
                break;
            case '\\':
                output << "\\\\";
                break;
            case '\b':
                output << "\\b";
                break;
            case '\f':
                output << "\\f";
                break;
            case '\n':
                output << "\\n";
                break;
            case '\r':
                output << "\\r";
                break;
            case '\t':
                output << "\\t";
                break;
            default:
                if (character < 0x20) {
                    output << "\\u"
                           << std::hex
                           << std::setw(4)
                           << std::setfill('0')
                           << static_cast<int>(character)
                           << std::dec;
                } else {
                    output << static_cast<char>(character);
                }
                break;
        }
    }
    return output.str();
}

void log_event(const std::string& event, const std::string& fields = {}) {
    std::ostringstream line;
    line << "{\"event\":\"" << json_escape(event) << "\"";
    if (!fields.empty()) {
        line << "," << fields;
    }
    line << "}";

    std::scoped_lock lock(g_event_mutex);
    std::cerr << line.str() << '\n';
    std::cerr.flush();
}

std::string hwnd_string(HWND hwnd) {
    std::ostringstream output;
    output << "0x" << std::hex << reinterpret_cast<uintptr_t>(hwnd);
    return output.str();
}

class RouterError final : public std::runtime_error {
public:
    RouterError(std::string code, std::string message, bool recoverable = true)
        : std::runtime_error(std::move(message)),
          code_(std::move(code)),
          recoverable_(recoverable) {}

    const std::string& code() const noexcept {
        return code_;
    }

    bool recoverable() const noexcept {
        return recoverable_;
    }

private:
    std::string code_;
    bool recoverable_;
};

int parse_int_arg(char** argv, int argc, const std::string& name, int fallback) {
    for (int index = 1; index + 1 < argc; ++index) {
        if (name == argv[index]) {
            size_t consumed = 0;
            const int value = std::stoi(argv[index + 1], &consumed, 10);
            if (consumed != std::strlen(argv[index + 1])) {
                throw RouterError("INVALID_ARGUMENT", "invalid numeric command-line argument", false);
            }
            return value;
        }
    }
    return fallback;
}

struct ParsedCommand {
    std::string verb;
    std::unordered_map<std::string, std::string> fields;
};

ParsedCommand parse_command(const std::string& line) {
    std::istringstream input(line);
    ParsedCommand command;
    input >> command.verb;
    if (command.verb.empty()) {
        throw RouterError("EMPTY_COMMAND", "empty command");
    }

    std::string token;
    while (input >> token) {
        const auto separator = token.find('=');
        if (separator == std::string::npos || separator == 0 || separator + 1 >= token.size()) {
            throw RouterError("INVALID_COMMAND_FIELD", "command fields must use key=value");
        }
        const std::string key = token.substr(0, separator);
        const std::string value = token.substr(separator + 1);
        if (!command.fields.emplace(key, value).second) {
            throw RouterError("DUPLICATE_COMMAND_FIELD", "duplicate command field");
        }
    }
    return command;
}

const std::string& required_field(const ParsedCommand& command, const std::string& name) {
    const auto found = command.fields.find(name);
    if (found == command.fields.end()) {
        throw RouterError("MISSING_COMMAND_FIELD", "missing command field: " + name);
    }
    return found->second;
}

uint64_t parse_unsigned(const std::string& value, const std::string& field, int base = 10) {
    if (value.empty() || value.front() == '-') {
        throw RouterError("INVALID_COMMAND_FIELD", "invalid unsigned field: " + field);
    }

    const char* first = value.data();
    const char* last = first + value.size();
    if (base == 16 && value.size() > 2 && value[0] == '0' &&
        (value[1] == 'x' || value[1] == 'X')) {
        first += 2;
    }

    uint64_t result = 0;
    const auto parsed = std::from_chars(first, last, result, base);
    if (parsed.ec != std::errc{} || parsed.ptr != last) {
        throw RouterError("INVALID_COMMAND_FIELD", "invalid numeric field: " + field);
    }
    return result;
}

int parse_positive_int(const ParsedCommand& command, const std::string& name) {
    const uint64_t value = parse_unsigned(required_field(command, name), name);
    if (value == 0 || value > static_cast<uint64_t>(std::numeric_limits<int>::max())) {
        throw RouterError("INVALID_COMMAND_FIELD", "field is outside valid range: " + name);
    }
    return static_cast<int>(value);
}

uint64_t optional_generation(const ParsedCommand& command) {
    const auto found = command.fields.find("generation");
    if (found == command.fields.end()) {
        return 0;
    }
    return parse_unsigned(found->second, "generation");
}

int optional_slot(const ParsedCommand& command) {
    const auto found = command.fields.find("slot");
    if (found == command.fields.end()) {
        return 0;
    }
    const uint64_t slot = parse_unsigned(found->second, "slot");
    return slot <= static_cast<uint64_t>(std::numeric_limits<int>::max())
        ? static_cast<int>(slot)
        : 0;
}

void log_error(
    uint64_t generation,
    int slot,
    const std::string& code,
    bool recoverable,
    const std::string& message) {
    std::ostringstream fields;
    fields << "\"generation\":" << generation
           << ",\"slot\":" << slot
           << ",\"code\":\"" << json_escape(code) << "\""
           << ",\"recoverable\":" << (recoverable ? "true" : "false")
           << ",\"message\":\"" << json_escape(message) << "\"";
    log_event("error", fields.str());
}

struct WindowGeometry {
    RECT window_rect{};
    RECT extended_rect{};
    POINT client_origin{};
    int client_width = 0;
    int client_height = 0;
    UINT dpi = 96;
    bool has_extended_rect = false;
};

struct CropGeometry {
    UINT x = 0;
    UINT y = 0;
    UINT width = 0;
    UINT height = 0;
    int inferred_client_width = 0;
    int inferred_client_height = 0;
    const char* bounds_source = "window";
};

struct CaptureSource {
    uint64_t generation = 0;
    int slot = 0;
    HWND hwnd = nullptr;
    int64_t switch_started_at_ms = 0;
    std::chrono::steady_clock::time_point switch_started_at{};
    std::chrono::steady_clock::time_point next_capture_at{};
    WindowGeometry geometry{};
    winrt::Windows::Graphics::SizeInt32 item_size{};
    winrt::Windows::Graphics::SizeInt32 pool_size{};
    GraphicsCaptureItem item{nullptr};
    Direct3D11CaptureFramePool frame_pool{nullptr};
    GraphicsCaptureSession session{nullptr};
    winrt::event_token frame_token{};
    winrt::event_token closed_token{};
    bool frame_subscribed = false;
    bool closed_subscribed = false;
    bool has_accepted_frame = false;
    std::atomic<bool> closed{false};
    std::mutex processing_mutex;
};

struct FramePacket {
    std::vector<uint8_t> bytes;
    uint64_t generation = 0;
    int slot = 0;
    int source_width = 0;
    int source_height = 0;
    CropGeometry crop{};
    int64_t switch_started_at_ms = 0;
    std::chrono::steady_clock::time_point switch_started_at{};
};

class CaptureRouter;

class CallbackGate {
public:
    explicit CallbackGate(CaptureRouter* router) : router_(router) {}

    bool enter(CaptureRouter*& router) {
        std::scoped_lock lock(mutex_);
        if (closing_ || router_ == nullptr) {
            return false;
        }
        ++active_callbacks_;
        router = router_;
        return true;
    }

    void leave() {
        std::scoped_lock lock(mutex_);
        if (active_callbacks_ > 0) {
            --active_callbacks_;
        }
        if (closing_ && active_callbacks_ == 0) {
            condition_.notify_all();
        }
    }

    void close_and_wait() {
        std::unique_lock lock(mutex_);
        closing_ = true;
        router_ = nullptr;
        condition_.wait(lock, [this]() { return active_callbacks_ == 0; });
    }

private:
    std::mutex mutex_;
    std::condition_variable condition_;
    CaptureRouter* router_ = nullptr;
    size_t active_callbacks_ = 0;
    bool closing_ = false;
};

class CallbackLease {
public:
    explicit CallbackLease(std::shared_ptr<CallbackGate> gate)
        : gate_(std::move(gate)) {}

    ~CallbackLease() {
        gate_->leave();
    }

private:
    std::shared_ptr<CallbackGate> gate_;
};

class MappedTexture {
public:
    MappedTexture(ID3D11DeviceContext* context, ID3D11Texture2D* texture)
        : context_(context), texture_(texture) {
        winrt::check_hresult(context_->Map(
            texture_,
            0,
            D3D11_MAP_READ,
            0,
            &mapped_));
        active_ = true;
    }

    ~MappedTexture() {
        if (active_) {
            context_->Unmap(texture_, 0);
        }
    }

    const D3D11_MAPPED_SUBRESOURCE& value() const noexcept {
        return mapped_;
    }

private:
    ID3D11DeviceContext* context_;
    ID3D11Texture2D* texture_;
    D3D11_MAPPED_SUBRESOURCE mapped_{};
    bool active_ = false;
};

class CaptureRouter {
public:
    CaptureRouter(int width, int height, int fps)
        : width_(width),
          height_(height),
          fps_(fps),
          black_frame_(static_cast<size_t>(width) * height * 4, 0),
          callback_gate_(std::make_shared<CallbackGate>(this)) {
        if (!GraphicsCaptureSession::IsSupported()) {
            throw RouterError(
                "WGC_UNSUPPORTED",
                "Windows Graphics Capture is not supported",
                false);
        }
        create_d3d_device();
        create_staging_textures();
    }

    ~CaptureRouter() {
        stop();
        callback_gate_->close_and_wait();
    }

    void start_output(int width, int height, int fps, const std::string& format) {
        if (width != width_ || height != height_ || fps != fps_ || format != "bgra") {
            throw RouterError(
                "START_PROFILE_MISMATCH",
                "START profile does not match router command-line profile");
        }

        {
            std::scoped_lock lock(output_start_mutex_);
            if (output_started_) {
                throw RouterError("ALREADY_STARTED", "router output is already started");
            }
            output_started_ = true;
        }
        output_start_condition_.notify_all();

        std::ostringstream fields;
        fields << "\"width\":" << width_
               << ",\"height\":" << height_
               << ",\"fps\":" << fps_
               << ",\"format\":\"bgra\"";
        log_event("started", fields.str());
    }

    void switch_to(uint64_t generation, int slot, HWND hwnd) {
        if (generation == 0) {
            throw RouterError("INVALID_GENERATION", "generation must be greater than zero");
        }
        if (slot < kMinimumSlot || slot > kMaximumSlot) {
            throw RouterError("INVALID_SLOT", "slot is outside 1..15");
        }

        {
            std::scoped_lock start_lock(output_start_mutex_);
            if (!output_started_) {
                throw RouterError("NOT_STARTED", "START must be sent before SWITCH");
            }
        }

        std::shared_ptr<CaptureSource> replaced_pending;
        {
            std::scoped_lock lock(capture_mutex_);
            if (generation <= last_requested_generation_) {
                throw RouterError(
                    "STALE_GENERATION",
                    "generation must be strictly monotonic");
            }
            last_requested_generation_ = generation;

            replaced_pending = pending_source_;
            pending_source_.reset();
        }
        close_source(replaced_pending);

        auto source = create_source(generation, slot, hwnd);
        {
            std::scoped_lock lock(capture_mutex_);
            pending_source_ = source;
        }
        try {
            {
                std::scoped_lock source_lock(source->processing_mutex);
                if (stop_requested_.load() || source->closed.load()) {
                    throw RouterError(
                        "STOPPING",
                        "router stopped while starting the capture source",
                        false);
                }
                source->session.StartCapture();
            }
        } catch (...) {
            {
                std::scoped_lock lock(capture_mutex_);
                if (pending_source_ == source) {
                    pending_source_.reset();
                }
            }
            close_source(source);
            throw;
        }

        if (stop_requested_.load()) {
            {
                std::scoped_lock lock(capture_mutex_);
                if (pending_source_ == source) {
                    pending_source_.reset();
                }
            }
            close_source(source);
            throw RouterError(
                "STOPPING",
                "router stopped while starting the capture source",
                false);
        }

        std::ostringstream fields;
        fields << "\"generation\":" << generation
               << ",\"slot\":" << slot
               << ",\"hwnd\":\"" << hwnd_string(hwnd) << "\""
               << ",\"at_ms\":" << source->switch_started_at_ms
               << ",\"item_width\":" << source->item_size.Width
               << ",\"item_height\":" << source->item_size.Height
               << ",\"client_width\":" << source->geometry.client_width
               << ",\"client_height\":" << source->geometry.client_height
               << ",\"dpi\":" << source->geometry.dpi;
        log_event("switch_started", fields.str());
    }

    void output_loop() {
        register_output_thread();
        const auto cleanup = [this]() { unregister_output_thread(); };

        try {
            {
                std::unique_lock lock(output_start_mutex_);
                output_start_condition_.wait(lock, [this]() {
                    return output_started_ || stop_requested_.load();
                });
            }
            if (stop_requested_.load()) {
                cleanup();
                return;
            }

            const auto frame_period = std::chrono::duration<double>(1.0 / fps_);
            auto next_frame = std::chrono::steady_clock::now();
            auto stats_started = next_frame;
            uint64_t stats_frames = 0;
            uint64_t output_frame_index = 0;
            uint64_t last_reported_generation = 0;
            HANDLE output = GetStdHandle(STD_OUTPUT_HANDLE);
            if (output == nullptr || output == INVALID_HANDLE_VALUE) {
                throw RouterError("STDOUT_UNAVAILABLE", "stdout handle is unavailable", false);
            }

            while (!stop_requested_.load()) {
                std::shared_ptr<const FramePacket> packet;
                {
                    std::scoped_lock lock(frame_mutex_);
                    packet = latest_frame_;
                }

                const auto* data = packet ? packet->bytes.data() : black_frame_.data();
                const size_t size = packet ? packet->bytes.size() : black_frame_.size();
                DWORD write_error = ERROR_SUCCESS;
                if (!write_all(output, data, size, write_error)) {
                    if (!stop_requested_.load()) {
                        log_error(
                            packet ? packet->generation : 0,
                            packet ? packet->slot : 0,
                            "STDOUT_WRITE_FAILED",
                            false,
                            "raw video stdout write failed: " + std::to_string(write_error));
                    }
                    stop();
                    break;
                }

                ++stats_frames;
                ++output_frame_index;
                if (packet && packet->generation != last_reported_generation) {
                    last_reported_generation = packet->generation;
                    delivered_generation_.store(packet->generation);
                    delivered_slot_.store(packet->slot);
                    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                                                std::chrono::steady_clock::now() -
                                                packet->switch_started_at)
                                                .count();
                    std::ostringstream fields;
                    fields << "\"generation\":" << packet->generation
                           << ",\"slot\":" << packet->slot
                           << ",\"source_w\":" << packet->source_width
                           << ",\"source_h\":" << packet->source_height
                           << ",\"output_w\":" << width_
                           << ",\"output_h\":" << height_
                           << ",\"crop_x\":" << packet->crop.x
                           << ",\"crop_y\":" << packet->crop.y
                           << ",\"client_physical_w\":"
                           << packet->crop.inferred_client_width
                           << ",\"client_physical_h\":"
                           << packet->crop.inferred_client_height
                           << ",\"bounds_source\":\""
                           << packet->crop.bounds_source
                           << "\",\"switch_started_at_ms\":"
                           << packet->switch_started_at_ms
                           << ",\"elapsed_ms\":" << elapsed_ms
                           << ",\"stdout_frame_index\":" << output_frame_index
                           << ",\"stdout_written\":true";
                    log_event("first_frame", fields.str());
                }

                const auto now = std::chrono::steady_clock::now();
                if (now - stats_started >= 1s && packet) {
                    const double elapsed = std::chrono::duration<double>(now - stats_started).count();
                    std::ostringstream fields;
                    fields << "\"generation\":" << packet->generation
                           << ",\"slot\":" << packet->slot
                           << ",\"fps\":" << std::fixed << std::setprecision(2)
                           << (static_cast<double>(stats_frames) / elapsed)
                           << ",\"frames_written\":" << stats_frames
                           << ",\"dropped\":0";
                    log_event("frame_stats", fields.str());
                    stats_started = now;
                    stats_frames = 0;
                }

                next_frame += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    frame_period);
                std::this_thread::sleep_until(next_frame);
                const auto after_sleep = std::chrono::steady_clock::now();
                if (next_frame + 500ms < after_sleep) {
                    next_frame = after_sleep;
                }
            }
        } catch (const RouterError& error) {
            log_error(0, 0, error.code(), error.recoverable(), error.what());
            stop();
        } catch (const winrt::hresult_error& error) {
            log_error(
                0,
                0,
                "OUTPUT_HRESULT",
                false,
                "output loop failed with HRESULT " + std::to_string(error.code().value));
            stop();
        } catch (const std::exception& error) {
            log_error(0, 0, "OUTPUT_FAILED", false, error.what());
            stop();
        }
        cleanup();
    }

    void stop() {
        stop_requested_.store(true);
        output_start_condition_.notify_all();
        cancel_output_io();

        std::call_once(capture_stop_once_, [this]() {
            std::shared_ptr<CaptureSource> pending;
            std::shared_ptr<CaptureSource> active;
            {
                std::scoped_lock lock(capture_mutex_);
                pending = std::move(pending_source_);
                active = std::move(active_source_);
            }
            close_source(pending);
            close_source(active);
        });
    }

    void on_frame(
        const Direct3D11CaptureFramePool& sender,
        const std::shared_ptr<CaptureSource>& source) noexcept {
        try {
            std::unique_lock processing_lock(
                source->processing_mutex,
                std::try_to_lock);
            if (!processing_lock.owns_lock()) {
                return;
            }

            bool drain_only = false;
            {
                std::scoped_lock lock(capture_mutex_);
                if (stop_requested_.load() || source->closed.load()) {
                    return;
                }
                if (source != active_source_ && source != pending_source_) {
                    return;
                }
                // The output thread can repeat the last active packet while the
                // pending source gets exclusive priority for its first frame.
                drain_only = source == active_source_ && pending_source_ != nullptr;
            }
            if (drain_only) {
                auto discarded = sender.TryGetNextFrame();
                return;
            }

            if (!IsWindow(source->hwnd)) {
                throw RouterError("HWND_DESTROYED", "capture target no longer exists");
            }
            if (IsIconic(source->hwnd)) {
                throw RouterError("HWND_MINIMIZED", "capture target is minimized");
            }

            const auto now = std::chrono::steady_clock::now();
            if (source->has_accepted_frame && now < source->next_capture_at) {
                auto discarded = sender.TryGetNextFrame();
                return;
            }

            auto frame = sender.TryGetNextFrame();
            if (!frame) {
                return;
            }

            const auto content_size = frame.ContentSize();
            if (content_size.Width <= 0 || content_size.Height <= 0) {
                throw RouterError("EMPTY_FRAME", "capture frame has no content");
            }
            if (content_size.Width != source->pool_size.Width ||
                content_size.Height != source->pool_size.Height) {
                frame.Close();
                source->frame_pool.Recreate(
                    winrt_device_,
                    DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    3,
                    content_size);
                source->pool_size = content_size;

                std::ostringstream fields;
                fields << "\"generation\":" << source->generation
                       << ",\"slot\":" << source->slot
                       << ",\"width\":" << content_size.Width
                       << ",\"height\":" << content_size.Height;
                log_event("source_resized", fields.str());
                return;
            }

            auto access = frame.Surface().as<
                ::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
            winrt::com_ptr<ID3D11Texture2D> source_texture;
            winrt::check_hresult(access->GetInterface(
                __uuidof(ID3D11Texture2D),
                source_texture.put_void()));

            D3D11_TEXTURE2D_DESC source_desc{};
            source_texture->GetDesc(&source_desc);
            const auto crop = resolve_crop(
                source->geometry,
                static_cast<UINT>(content_size.Width),
                static_cast<UINT>(content_size.Height),
                source_desc.Width,
                source_desc.Height);

            auto packet = std::make_shared<FramePacket>();
            packet->bytes.resize(static_cast<size_t>(width_) * height_ * 4);
            packet->generation = source->generation;
            packet->slot = source->slot;
            packet->source_width = content_size.Width;
            packet->source_height = content_size.Height;
            packet->crop = crop;
            packet->switch_started_at_ms = source->switch_started_at_ms;
            packet->switch_started_at = source->switch_started_at;

            {
                std::scoped_lock d3d_lock(d3d_mutex_);
                ID3D11Texture2D* staging =
                    staging_textures_[staging_texture_index_ % staging_textures_.size()].get();
                ++staging_texture_index_;
                D3D11_BOX source_box{
                    crop.x,
                    crop.y,
                    0,
                    crop.x + crop.width,
                    crop.y + crop.height,
                    1};
                d3d_context_->CopySubresourceRegion(
                    staging,
                    0,
                    0,
                    0,
                    0,
                    source_texture.get(),
                    0,
                    &source_box);

                MappedTexture mapped(d3d_context_.get(), staging);
                const auto row_bytes = static_cast<size_t>(width_) * 4;
                for (int row = 0; row < height_; ++row) {
                    const auto* source_row =
                        static_cast<const uint8_t*>(mapped.value().pData) +
                        static_cast<size_t>(row) * mapped.value().RowPitch;
                    auto* destination =
                        packet->bytes.data() + static_cast<size_t>(row) * row_bytes;
                    std::memcpy(destination, source_row, row_bytes);
                }
            }

            std::shared_ptr<CaptureSource> previous;
            {
                std::scoped_lock lock(capture_mutex_);
                if (stop_requested_.load() || source->closed.load() ||
                    (source != active_source_ && source != pending_source_)) {
                    return;
                }
                {
                    std::scoped_lock frame_lock(frame_mutex_);
                    latest_frame_ = packet;
                }

                source->has_accepted_frame = true;
                source->next_capture_at = now +
                    std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                        std::chrono::duration<double>(1.0 / fps_));

                if (source == pending_source_) {
                    previous = active_source_;
                    active_source_ = source;
                    pending_source_.reset();
                    active_slot_.store(source->slot);
                }
            }
            close_source(previous);
        } catch (const RouterError& error) {
            handle_capture_error(source, error.code(), error.recoverable(), error.what());
        } catch (const winrt::hresult_error& error) {
            const HRESULT removed_reason = d3d_device_
                ? d3d_device_->GetDeviceRemovedReason()
                : S_OK;
            const std::string code = FAILED(removed_reason)
                ? "DEVICE_REMOVED"
                : "CAPTURE_HRESULT";
            handle_capture_error(
                source,
                code,
                code != "DEVICE_REMOVED",
                "capture failed with HRESULT " + std::to_string(error.code().value));
        } catch (const std::exception& error) {
            handle_capture_error(
                source,
                "CAPTURE_FAILED",
                true,
                error.what());
        }
    }

    void on_source_closed(const std::shared_ptr<CaptureSource>& source) noexcept {
        bool relevant = false;
        {
            std::scoped_lock lock(capture_mutex_);
            if (source == pending_source_) {
                pending_source_.reset();
                relevant = true;
            }
            if (source == active_source_) {
                active_source_.reset();
                active_slot_.store(0);
                relevant = true;
            }
        }
        close_source(source);
        if (relevant) {
            log_error(
                source->generation,
                source->slot,
                "HWND_DESTROYED",
                true,
                "capture target was closed");
        }
    }

private:
    WindowGeometry inspect_window(HWND hwnd) const {
        if (hwnd == nullptr || !IsWindow(hwnd)) {
            throw RouterError("HWND_INVALID", "capture target HWND is invalid");
        }
        if (!IsWindowVisible(hwnd)) {
            throw RouterError("HWND_NOT_VISIBLE", "capture target is not visible");
        }
        if (IsIconic(hwnd)) {
            throw RouterError("HWND_MINIMIZED", "capture target is minimized");
        }

        DWORD cloaked = 0;
        if (SUCCEEDED(DwmGetWindowAttribute(
                hwnd,
                DWMWA_CLOAKED,
                &cloaked,
                sizeof(cloaked))) &&
            cloaked != 0) {
            throw RouterError("HWND_CLOAKED", "capture target is cloaked");
        }

        WindowGeometry geometry;
        RECT client_rect{};
        if (!GetWindowRect(hwnd, &geometry.window_rect) ||
            !GetClientRect(hwnd, &client_rect) ||
            !ClientToScreen(hwnd, &geometry.client_origin)) {
            throw RouterError(
                "WINDOW_GEOMETRY_FAILED",
                "failed to query capture target geometry");
        }
        geometry.client_width = client_rect.right - client_rect.left;
        geometry.client_height = client_rect.bottom - client_rect.top;
        if (geometry.client_width <= 0 || geometry.client_height <= 0) {
            throw RouterError("EMPTY_CLIENT", "capture target client area is empty");
        }

        geometry.has_extended_rect = SUCCEEDED(DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            &geometry.extended_rect,
            sizeof(geometry.extended_rect)));
        geometry.dpi = GetDpiForWindow(hwnd);
        if (geometry.dpi == 0) {
            geometry.dpi = 96;
        }
        return geometry;
    }

    std::shared_ptr<CaptureSource> create_source(
        uint64_t generation,
        int slot,
        HWND hwnd) {
        auto source = std::make_shared<CaptureSource>();
        source->generation = generation;
        source->slot = slot;
        source->hwnd = hwnd;
        source->switch_started_at = std::chrono::steady_clock::now();
        source->switch_started_at_ms = unix_time_ms();
        source->geometry = inspect_window(hwnd);

        auto interop = winrt::get_activation_factory<
            GraphicsCaptureItem,
            IGraphicsCaptureItemInterop>();
        winrt::check_hresult(interop->CreateForWindow(
            hwnd,
            winrt::guid_of<GraphicsCaptureItem>(),
            winrt::put_abi(source->item)));

        source->item_size = source->item.Size();
        source->pool_size = source->item_size;
        source->frame_pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            winrt_device_,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            3,
            source->pool_size);

        const std::weak_ptr<CaptureSource> weak_source = source;
        const auto gate = callback_gate_;
        source->frame_token = source->frame_pool.FrameArrived(
            [gate, weak_source](
                const Direct3D11CaptureFramePool& sender,
                const winrt::Windows::Foundation::IInspectable&) noexcept {
                const auto captured_source = weak_source.lock();
                if (!captured_source) {
                    return;
                }
                CaptureRouter* router = nullptr;
                if (!gate->enter(router)) {
                    return;
                }
                CallbackLease lease(gate);
                router->on_frame(sender, captured_source);
            });
        source->frame_subscribed = true;

        source->closed_token = source->item.Closed(
            [gate, weak_source](
                const GraphicsCaptureItem&,
                const winrt::Windows::Foundation::IInspectable&) noexcept {
                const auto captured_source = weak_source.lock();
                if (!captured_source) {
                    return;
                }
                CaptureRouter* router = nullptr;
                if (!gate->enter(router)) {
                    return;
                }
                CallbackLease lease(gate);
                router->on_source_closed(captured_source);
            });
        source->closed_subscribed = true;

        source->session = source->frame_pool.CreateCaptureSession(source->item);
        source->session.IsCursorCaptureEnabled(false);
        return source;
    }

    CropGeometry resolve_crop(
        const WindowGeometry& geometry,
        UINT content_width,
        UINT content_height,
        UINT texture_width,
        UINT texture_height) const {
        struct Candidate {
            CropGeometry crop{};
            double score = std::numeric_limits<double>::infinity();
        };

        std::optional<Candidate> best;
        const auto consider = [&](const RECT& bounds, const char* source_name) {
            const int bounds_width = bounds.right - bounds.left;
            const int bounds_height = bounds.bottom - bounds.top;
            if (bounds_width <= 0 || bounds_height <= 0) {
                return;
            }

            const double scale_x =
                static_cast<double>(content_width) / static_cast<double>(bounds_width);
            const double scale_y =
                static_cast<double>(content_height) / static_cast<double>(bounds_height);
            const int client_x = static_cast<int>(std::llround(
                (geometry.client_origin.x - bounds.left) * scale_x));
            const int client_y = static_cast<int>(std::llround(
                (geometry.client_origin.y - bounds.top) * scale_y));
            const int client_width = static_cast<int>(std::llround(
                geometry.client_width * scale_x));
            const int client_height = static_cast<int>(std::llround(
                geometry.client_height * scale_y));

            const int centered_x = client_x + (client_width - width_) / 2;
            const int centered_y = client_y + (client_height - height_) / 2;
            if (centered_x < 0 || centered_y < 0 ||
                centered_x + width_ > static_cast<int>(content_width) ||
                centered_y + height_ > static_cast<int>(content_height) ||
                centered_x + width_ > static_cast<int>(texture_width) ||
                centered_y + height_ > static_cast<int>(texture_height)) {
                return;
            }

            Candidate candidate;
            candidate.crop.x = static_cast<UINT>(centered_x);
            candidate.crop.y = static_cast<UINT>(centered_y);
            candidate.crop.width = static_cast<UINT>(width_);
            candidate.crop.height = static_cast<UINT>(height_);
            candidate.crop.inferred_client_width = client_width;
            candidate.crop.inferred_client_height = client_height;
            candidate.crop.bounds_source = source_name;
            candidate.score =
                std::abs(client_width - width_) +
                std::abs(client_height - height_) +
                std::abs(scale_x - scale_y) * 100.0;
            if (!best || candidate.score < best->score) {
                best = candidate;
            }
        };

        consider(geometry.window_rect, "window");
        if (geometry.has_extended_rect) {
            consider(geometry.extended_rect, "dwm_extended");
        }
        if (!best) {
            throw RouterError(
                "CROP_OUT_OF_BOUNDS",
                "client crop does not fit the WGC frame");
        }
        if (std::abs(best->crop.inferred_client_width - width_) >
                kGeometryTolerancePixels ||
            std::abs(best->crop.inferred_client_height - height_) >
                kGeometryTolerancePixels) {
            std::ostringstream message;
            message << "physical client is "
                    << best->crop.inferred_client_width
                    << "x"
                    << best->crop.inferred_client_height
                    << ", expected "
                    << width_
                    << "x"
                    << height_;
            throw RouterError("GEOMETRY_MISMATCH", message.str());
        }
        return best->crop;
    }

    void create_d3d_device() {
        constexpr UINT flags =
            D3D11_CREATE_DEVICE_BGRA_SUPPORT |
            D3D11_CREATE_DEVICE_VIDEO_SUPPORT;
        D3D_FEATURE_LEVEL feature_level{};
        winrt::check_hresult(D3D11CreateDevice(
            nullptr,
            D3D_DRIVER_TYPE_HARDWARE,
            nullptr,
            flags,
            nullptr,
            0,
            D3D11_SDK_VERSION,
            d3d_device_.put(),
            &feature_level,
            d3d_context_.put()));

        auto dxgi_device = d3d_device_.as<IDXGIDevice>();
        winrt::com_ptr<IInspectable> inspectable;
        winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(
            dxgi_device.get(),
            inspectable.put()));
        winrt_device_ = inspectable.as<IDirect3DDevice>();
    }

    void create_staging_textures() {
        D3D11_TEXTURE2D_DESC description{};
        description.Width = static_cast<UINT>(width_);
        description.Height = static_cast<UINT>(height_);
        description.MipLevels = 1;
        description.ArraySize = 1;
        description.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        description.SampleDesc.Count = 1;
        description.Usage = D3D11_USAGE_STAGING;
        description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

        for (auto& texture : staging_textures_) {
            winrt::check_hresult(d3d_device_->CreateTexture2D(
                &description,
                nullptr,
                texture.put()));
        }
    }

    void close_source(const std::shared_ptr<CaptureSource>& source) noexcept {
        if (!source || source->closed.exchange(true)) {
            return;
        }
        // Serialize WGC revoke/Close with the frame callback. The closed flag is
        // published before waiting so a synchronous Closed callback cannot recurse.
        std::scoped_lock processing_lock(source->processing_mutex);
        try {
            if (source->frame_pool && source->frame_subscribed) {
                source->frame_pool.FrameArrived(source->frame_token);
            }
        } catch (...) {
        }
        source->frame_subscribed = false;
        try {
            if (source->item && source->closed_subscribed) {
                source->item.Closed(source->closed_token);
            }
        } catch (...) {
        }
        source->closed_subscribed = false;
        try {
            if (source->session) {
                source->session.Close();
            }
        } catch (...) {
        }
        try {
            if (source->frame_pool) {
                source->frame_pool.Close();
            }
        } catch (...) {
        }
        source->session = nullptr;
        source->frame_pool = nullptr;
        source->item = nullptr;
    }

    void handle_capture_error(
        const std::shared_ptr<CaptureSource>& source,
        const std::string& code,
        bool recoverable,
        const std::string& message) noexcept {
        bool should_close = false;
        {
            std::scoped_lock lock(capture_mutex_);
            if (source == pending_source_) {
                pending_source_.reset();
                should_close = true;
            } else if (source == active_source_ &&
                       (code == "HWND_DESTROYED" ||
                        code == "HWND_MINIMIZED" ||
                        code == "DEVICE_REMOVED")) {
                active_source_.reset();
                active_slot_.store(0);
                should_close = true;
            }
        }
        if (should_close) {
            close_source(source);
        }
        log_error(
            source ? source->generation : 0,
            source ? source->slot : 0,
            code,
            recoverable,
            message);
    }

    void register_output_thread() {
        HANDLE thread_handle = nullptr;
        if (!DuplicateHandle(
                GetCurrentProcess(),
                GetCurrentThread(),
                GetCurrentProcess(),
                &thread_handle,
                0,
                FALSE,
                DUPLICATE_SAME_ACCESS)) {
            throw RouterError(
                "THREAD_HANDLE_FAILED",
                "failed to duplicate output thread handle",
                false);
        }
        std::scoped_lock lock(output_thread_mutex_);
        output_thread_handle_ = thread_handle;
    }

    void unregister_output_thread() noexcept {
        HANDLE thread_handle = nullptr;
        {
            std::scoped_lock lock(output_thread_mutex_);
            thread_handle = output_thread_handle_;
            output_thread_handle_ = nullptr;
        }
        if (thread_handle != nullptr) {
            CloseHandle(thread_handle);
        }
    }

    void cancel_output_io() noexcept {
        std::scoped_lock lock(output_thread_mutex_);
        if (output_thread_handle_ != nullptr) {
            if (!CancelSynchronousIo(output_thread_handle_)) {
                const DWORD error = GetLastError();
                if (error != ERROR_NOT_FOUND) {
                    // The supervisor still enforces a bounded terminate/kill fallback.
                }
            }
        }
    }

    static bool write_all(
        HANDLE output,
        const uint8_t* data,
        size_t size,
        DWORD& error) {
        size_t offset = 0;
        while (offset < size) {
            const DWORD chunk = static_cast<DWORD>(
                std::min<size_t>(size - offset, 1U << 20));
            DWORD written = 0;
            if (!WriteFile(output, data + offset, chunk, &written, nullptr) ||
                written == 0) {
                error = GetLastError();
                return false;
            }
            offset += written;
        }
        error = ERROR_SUCCESS;
        return true;
    }

    int width_;
    int height_;
    int fps_;
    std::vector<uint8_t> black_frame_;
    std::atomic<bool> stop_requested_{false};
    std::atomic<int> active_slot_{0};
    std::atomic<int> delivered_slot_{0};
    std::atomic<uint64_t> delivered_generation_{0};

    std::mutex output_start_mutex_;
    std::condition_variable output_start_condition_;
    bool output_started_ = false;
    std::mutex output_thread_mutex_;
    HANDLE output_thread_handle_ = nullptr;

    std::mutex capture_mutex_;
    std::once_flag capture_stop_once_;
    std::mutex frame_mutex_;
    std::mutex d3d_mutex_;
    winrt::com_ptr<ID3D11Device> d3d_device_;
    winrt::com_ptr<ID3D11DeviceContext> d3d_context_;
    IDirect3DDevice winrt_device_{nullptr};
    std::array<winrt::com_ptr<ID3D11Texture2D>, kStagingTextureCount>
        staging_textures_{};
    size_t staging_texture_index_ = 0;
    std::shared_ptr<CaptureSource> active_source_;
    std::shared_ptr<CaptureSource> pending_source_;
    std::shared_ptr<const FramePacket> latest_frame_;
    std::shared_ptr<CallbackGate> callback_gate_;
    uint64_t last_requested_generation_ = 0;
};

void apply_dpi_awareness() {
    if (SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)) {
        return;
    }
    const DWORD error = GetLastError();
    if (error != ERROR_ACCESS_DENIED) {
        throw RouterError(
            "DPI_AWARENESS_FAILED",
            "failed to enable per-monitor V2 DPI awareness",
            false);
    }
    log_event(
        "dpi_awareness_warning",
        "\"code\":\"ALREADY_CONFIGURED\",\"message\":"
        "\"process DPI awareness was configured before router startup\"");
}

void validate_allowed_fields(
    const ParsedCommand& command,
    std::initializer_list<std::string_view> allowed) {
    for (const auto& [key, value] : command.fields) {
        static_cast<void>(value);
        const bool known = std::any_of(
            allowed.begin(),
            allowed.end(),
            [&](std::string_view expected) { return key == expected; });
        if (!known) {
            throw RouterError("UNKNOWN_COMMAND_FIELD", "unknown command field: " + key);
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        apply_dpi_awareness();
        winrt::init_apartment(winrt::apartment_type::multi_threaded);
        if (_setmode(_fileno(stdout), _O_BINARY) == -1) {
            throw RouterError("STDOUT_BINARY_FAILED", "failed to set stdout binary mode", false);
        }

        const int width = parse_int_arg(argv, argc, "--width", 1920);
        const int height = parse_int_arg(argv, argc, "--height", 1080);
        const int fps = parse_int_arg(argv, argc, "--fps", 30);
        if (width <= 0 || height <= 0 || fps <= 0 || fps > 120) {
            throw RouterError("INVALID_PROFILE", "invalid output geometry or frame rate", false);
        }

        CaptureRouter router(width, height, fps);
        std::thread output_thread([&router]() { router.output_loop(); });

        {
            std::ostringstream fields;
            fields << "\"pid\":" << GetCurrentProcessId()
                   << ",\"protocol_version\":1"
                   << ",\"width\":" << width
                   << ",\"height\":" << height
                   << ",\"fps\":" << fps
                   << ",\"format\":\"bgra\"";
            log_event("ready", fields.str());
        }

        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) {
                continue;
            }

            uint64_t generation = 0;
            int slot = 0;
            try {
                const ParsedCommand command = parse_command(line);
                generation = optional_generation(command);
                slot = optional_slot(command);

                if (command.verb == "START") {
                    validate_allowed_fields(
                        command,
                        {"width", "height", "fps", "format"});
                    router.start_output(
                        parse_positive_int(command, "width"),
                        parse_positive_int(command, "height"),
                        parse_positive_int(command, "fps"),
                        required_field(command, "format"));
                } else if (command.verb == "SWITCH") {
                    validate_allowed_fields(
                        command,
                        {"generation", "slot", "hwnd"});
                    generation = parse_unsigned(
                        required_field(command, "generation"),
                        "generation");
                    slot = parse_positive_int(command, "slot");
                    const uint64_t raw_hwnd = parse_unsigned(
                        required_field(command, "hwnd"),
                        "hwnd",
                        16);
                    if (raw_hwnd == 0 ||
                        raw_hwnd > static_cast<uint64_t>(
                            std::numeric_limits<uintptr_t>::max())) {
                        throw RouterError("HWND_INVALID", "HWND is outside pointer range");
                    }
                    router.switch_to(
                        generation,
                        slot,
                        reinterpret_cast<HWND>(static_cast<uintptr_t>(raw_hwnd)));
                } else if (command.verb == "STOP") {
                    validate_allowed_fields(command, {"reason"});
                    log_event("stop_requested");
                    break;
                } else {
                    throw RouterError("UNKNOWN_COMMAND", "unknown command verb");
                }
            } catch (const RouterError& error) {
                log_error(
                    generation,
                    slot,
                    error.code(),
                    error.recoverable(),
                    error.what());
            } catch (const winrt::hresult_error& error) {
                log_error(
                    generation,
                    slot,
                    "COMMAND_HRESULT",
                    true,
                    "command failed with HRESULT " +
                        std::to_string(error.code().value));
            } catch (const std::exception& error) {
                log_error(
                    generation,
                    slot,
                    "COMMAND_FAILED",
                    true,
                    error.what());
            }
        }

        router.stop();
        if (output_thread.joinable()) {
            output_thread.join();
        }
        log_event("stopped");
        return 0;
    } catch (const RouterError& error) {
        log_error(0, 0, error.code(), error.recoverable(), error.what());
        return 2;
    } catch (const winrt::hresult_error& error) {
        log_error(
            0,
            0,
            "FATAL_HRESULT",
            false,
            "fatal HRESULT " + std::to_string(error.code().value));
        return 2;
    } catch (const std::exception& error) {
        log_error(0, 0, "FATAL", false, error.what());
        return 2;
    }
}
