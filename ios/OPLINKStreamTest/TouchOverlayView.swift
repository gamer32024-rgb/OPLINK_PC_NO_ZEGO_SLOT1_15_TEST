import UIKit

final class TouchOverlayView: UIView {
    struct Command {
        let action: String
        let x: Double
        let y: Double
    }

    var onCommand: ((Command) -> Void)?
    var onTouchOutsideVideo: (() -> Void)?
    var videoAspect: CGFloat = 16.0 / 9.0
    private var hasActiveTouch = false

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        isMultipleTouchEnabled = false
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        hasActiveTouch = emit("down", touches: touches)
        if !hasActiveTouch {
            onTouchOutsideVideo?()
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard hasActiveTouch else { return }
        _ = emit("move", touches: touches)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard hasActiveTouch else { return }
        _ = emit("up", touches: touches, clampToVideo: true)
        hasActiveTouch = false
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard hasActiveTouch else { return }
        onCommand?(Command(action: "cancel", x: 0, y: 0))
        hasActiveTouch = false
    }

    @discardableResult
    private func emit(_ action: String, touches: Set<UITouch>, clampToVideo: Bool = false) -> Bool {
        guard let touch = touches.first else { return false }
        let rect = renderedVideoRect()
        var point = touch.location(in: self)
        if clampToVideo {
            point.x = min(max(point.x, rect.minX), rect.maxX)
            point.y = min(max(point.y, rect.minY), rect.maxY)
        } else if !rect.contains(point) {
            return false
        }
        let x = min(1, max(0, (point.x - rect.minX) / max(1, rect.width)))
        let y = min(1, max(0, (point.y - rect.minY) / max(1, rect.height)))
        onCommand?(Command(action: action, x: Double(x), y: Double(y)))
        return true
    }

    private func renderedVideoRect() -> CGRect {
        guard bounds.width > 0, bounds.height > 0, videoAspect > 0 else { return bounds }
        let boundsAspect = bounds.width / bounds.height
        if boundsAspect > videoAspect {
            let width = bounds.height * videoAspect
            return CGRect(x: (bounds.width - width) / 2, y: 0, width: width, height: bounds.height)
        }
        let height = bounds.width / videoAspect
        return CGRect(x: 0, y: (bounds.height - height) / 2, width: bounds.width, height: height)
    }
}
