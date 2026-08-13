import Foundation
import CoreGraphics
import WebRTC

private final class FirstFrameProbe: NSObject, RTCVideoRenderer {
    var onFirstFrame: (() -> Void)?

    private let lock = NSLock()
    private var receivedFrame = false

    func setSize(_ size: CGSize) {}

    func renderFrame(_ frame: RTCVideoFrame?) {
        guard frame != nil else { return }
        lock.lock()
        let isFirst = !receivedFrame
        receivedFrame = true
        lock.unlock()
        if isFirst { onFirstFrame?() }
    }

    func reset() {
        lock.lock()
        receivedFrame = false
        lock.unlock()
    }
}

final class WHEPClient: NSObject {
    var onStateChanged: ((String) -> Void)?
    var onError: ((Error) -> Void)?
    var onReady: ((Int) -> Void)?

    private static let factory: RTCPeerConnectionFactory = {
        RTCInitializeSSL()
        return RTCPeerConnectionFactory(
            encoderFactory: RTCDefaultVideoEncoderFactory(),
            decoderFactory: RTCDefaultVideoDecoderFactory()
        )
    }()

    private let session: URLSession
    private var peerConnection: RTCPeerConnection?
    private var renderer: RTCVideoRenderer?
    private var remoteTrack: RTCVideoTrack?
    private var sessionURL: URL?
    private var offerTask: URLSessionDataTask?
    private var iceReady: (() -> Void)?
    private var connectionGeneration = 0
    private let readyLock = NSLock()
    private var ready = false
    private var iceConnected = false
    private var readyNotificationSent = false
    private var connectionStartedAt: Date?
    private var frameProbe = FirstFrameProbe()

    var isReady: Bool {
        readyLock.lock()
        defer { readyLock.unlock() }
        return ready && iceConnected
    }

    var isStarted: Bool { peerConnection != nil }

    init(session: URLSession = .shared) {
        self.session = session
        super.init()
    }

    func connect(endpoint: URL, renderer: RTCVideoRenderer? = nil) {
        stop()
        connectionGeneration += 1
        let generation = connectionGeneration
        frameProbe = FirstFrameProbe()
        frameProbe.onFirstFrame = { [weak self] in
            self?.markReady(generation: generation)
        }
        readyLock.lock()
        ready = false
        iceConnected = false
        readyNotificationSent = false
        readyLock.unlock()
        connectionStartedAt = Date()
        frameProbe.reset()
        self.renderer = renderer
        emitState("建立 WebRTC offer")

        let configuration = RTCConfiguration()
        configuration.sdpSemantics = .unifiedPlan
        configuration.bundlePolicy = .maxBundle
        let constraints = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil)
        guard let peer = Self.factory.peerConnection(
            with: configuration,
            constraints: constraints,
            delegate: self
        ) else {
            fail(WHEPError.cannotCreatePeer)
            return
        }
        peerConnection = peer
        let transceiverInit = RTCRtpTransceiverInit()
        transceiverInit.direction = .recvOnly
        peer.addTransceiver(of: .video, init: transceiverInit)

        peer.offer(for: constraints) { [weak self] offer, error in
            guard let self, generation == self.connectionGeneration else { return }
            if let error { self.fail(error); return }
            guard let offer else { self.fail(WHEPError.missingOffer); return }
            peer.setLocalDescription(offer) { [weak self] error in
                guard let self, generation == self.connectionGeneration else { return }
                if let error { self.fail(error); return }
                self.waitForICE(peer: peer, generation: generation) { [weak self] in
                    self?.postOffer(peer: peer, endpoint: endpoint, generation: generation)
                }
            }
        }
    }

    func stop() {
        connectionGeneration += 1
        iceReady = nil
        offerTask?.cancel()
        offerTask = nil
        if let remoteTrack {
            if let renderer { remoteTrack.remove(renderer) }
            remoteTrack.remove(frameProbe)
        }
        remoteTrack = nil
        renderer = nil
        peerConnection?.close()
        peerConnection = nil
        if let sessionURL { deleteSession(at: sessionURL) }
        sessionURL = nil
        readyLock.lock()
        ready = false
        iceConnected = false
        readyNotificationSent = false
        readyLock.unlock()
        connectionStartedAt = nil
        frameProbe.reset()
        emitState("未連線")
    }

    func setRenderer(_ renderer: RTCVideoRenderer?) {
        if let remoteTrack, let current = self.renderer {
            remoteTrack.remove(current)
        }
        self.renderer = renderer
        if let remoteTrack, let renderer {
            remoteTrack.add(renderer)
        }
    }

    private func waitForICE(peer: RTCPeerConnection, generation: Int, completion: @escaping () -> Void) {
        DispatchQueue.main.async { [weak self] in
            guard let self, generation == self.connectionGeneration else { return }
            if peer.iceGatheringState == .complete {
                completion()
                return
            }
            // Serialize the ICE callback and timeout so the WHEP offer is posted exactly once.
            var completed = false
            self.iceReady = { [weak self] in
                guard let self, generation == self.connectionGeneration, !completed else { return }
                completed = true
                self.iceReady = nil
                completion()
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
                guard let self, generation == self.connectionGeneration, !completed else { return }
                completed = true
                self.iceReady = nil
                completion()
            }
        }
    }

    private func postOffer(peer: RTCPeerConnection, endpoint: URL, generation: Int) {
        guard let localDescription = peer.localDescription else {
            fail(WHEPError.missingOffer)
            return
        }
        guard let offerSDP = Self.normalizedOfferSDP(localDescription.sdp),
              let offerData = offerSDP.data(using: .utf8) else {
            fail(WHEPError.invalidOffer)
            return
        }
        emitState("送出 WHEP offer")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/sdp", forHTTPHeaderField: "Content-Type")
        request.setValue("application/sdp", forHTTPHeaderField: "Accept")
        request.httpBody = offerData
        request.timeoutInterval = 8
        let urlSession = session
        let task = session.dataTask(with: request) { [weak self] data, response, error in
            let http = response as? HTTPURLResponse
            let locationURL = http?
                .value(forHTTPHeaderField: "Location")
                .flatMap { URL(string: $0, relativeTo: endpoint)?.absoluteURL }
            guard let self else {
                if let http,
                   (200..<300).contains(http.statusCode),
                   let locationURL {
                    Self.deleteSession(at: locationURL, using: urlSession)
                }
                return
            }
            DispatchQueue.main.async { [weak self] in
                guard let self else {
                    if let http,
                       (200..<300).contains(http.statusCode),
                       let locationURL {
                        Self.deleteSession(at: locationURL, using: urlSession)
                    }
                    return
                }
                guard generation == self.connectionGeneration else {
                    if let http,
                       (200..<300).contains(http.statusCode),
                       let locationURL {
                        self.deleteSession(at: locationURL)
                    }
                    return
                }
                self.offerTask = nil
                if let error {
                    if let http,
                       (200..<300).contains(http.statusCode),
                       let locationURL {
                        Self.deleteSession(at: locationURL, using: urlSession)
                    }
                    self.fail(error)
                    return
                }
                guard let http else {
                    self.fail(WHEPError.invalidAnswer)
                    return
                }
                guard (200..<300).contains(http.statusCode) else {
                    let reason: String?
                    if let data, let message = String(data: data, encoding: .utf8) {
                        reason = Self.compactServerMessage(message)
                    } else {
                        reason = nil
                    }
                    self.fail(WHEPError.serverRejected(status: http.statusCode, reason: reason))
                    return
                }
                guard let data,
                      let answerText = String(data: data, encoding: .utf8) else {
                    if let locationURL { self.deleteSession(at: locationURL) }
                    self.fail(WHEPError.invalidAnswer)
                    return
                }
                self.sessionURL = locationURL
                let answer = RTCSessionDescription(type: .answer, sdp: answerText)
                peer.setRemoteDescription(answer) { [weak self] error in
                    guard let self, generation == self.connectionGeneration else { return }
                    if let error { self.fail(error); return }
                    self.emitState("等待首幀")
                    self.attachFirstVideoReceiver(from: peer, generation: generation, retries: 20)
                }
            }
        }
        offerTask = task
        task.resume()
    }

    private func deleteSession(at url: URL) {
        Self.deleteSession(at: url, using: session)
    }

    private static func deleteSession(at url: URL, using session: URLSession) {
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 2
        session.dataTask(with: request).resume()
    }

    private static func normalizedOfferSDP(_ value: String) -> String? {
        let lines = value
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: true)
            .map(String.init)
        guard lines.first == "v=0",
              lines.contains(where: { $0.hasPrefix("m=video ") }) else { return nil }
        return lines.joined(separator: "\r\n") + "\r\n"
    }

    private static func compactServerMessage(_ value: String) -> String? {
        let message = value
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard !message.isEmpty else { return nil }
        return String(message.prefix(180))
    }

    private func attachFirstVideoReceiver(from peer: RTCPeerConnection, generation: Int, retries: Int) {
        guard generation == connectionGeneration else { return }
        if let track = peer.receivers.compactMap({ $0.track as? RTCVideoTrack }).first {
            attach(track)
            return
        }
        guard retries > 0 else {
            fail(WHEPError.missingVideoTrack)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            self?.attachFirstVideoReceiver(from: peer, generation: generation, retries: retries - 1)
        }
    }

    private func attach(_ track: RTCVideoTrack) {
        guard remoteTrack !== track else { return }
        if let remoteTrack {
            if let renderer { remoteTrack.remove(renderer) }
            remoteTrack.remove(frameProbe)
        }
        remoteTrack = track
        track.add(frameProbe)
        if let renderer { track.add(renderer) }
        emitState("解碼中")
    }

    private func markReady(generation: Int) {
        guard generation == connectionGeneration else { return }
        readyLock.lock()
        ready = true
        let shouldNotify = iceConnected && !readyNotificationSent
        if shouldNotify { readyNotificationSent = true }
        readyLock.unlock()
        if shouldNotify { emitReady(generation: generation) }
    }

    private func emitReady(generation: Int) {
        DispatchQueue.main.async { [weak self] in
            guard let self, generation == self.connectionGeneration else { return }
            self.readyLock.lock()
            let stillReady = self.ready && self.iceConnected
            if !stillReady { self.readyNotificationSent = false }
            self.readyLock.unlock()
            guard stillReady else { return }
            let elapsed = self.connectionStartedAt.map {
                Int(Date().timeIntervalSince($0) * 1000)
            } ?? 0
            self.onReady?(elapsed)
        }
    }

    private func setICEConnected(_ connected: Bool, generation: Int) {
        guard generation == connectionGeneration else { return }
        readyLock.lock()
        iceConnected = connected
        let shouldNotify = connected && ready && !readyNotificationSent
        if shouldNotify { readyNotificationSent = true }
        readyLock.unlock()
        if shouldNotify { emitReady(generation: generation) }
    }

    private func emitState(_ state: String) {
        DispatchQueue.main.async { [weak self] in self?.onStateChanged?(state) }
    }

    private func fail(_ error: Error) {
        DispatchQueue.main.async { [weak self] in self?.onError?(error) }
    }
}

extension WHEPClient: RTCPeerConnectionDelegate {
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange stateChanged: RTCSignalingState) {}

    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd stream: RTCMediaStream) {
        if let track = stream.videoTracks.first { attach(track) }
    }

    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove stream: RTCMediaStream) {}
    func peerConnectionShouldNegotiate(_ peerConnection: RTCPeerConnection) {}

    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceConnectionState) {
        guard self.peerConnection === peerConnection else { return }
        let generation = connectionGeneration
        switch newState {
        case .connected, .completed:
            setICEConnected(true, generation: generation)
            emitState("ICE 已連線")
        case .failed:
            setICEConnected(false, generation: generation)
            fail(WHEPError.iceFailed)
        case .closed:
            setICEConnected(false, generation: generation)
            fail(WHEPError.iceClosed)
        case .disconnected:
            setICEConnected(false, generation: generation)
            emitState("ICE 中斷，準備重連")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.75) { [weak self, weak peerConnection] in
                guard let self,
                      let peerConnection,
                      generation == self.connectionGeneration,
                      self.peerConnection === peerConnection,
                      peerConnection.iceConnectionState == .disconnected else { return }
                self.fail(WHEPError.iceDisconnected)
            }
        default: break
        }
    }

    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceGatheringState) {
        guard self.peerConnection === peerConnection else { return }
        if newState == .complete {
            DispatchQueue.main.async { [weak self] in
                guard let self, self.peerConnection === peerConnection else { return }
                self.iceReady?()
            }
        }
    }

    func peerConnection(_ peerConnection: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {}

    func peerConnection(_ peerConnection: RTCPeerConnection, didStartReceivingOn transceiver: RTCRtpTransceiver) {
        if let track = transceiver.receiver.track as? RTCVideoTrack { attach(track) }
    }
}

enum WHEPError: LocalizedError {
    case cannotCreatePeer
    case missingOffer
    case invalidOffer
    case invalidAnswer
    case serverRejected(status: Int, reason: String?)
    case missingVideoTrack
    case iceFailed
    case iceClosed
    case iceDisconnected

    var errorDescription: String? {
        switch self {
        case .cannotCreatePeer: return "無法建立 WebRTC peer。"
        case .missingOffer: return "WebRTC 沒有產生 offer。"
        case .invalidOffer: return "WebRTC 產生的 WHEP offer 格式無效。"
        case .invalidAnswer: return "MediaMTX WHEP answer 無效。"
        case .serverRejected(let status, let reason):
            return reason.map { "MediaMTX 拒絕 WHEP (HTTP \(status))：\($0)" }
                ?? "MediaMTX 拒絕 WHEP (HTTP \(status))。"
        case .missingVideoTrack: return "WHEP 已連線，但沒有收到 video track。"
        case .iceFailed: return "WebRTC ICE 連線失敗，請確認 iPhone Tailscale 已連線。"
        case .iceClosed: return "WebRTC ICE session 已關閉。"
        case .iceDisconnected: return "WebRTC ICE 已中斷，正在重新連線。"
        }
    }
}
