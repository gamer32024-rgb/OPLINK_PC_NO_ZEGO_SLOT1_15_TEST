import Foundation

final class GUIBridgeAPI {
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(session: URLSession = .shared) {
        self.session = session
    }

    func fetchTargets(baseURL: URL, completion: @escaping (Result<GUITargetsResponse, Error>) -> Void) {
        get(GUIBridgeEndpoint.targets(base: baseURL), completion: completion)
    }

    func fetchModules(baseURL: URL, completion: @escaping (Result<GUIModulesResponse, Error>) -> Void) {
        get(GUIBridgeEndpoint.modules(base: baseURL), completion: completion)
    }

    func fetchJobs(baseURL: URL, completion: @escaping (Result<GUIJobsResponse, Error>) -> Void) {
        get(GUIBridgeEndpoint.jobs(base: baseURL), completion: completion)
    }

    func playModuleChain(
        baseURL: URL,
        slots: [Int],
        modules: [String],
        completion: @escaping (Result<GUIBridgeResponse, Error>) -> Void
    ) {
        post(
            GUIBridgeEndpoint.moduleChain(base: baseURL),
            body: GUIModuleChainRequest(slots: slots, modules: modules),
            completion: completion
        )
    }

    func stopSlot(
        baseURL: URL,
        slot: Int,
        completion: @escaping (Result<GUIBridgeResponse, Error>) -> Void
    ) {
        post(GUIBridgeEndpoint.stopSlot(base: baseURL), body: GUISlotRequest(slot: slot), completion: completion)
    }

    func stopAll(baseURL: URL, completion: @escaping (Result<GUIBridgeResponse, Error>) -> Void) {
        post(GUIBridgeEndpoint.stopAll(base: baseURL), body: GUIEmptyRequest(), completion: completion)
    }

    func launcher(
        baseURL: URL,
        action: String,
        slots: [Int],
        completion: @escaping (Result<GUIBridgeResponse, Error>) -> Void
    ) {
        post(
            GUIBridgeEndpoint.launcher(base: baseURL, action: action),
            body: GUILauncherRequest(slots: slots),
            completion: completion
        )
    }

    func ensureLayout(
        baseURL: URL,
        slots: [Int],
        completion: @escaping (Result<GUIBridgeResponse, Error>) -> Void
    ) {
        post(GUIBridgeEndpoint.ensureLayout(base: baseURL), body: GUILayoutRequest(slots: slots), completion: completion)
    }

    private func get<Response: Decodable>(
        _ url: URL,
        completion: @escaping (Result<Response, Error>) -> Void
    ) {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 5
        perform(request, completion: completion)
    }

    private func post<Body: Encodable, Response: Decodable>(
        _ url: URL,
        body: Body,
        completion: @escaping (Result<Response, Error>) -> Void
    ) {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? encoder.encode(body)
        request.timeoutInterval = 8
        perform(request, completion: completion)
    }

    private func perform<Response: Decodable>(
        _ request: URLRequest,
        completion: @escaping (Result<Response, Error>) -> Void
    ) {
        session.dataTask(with: request) { [decoder] data, response, error in
            if let error {
                completion(.failure(error))
                return
            }
            guard let http = response as? HTTPURLResponse, let data else {
                completion(.failure(GUIBridgeError.invalidResponse))
                return
            }
            guard (200..<300).contains(http.statusCode) else {
                let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                completion(.failure(GUIBridgeError.rejected(message ?? "GUI_TEST_PC HTTP \(http.statusCode)")))
                return
            }
            do {
                completion(.success(try decoder.decode(Response.self, from: data)))
            } catch {
                completion(.failure(error))
            }
        }.resume()
    }
}

enum GUIBridgeError: LocalizedError {
    case invalidResponse
    case rejected(String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "GUI_TEST_PC bridge 回應無效。"
        case .rejected(let message): return message
        }
    }
}
