import Foundation

struct PreparedOfferGate {
    private(set) var requested = false
    private var ready = false
    private var posted = false
    private var endpoint: URL?

    mutating func request(_ endpoint: URL) -> URL? {
        guard !requested else { return nil }
        requested = true
        self.endpoint = endpoint
        return takeIfReady()
    }

    mutating func markReady() -> URL? {
        ready = true
        return takeIfReady()
    }

    private mutating func takeIfReady() -> URL? {
        guard ready, requested, !posted, let endpoint else { return nil }
        posted = true
        return endpoint
    }
}
