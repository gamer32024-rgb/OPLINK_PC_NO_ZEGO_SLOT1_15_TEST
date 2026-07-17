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
}

enum APIError: LocalizedError {
    case invalidResponse

    var errorDescription: String? { "Windows metadata API 沒有回傳有效資料。" }
}

