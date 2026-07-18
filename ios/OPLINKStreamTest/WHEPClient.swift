import Foundation
import WebRTC

final class WHEPClient: NSObject {
    var onStateChanged: ((String) -> Void)?
    var onError: ((Error) -> Void)?

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
    private var iceReady: (() -> Void)?
    private var hasLocalCandidate = false
    private var connectionGeneration = 0

    init(session: URLSession = .shared) {
        self.session = session
        super.init()
    }

    func connect(endpoint: URL, renderer: RTCVideoRenderer) {
        stop()
        connectionGeneration += 1
        let generation = connectionGeneration
        hasLocalCandidate = false
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
        hasLocalCandidate = false
        if let remoteTrack, let renderer { remoteTrack.remove(renderer) }
        remoteTrack = nil
        renderer = nil
        peerConnection?.close()
        peerConnection = nil
        if let sessionURL {
            var request = URLRequest(url: sessionURL)
            request.httpMethod = "DELETE"
            request.timeoutInterval = 2
            session.dataTask(with: request).resume()
        }
        sessionURL = nil
        emitState("未連線")
    }

    private func waitForICE(peer: RTCPeerConnection, generation: Int, completion: @escaping () -> Void) {
        if peer.iceGatheringState == .complete || hasLocalCandidate {
            completion()
            return
        }
        var completed = false
        iceReady = { [weak self] in
            guard let self, generation == self.connectionGeneration, !completed else { return }
            completed = true
            self.iceReady = nil
            completion()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, generation == self.connectionGeneration, !completed else { return }
            completed = true
            self.iceReady = nil
            completion()
        }
    }

    private func postOffer(peer: RTCPeerConnection, endpoint: URL, generation: Int) {
        guard let localDescription = peer.localDescription else {
            fail(WHEPError.missingOffer)
            return
        }
        emitState("送出 WHEP offer")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/sdp", forHTTPHeaderField: "Content-Type")
        request.setValue("application/sdp", forHTTPHeaderField: "Accept")
        request.httpBody = localDescription.sdp.data(using: .utf8)
        request.timeoutInterval = 8
        session.dataTask(with: request) { [weak self] data, response, error in
            guard let self, generation == self.connectionGeneration else { return }
            if let error { self.fail(error); return }
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode),
                  let data,
                  let answerText = String(data: data, encoding: .utf8) else {
                self.fail(WHEPError.invalidAnswer)
                return
            }
            if let location = http.value(forHTTPHeaderField: "Location") {
                self.sessionURL = URL(string: location, relativeTo: endpoint)?.absoluteURL
            }
            let answer = RTCSessionDescription(type: .answer, sdp: answerText)
            peer.setRemoteDescription(answer) { [weak self] error in
                guard let self, generation == self.connectionGeneration else { return }
                if let error { self.fail(error); return }
                self.emitState("等待首幀")
                self.attachFirstVideoReceiver(from: peer, generation: generation, retries: 20)
            }
        }.resume()
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
        guard remoteTrack !== track, let renderer else { return }
        if let remoteTrack { remoteTrack.remove(renderer) }
        remoteTrack = track
        track.add(renderer)
        emitState("解碼中")
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
        switch newState {
        case .connected, .completed: emitState("ICE 已連線")
        case .failed: fail(WHEPError.iceFailed)
        case .disconnected: emitState("ICE 中斷")
        default: break
        }
    }

    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceGatheringState) {
        guard self.peerConnection === peerConnection else { return }
        if newState == .complete { iceReady?() }
    }

    func peerConnection(_ peerConnection: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.peerConnection === peerConnection else { return }
            self.hasLocalCandidate = true
            self.iceReady?()
        }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {}

    func peerConnection(_ peerConnection: RTCPeerConnection, didStartReceivingOn transceiver: RTCRtpTransceiver) {
        if let track = transceiver.receiver.track as? RTCVideoTrack { attach(track) }
    }
}

enum WHEPError: LocalizedError {
    case cannotCreatePeer
    case missingOffer
    case invalidAnswer
    case missingVideoTrack
    case iceFailed

    var errorDescription: String? {
        switch self {
        case .cannotCreatePeer: return "無法建立 WebRTC peer。"
        case .missingOffer: return "WebRTC 沒有產生 offer。"
        case .invalidAnswer: return "MediaMTX WHEP answer 無效。"
        case .missingVideoTrack: return "WHEP 已連線，但沒有收到 video track。"
        case .iceFailed: return "WebRTC ICE 連線失敗，請確認 iPhone Tailscale 已連線。"
        }
    }
}
