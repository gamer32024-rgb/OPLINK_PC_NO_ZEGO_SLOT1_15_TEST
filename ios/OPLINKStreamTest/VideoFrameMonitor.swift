import CoreGraphics
import Foundation
import WebRTC

final class VideoFrameMonitor: NSObject, RTCVideoRenderer {
    struct Snapshot {
        let size: CGSize
        let totalFrames: Int
        let framesSinceLastRead: Int
    }

    var onFirstFrame: ((CGSize, Int) -> Void)?
    var onSizeChanged: ((CGSize) -> Void)?

    private let target: RTCVideoRenderer
    private let lock = NSLock()
    private var size = CGSize.zero
    private var totalFrames = 0
    private var previousReadFrames = 0
    private var hasRenderedFrame = false
    private var generation = 0

    init(target: RTCVideoRenderer) {
        self.target = target
        super.init()
    }

    func setSize(_ size: CGSize) {
        target.setSize(size)
        lock.lock()
        self.size = size
        lock.unlock()
        DispatchQueue.main.async { [weak self] in self?.onSizeChanged?(size) }
    }

    func renderFrame(_ frame: RTCVideoFrame?) {
        target.renderFrame(frame)
        guard frame != nil else { return }
        lock.lock()
        totalFrames += 1
        let isFirst = !hasRenderedFrame
        hasRenderedFrame = true
        let currentSize = size
        let currentGeneration = generation
        lock.unlock()
        if isFirst {
            DispatchQueue.main.async { [weak self] in
                self?.onFirstFrame?(currentSize, currentGeneration)
            }
        }
    }

    @discardableResult
    func reset() -> Int {
        lock.lock()
        generation += 1
        size = .zero
        totalFrames = 0
        previousReadFrames = 0
        hasRenderedFrame = false
        let currentGeneration = generation
        lock.unlock()
        return currentGeneration
    }

    func snapshot() -> Snapshot {
        lock.lock()
        defer { lock.unlock() }
        let delta = totalFrames - previousReadFrames
        previousReadFrames = totalFrames
        return Snapshot(size: size, totalFrames: totalFrames, framesSinceLastRead: delta)
    }
}
