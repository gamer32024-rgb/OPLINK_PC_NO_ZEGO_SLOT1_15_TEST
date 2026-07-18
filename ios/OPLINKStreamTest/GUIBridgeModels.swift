import Foundation

struct GUITargetsResponse: Decodable {
    let targetSlots: [GUITargetSlot]

    enum CodingKeys: String, CodingKey {
        case targetSlots = "target_slots"
    }
}

struct GUITargetSlot: Decodable {
    let slot: Int
    let running: Bool
}

struct GUIModulesResponse: Decodable {
    let modules: [String: [String]]
}

struct GUIJobsResponse: Decodable {
    let jobs: [GUIBridgeJob]
    let gui: GUIHeartbeat?
    let executionOwner: String?

    enum CodingKeys: String, CodingKey {
        case jobs, gui
        case executionOwner = "execution_owner"
    }
}

struct GUIHeartbeat: Decodable {
    let updatedAt: String?
    let online: Bool?
    let runningSlots: [Int]
    let playingSlots: [Int]
    let slotPlaybackStatus: [String: String]
    let launcherBusy: Bool?
    let executionOwner: String?

    enum CodingKeys: String, CodingKey {
        case online
        case updatedAt = "updated_at"
        case runningSlots = "running_slots"
        case playingSlots = "playing_slots"
        case slotPlaybackStatus = "slot_playback_status"
        case launcherBusy = "launcher_busy"
        case executionOwner = "execution_owner"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
        online = try values.decodeIfPresent(Bool.self, forKey: .online)
        runningSlots = try values.decodeIfPresent([Int].self, forKey: .runningSlots) ?? []
        playingSlots = try values.decodeIfPresent([Int].self, forKey: .playingSlots) ?? []
        slotPlaybackStatus = try values.decodeIfPresent([String: String].self, forKey: .slotPlaybackStatus) ?? [:]
        launcherBusy = try values.decodeIfPresent(Bool.self, forKey: .launcherBusy)
        executionOwner = try values.decodeIfPresent(String.self, forKey: .executionOwner)
    }

    var isFresh: Bool {
        guard let updatedAt,
              let date = ISO8601DateFormatter().date(from: updatedAt) else { return false }
        return Date().timeIntervalSince(date) < 12
    }
}

struct GUIBridgeResponse: Decodable {
    let ok: Bool
    let relayedTo: String
    let job: GUIBridgeJob
    let gui: GUIHeartbeat?

    enum CodingKeys: String, CodingKey {
        case ok, job, gui
        case relayedTo = "relayed_to"
    }
}

struct GUIBridgeJob: Decodable {
    let id: String
    let action: String?
    let status: String?
    let label: String?
    let slots: [Int]?
    let slotStatus: [String: String]?

    enum CodingKeys: String, CodingKey {
        case id, action, status, label, slots
        case slotStatus = "slot_status"
    }
}

struct GUIModuleChainRequest: Encodable {
    let slots: [Int]
    let modules: [String]
}

struct GUISlotRequest: Encodable {
    let slot: Int
}

struct GUILauncherRequest: Encodable {
    let slots: [Int]
    let forcebindMode = "netbind"
    let useWindowsUsers = true

    enum CodingKeys: String, CodingKey {
        case slots
        case forcebindMode = "forcebind_mode"
        case useWindowsUsers = "use_windows_users"
    }
}

struct GUILayoutRequest: Encodable {
    let slots: [Int]
}

struct GUIEmptyRequest: Encodable {}

enum GUIBridgeEndpoint {
    static func targets(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/targets")
    }

    static func modules(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/modules")
    }

    static func jobs(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/jobs")
    }

    static func moduleChain(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/module-chain")
    }

    static func stopSlot(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/stop-slot")
    }

    static func stopAll(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/stop-all")
    }

    static func launcher(base: URL, action: String) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/starcg/\(action)")
    }

    static func ensureLayout(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/starcg/layout/ensure")
    }
}
