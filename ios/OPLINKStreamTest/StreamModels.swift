import Foundation

struct StreamSourcesResponse: Decodable {
    let ok: Bool
    let profile: StreamProfile
    let encoder: String
    let networkUnderlay: NetworkUnderlay
    let input: StreamInputInfo
    let sources: [StreamSource]

    enum CodingKeys: String, CodingKey {
        case ok, profile, encoder, input, sources
        case networkUnderlay = "network_underlay"
    }
}

struct NetworkUnderlay: Decodable {
    let gatePassed: Bool
    let selectedAlias: String
    let selectedDescription: String
    let selectedEffectiveMetric: Int
    let usbSharingCanWin: Bool
    let overallDefaultAlias: String?
    let overallDefaultIsSelectedEthernet: Bool?

    enum CodingKeys: String, CodingKey {
        case gatePassed = "gate_passed"
        case selectedAlias = "selected_alias"
        case selectedDescription = "selected_description"
        case selectedEffectiveMetric = "selected_effective_metric"
        case usbSharingCanWin = "usb_sharing_can_win"
        case overallDefaultAlias = "overall_default_alias"
        case overallDefaultIsSelectedEthernet = "overall_default_is_selected_ethernet"
    }
}

struct StreamInputInfo: Decodable {
    let enabled: Bool
    let tokenRequired: Bool?
    let reportMode: String?
    let port: String?
    let minSlotIntervalMs: Int?
    let executionOwner: String?
    let relayedTo: String?

    enum CodingKeys: String, CodingKey {
        case enabled, port
        case tokenRequired = "token_required"
        case reportMode = "report_mode"
        case minSlotIntervalMs = "min_slot_interval_ms"
        case executionOwner = "execution_owner"
        case relayedTo = "relayed_to"
    }
}

struct StreamInputRequest: Encodable {
    let slot: Int
    let action: String
    let x: Double?
    let y: Double?
    let pointerID: Int?
    let text: String?
    let key: String?
    let clientSentAtMs: Int64

    enum CodingKeys: String, CodingKey {
        case slot, action, x, y, text, key
        case pointerID = "pointer_id"
        case clientSentAtMs = "client_sent_at_ms"
    }

    static func touch(slot: Int, command: TouchOverlayView.Command, sentAtMs: Int64) -> StreamInputRequest {
        StreamInputRequest(
            slot: slot,
            action: command.action,
            x: command.x,
            y: command.y,
            pointerID: 0,
            text: nil,
            key: nil,
            clientSentAtMs: sentAtMs
        )
    }

    static func text(slot: Int, value: String, sentAtMs: Int64) -> StreamInputRequest {
        StreamInputRequest(
            slot: slot,
            action: "text",
            x: nil,
            y: nil,
            pointerID: nil,
            text: value,
            key: nil,
            clientSentAtMs: sentAtMs
        )
    }

    static func key(slot: Int, value: String, sentAtMs: Int64) -> StreamInputRequest {
        StreamInputRequest(
            slot: slot,
            action: "key",
            x: nil,
            y: nil,
            pointerID: nil,
            text: nil,
            key: value,
            clientSentAtMs: sentAtMs
        )
    }
}

struct StreamActivateRequest: Encodable {
    let slot: Int
}

struct StreamPrewarmRequest: Encodable {
    let slots: [Int]
}

struct StreamViewerRequest: Encodable {
    let state: String
    let slot: Int?
}

struct StreamActivateResponse: Decodable {
    let ok: Bool
    let mode: String
    let encoder: String
    let activeSlot: Int
    let publisherPID: Int?
    let publisherAlive: Bool
    let reused: Bool
    let activationMs: Int

    enum CodingKeys: String, CodingKey {
        case ok, mode, encoder, reused
        case activeSlot = "active_slot"
        case publisherPID = "publisher_pid"
        case publisherAlive = "publisher_alive"
        case activationMs = "activation_ms"
    }
}

struct StreamPrewarmResponse: Decodable {
    let ok: Bool
    let warmSlots: [Int]
    let requestedSlots: [Int]

    enum CodingKeys: String, CodingKey {
        case ok
        case warmSlots = "warm_slots"
        case requestedSlots = "requested_slots"
    }
}

struct StreamViewerResponse: Decodable {
    let ok: Bool
    let state: String
    let slot: Int?
    let heartbeatAgeMs: Int?
    let idleTimeoutMs: Int
    let warmSlots: [Int]?

    enum CodingKeys: String, CodingKey {
        case ok, state, slot
        case heartbeatAgeMs = "heartbeat_age_ms"
        case idleTimeoutMs = "idle_timeout_ms"
        case warmSlots = "warm_slots"
    }
}

struct StreamInputResponse: Decodable {
    let ok: Bool
    let slot: Int
    let action: String
    let hostReceivedAtMs: Int64?
    let hidAckAtMs: Int64?
    let hostToHIDAckMs: Double
    let slotCooldownWaitMs: Int
    let backend: String
    let executionOwner: String?
    let relayedTo: String?

    enum CodingKeys: String, CodingKey {
        case ok, slot, action, backend
        case hostReceivedAtMs = "host_received_at_ms"
        case hidAckAtMs = "hid_ack_at_ms"
        case hostToHIDAckMs = "host_to_hid_ack_ms"
        case slotCooldownWaitMs = "slot_cooldown_wait_ms"
        case executionOwner = "execution_owner"
        case relayedTo = "relayed_to"
    }
}

struct StreamProfile: Decodable {
    let encoded: PixelSize
    let fps: Int
    let bitrateKbps: Int

    enum CodingKeys: String, CodingKey {
        case encoded
        case fps
        case bitrateKbps = "bitrate_kbps"
    }
}

struct PixelSize: Decodable {
    let w: Int
    let h: Int
}

struct StreamSource: Decodable {
    let ok: Bool
    let slot: Int
    let hwnd: Int?
    let pid: Int?
    let title: String?
    let clientLogical: PixelSize?
    let capturePhysicalExpected: PixelSize?
    let aspect: Double?
    let aspectIs16x9: Bool?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case slot
        case hwnd
        case pid
        case title
        case clientLogical = "client_logical"
        case capturePhysicalExpected = "capture_physical_expected"
        case aspect
        case aspectIs16x9 = "aspect_is_16_9"
        case error
    }
}

enum StreamEndpoint {
    static func normalizedHost(_ value: String) throws -> URL {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: trimmed),
              components.scheme?.lowercased() == "https",
              components.host != nil else {
            throw StreamConfigurationError.invalidHost
        }
        components.path = ""
        components.query = nil
        components.fragment = nil
        guard let url = components.url else { throw StreamConfigurationError.invalidHost }
        return url
    }

    static func sources(base: URL) -> URL {
        replacingPath(base, with: "/oplink-test/api/v1/sources")
    }

    static func activate(base: URL) -> URL {
        replacingPath(base, with: "/oplink-test/api/v1/activate")
    }

    static func prewarm(base: URL) -> URL {
        replacingPath(base, with: "/oplink-test/api/v1/prewarm")
    }

    static func viewer(base: URL) -> URL {
        replacingPath(base, with: "/oplink-test/api/v1/viewer")
    }

    static func whep(base: URL, slot: Int) -> URL {
        replacingPath(base, with: String(format: "/oplink-whep/slot%02d/whep", slot))
    }

    static func replacingPath(_ base: URL, with path: String) -> URL {
        var components = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        components.path = path
        components.query = nil
        components.fragment = nil
        return components.url!
    }
}

enum StreamConfigurationError: LocalizedError {
    case invalidHost

    var errorDescription: String? {
        "請輸入 Windows 啟動器顯示的 https:// Tailnet 主機網址，不要加入路徑。"
    }
}

enum StreamInputError: LocalizedError {
    case rejected(statusCode: Int, message: String)

    var isInvalidPairingToken: Bool {
        if case .rejected(let statusCode, _) = self { return statusCode == 401 }
        return false
    }

    var errorDescription: String? {
        switch self {
        case .rejected(_, let message): return message
        }
    }
}
