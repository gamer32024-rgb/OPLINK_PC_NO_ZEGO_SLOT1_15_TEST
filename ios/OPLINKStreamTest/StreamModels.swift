import Foundation

struct StreamSourcesResponse: Decodable {
    let ok: Bool
    let profile: StreamProfile
    let encoder: String
    let sources: [StreamSource]
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

    static func whep(base: URL, slot: Int) -> URL {
        replacingPath(base, with: String(format: "/oplink-whep/slot%02d/whep", slot))
    }

    private static func replacingPath(_ base: URL, with path: String) -> URL {
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

