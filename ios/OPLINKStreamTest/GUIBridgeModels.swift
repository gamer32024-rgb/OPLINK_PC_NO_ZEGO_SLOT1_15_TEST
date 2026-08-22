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

struct GUIModuleGroup: Decodable {
    let name: String
    let modules: [String]
}

struct GUIModuleGroupsResponse: Decodable {
    let groups: [GUIModuleGroup]
}

struct GUIModuleChainPreset: Codable {
    let index: Int
    let name: String
    let modules: [String]
}

struct GUIModuleChainPresetsResponse: Decodable {
    let presets: [GUIModuleChainPreset]
}

struct GUIModuleChainPresetSaveResponse: Decodable {
    let ok: Bool
    let preset: GUIModuleChainPreset
    let presets: [GUIModuleChainPreset]
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
    let playbackAutomations: [GUIPlaybackAutomation]

    enum CodingKeys: String, CodingKey {
        case online
        case updatedAt = "updated_at"
        case runningSlots = "running_slots"
        case playingSlots = "playing_slots"
        case slotPlaybackStatus = "slot_playback_status"
        case launcherBusy = "launcher_busy"
        case executionOwner = "execution_owner"
        case playbackAutomations = "playback_automations"
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
        playbackAutomations = try values.decodeIfPresent(
            [GUIPlaybackAutomation].self,
            forKey: .playbackAutomations
        ) ?? []
    }

    var isFresh: Bool {
        guard let updatedAt,
              let date = ISO8601DateFormatter().date(from: updatedAt) else { return false }
        return Date().timeIntervalSince(date) < 12
    }
}

struct GUIPlaybackAutomation: Decodable {
    let id: String
    let mode: String
    let targetKind: String
    let slots: [Int]
    let modules: [String]
    let script: String?
    let cooldownSeconds: Double
    let repeatCount: Int?
    let runAt: Double?
    let runAtISO: String?
    let nextRunAt: Double?
    let iteration: Int
    let status: String

    enum CodingKeys: String, CodingKey {
        case id, mode, slots, modules, script, iteration, status
        case targetKind = "target_kind"
        case cooldownSeconds = "cooldown_seconds"
        case repeatCount = "repeat_count"
        case runAt = "run_at"
        case runAtISO = "run_at_iso"
        case nextRunAt = "next_run_at"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        mode = try values.decodeIfPresent(String.self, forKey: .mode) ?? ""
        targetKind = try values.decodeIfPresent(String.self, forKey: .targetKind) ?? "module_chain"
        slots = try values.decodeIfPresent([Int].self, forKey: .slots) ?? []
        modules = try values.decodeIfPresent([String].self, forKey: .modules) ?? []
        script = try values.decodeIfPresent(String.self, forKey: .script)
        cooldownSeconds = try values.decodeIfPresent(Double.self, forKey: .cooldownSeconds) ?? 0
        repeatCount = try values.decodeIfPresent(Int.self, forKey: .repeatCount)
        runAt = try values.decodeIfPresent(Double.self, forKey: .runAt)
        runAtISO = try values.decodeIfPresent(String.self, forKey: .runAtISO)
        nextRunAt = try values.decodeIfPresent(Double.self, forKey: .nextRunAt)
        iteration = try values.decodeIfPresent(Int.self, forKey: .iteration) ?? 0
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? ""
    }

    var isActive: Bool { ["waiting", "running", "cooling"].contains(status) }
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

struct GUIPlaybackAutomationRequest: Encodable {
    let mode: String
    let targetKind = "module_chain"
    let slots: [Int]
    let modules: [String]
    let cooldownSeconds: Int?
    let repeatCount: Int?
    let runAt: String?

    enum CodingKeys: String, CodingKey {
        case mode, slots, modules
        case targetKind = "target_kind"
        case cooldownSeconds = "cooldown_seconds"
        case repeatCount = "repeat_count"
        case runAt = "run_at"
    }
}

struct GUIPlaybackAutomationCancelRequest: Encodable {
    let id: String
}

struct GUIModuleChainPresetSaveRequest: Encodable {
    let name: String
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

    static func moduleGroups(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/module-groups")
    }

    static func moduleChainPresets(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/module-chain-presets")
    }

    static func moduleChainPreset(base: URL, index: Int) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/module-chain-presets/\(index)")
    }

    static func jobs(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/jobs")
    }

    static func moduleChain(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/module-chain")
    }

    static func playbackAutomation(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/automation")
    }

    static func cancelPlaybackAutomation(base: URL) -> URL {
        StreamEndpoint.replacingPath(base, with: "/gui-test-pc/api/play/automation/cancel")
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
