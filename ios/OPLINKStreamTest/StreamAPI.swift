import Foundation

final class StreamAPI {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func fetchSources(baseURL: URL, completion: @escaping (Result<StreamSourcesResponse, Error>) -> Void) {
        var request = URLRequest(url: StreamEndpoint.sources(base: baseURL))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 5
        session.dataTask(with: request) { data, response, error in
            if let error {
                completion(.failure(error))
                return
            }
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode), let data else {
                completion(.failure(APIError.invalidResponse))
                return
            }
            do {
                completion(.success(try JSONDecoder().decode(StreamSourcesResponse.self, from: data)))
            } catch {
                completion(.failure(error))
            }
        }.resume()
    }

    func activate(
        baseURL: URL,
        slot: Int,
        completion: @escaping (Result<StreamActivateResponse, Error>) -> Void
    ) {
        var request = URLRequest(url: StreamEndpoint.activate(base: baseURL))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(StreamActivateRequest(slot: slot))
        request.timeoutInterval = 5
        session.dataTask(with: request) { data, response, error in
            if let error { completion(.failure(error)); return }
            guard let http = response as? HTTPURLResponse, let data else {
                completion(.failure(APIError.invalidResponse))
                return
            }
            guard (200..<300).contains(http.statusCode) else {
                let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                completion(.failure(StreamInputError.rejected(
                    statusCode: http.statusCode,
                    message: message ?? "Activate failed: HTTP \(http.statusCode)"
                )))
                return
            }
            do {
                completion(.success(try JSONDecoder().decode(StreamActivateResponse.self, from: data)))
            } catch {
                completion(.failure(error))
            }
        }.resume()
    }

    func sendInput(
        baseURL: URL,
        token: String,
        input: StreamInputRequest,
        completion: @escaping (Result<StreamInputResponse, Error>) -> Void
    ) {
        var request = URLRequest(url: StreamEndpoint.replacingPath(baseURL, with: "/oplink-test/api/v1/input"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try? JSONEncoder().encode(input)
        request.timeoutInterval = 4
        session.dataTask(with: request) { data, response, error in
            if let error { completion(.failure(error)); return }
            guard let http = response as? HTTPURLResponse, let data else {
                completion(.failure(APIError.invalidResponse))
                return
            }
            if (200..<300).contains(http.statusCode) {
                do {
                    completion(.success(try JSONDecoder().decode(StreamInputResponse.self, from: data)))
                } catch {
                    completion(.failure(error))
                }
                return
            }
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            completion(.failure(StreamInputError.rejected(
                statusCode: http.statusCode,
                message: message ?? "Input request failed: HTTP \(http.statusCode)"
            )))
        }.resume()
    }
}

enum APIError: LocalizedError {
    case invalidResponse

    var errorDescription: String? { "Windows metadata API 沒有回傳有效資料。" }
}
