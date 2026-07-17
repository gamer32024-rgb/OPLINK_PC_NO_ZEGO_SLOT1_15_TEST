import UIKit
import WebRTC

final class StreamViewController: UIViewController {
    private enum Defaults {
        static let host = "oplink.streamTest.host"
        static let inputToken = "oplink.streamTest.inputToken"
    }

    private struct QueuedInput {
        let command: TouchOverlayView.Command
        let enqueuedAt: Date
    }

    private let videoView = RTCMTLVideoView(frame: .zero)
    private let touchOverlay = TouchOverlayView(frame: .zero)
    private let chrome = UIVisualEffectView(effect: UIBlurEffect(style: .systemThinMaterialDark))
    private let slot1Button = UIButton(type: .system)
    private let slot15Button = UIButton(type: .system)
    private let settingsButton = UIButton(type: .system)
    private let statusLabel = UILabel()
    private let sourceLabel = UILabel()
    private let metricsLabel = UILabel()
    private let targetLabel = UILabel()
    private let api = StreamAPI()
    private lazy var frameMonitor = VideoFrameMonitor(target: videoView)
    private lazy var whepClient = WHEPClient()

    private var selectedSlot = 1
    private var connectionSequence = 0
    private var switchStartedAt: Date?
    private var latestResponse: StreamSourcesResponse?
    private var renderedSize = CGSize.zero
    private var renderedFPS = 0
    private var lastSwitchMilliseconds: Int?
    private var lastInputRTTMilliseconds: Int?
    private var lastHostToHIDMilliseconds: Double?
    private var lastInputBackend = "--"
    private var inputQueue: [QueuedInput] = []
    private var inputInFlight = false
    private var metricsTimer: Timer?

    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .landscape }
    override var preferredInterfaceOrientationForPresentation: UIInterfaceOrientation { .landscapeRight }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    override var prefersStatusBarHidden: Bool { true }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.015, green: 0.035, blue: 0.04, alpha: 1)
        buildLayout()
        configureCallbacks()
        refreshButtonState()
        updateMetrics()
        metricsTimer = Timer.scheduledTimer(
            timeInterval: 1,
            target: self,
            selector: #selector(sampleRenderedFrames),
            userInfo: nil,
            repeats: true
        )
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        requestLandscape()
        if configuredBaseURL() != nil {
            connect(slot: selectedSlot)
        } else {
            presentHostSettings()
        }
    }

    deinit {
        metricsTimer?.invalidate()
        whepClient.stop()
    }

    private func buildLayout() {
        videoView.translatesAutoresizingMaskIntoConstraints = false
        videoView.videoContentMode = .scaleAspectFit
        videoView.backgroundColor = .black
        view.addSubview(videoView)
        touchOverlay.translatesAutoresizingMaskIntoConstraints = false
        touchOverlay.isUserInteractionEnabled = false
        view.addSubview(touchOverlay)
        NSLayoutConstraint.activate([
            videoView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            videoView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            videoView.topAnchor.constraint(equalTo: view.topAnchor),
            videoView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            touchOverlay.leadingAnchor.constraint(equalTo: videoView.leadingAnchor),
            touchOverlay.trailingAnchor.constraint(equalTo: videoView.trailingAnchor),
            touchOverlay.topAnchor.constraint(equalTo: videoView.topAnchor),
            touchOverlay.bottomAnchor.constraint(equalTo: videoView.bottomAnchor)
        ])

        chrome.translatesAutoresizingMaskIntoConstraints = false
        chrome.layer.cornerRadius = 16
        chrome.clipsToBounds = true
        view.addSubview(chrome)

        let slotStack = UIStackView(arrangedSubviews: [slot1Button, slot15Button])
        slotStack.axis = .horizontal
        slotStack.spacing = 8
        slotStack.distribution = .fillEqually
        configureSlotButton(slot1Button, title: "EXE 1", action: #selector(selectSlot1))
        configureSlotButton(slot15Button, title: "EXE 15", action: #selector(selectSlot15))

        statusLabel.font = .monospacedSystemFont(ofSize: 12, weight: .semibold)
        statusLabel.textColor = .white
        statusLabel.text = "未連線"
        statusLabel.numberOfLines = 1

        sourceLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        sourceLabel.textColor = UIColor.white.withAlphaComponent(0.76)
        sourceLabel.numberOfLines = 2

        settingsButton.setImage(UIImage(systemName: "network"), for: .normal)
        settingsButton.tintColor = .white
        settingsButton.backgroundColor = UIColor.white.withAlphaComponent(0.1)
        settingsButton.layer.cornerRadius = 10
        settingsButton.accessibilityLabel = "設定 Tailnet 主機"
        settingsButton.addTarget(self, action: #selector(presentHostSettings), for: .touchUpInside)
        settingsButton.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            settingsButton.widthAnchor.constraint(equalToConstant: 40),
            settingsButton.heightAnchor.constraint(equalToConstant: 40)
        ])

        let textStack = UIStackView(arrangedSubviews: [statusLabel, sourceLabel])
        textStack.axis = .vertical
        textStack.spacing = 2
        let topRow = UIStackView(arrangedSubviews: [slotStack, textStack, settingsButton])
        topRow.axis = .horizontal
        topRow.alignment = .center
        topRow.spacing = 12
        slotStack.widthAnchor.constraint(equalToConstant: 176).isActive = true

        chrome.contentView.addSubview(topRow)
        topRow.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            chrome.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 10),
            chrome.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 8),
            chrome.widthAnchor.constraint(lessThanOrEqualToConstant: 620),
            topRow.leadingAnchor.constraint(equalTo: chrome.contentView.leadingAnchor, constant: 10),
            topRow.trailingAnchor.constraint(equalTo: chrome.contentView.trailingAnchor, constant: -10),
            topRow.topAnchor.constraint(equalTo: chrome.contentView.topAnchor, constant: 8),
            topRow.bottomAnchor.constraint(equalTo: chrome.contentView.bottomAnchor, constant: -8)
        ])

        let metricsPanel = UIVisualEffectView(effect: UIBlurEffect(style: .systemUltraThinMaterialDark))
        metricsPanel.translatesAutoresizingMaskIntoConstraints = false
        metricsPanel.layer.cornerRadius = 13
        metricsPanel.clipsToBounds = true
        view.addSubview(metricsPanel)

        metricsLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .medium)
        metricsLabel.textColor = .white
        metricsLabel.numberOfLines = 2
        targetLabel.font = .systemFont(ofSize: 10, weight: .bold)
        targetLabel.textColor = UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
        targetLabel.text = "TARGET 720P / 30 FPS / SWITCH < 1000 MS / INPUT RTT < 300 MS"

        let metricsStack = UIStackView(arrangedSubviews: [metricsLabel, targetLabel])
        metricsStack.axis = .vertical
        metricsStack.spacing = 2
        metricsPanel.contentView.addSubview(metricsStack)
        metricsStack.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            metricsPanel.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 10),
            metricsPanel.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -8),
            metricsStack.leadingAnchor.constraint(equalTo: metricsPanel.contentView.leadingAnchor, constant: 10),
            metricsStack.trailingAnchor.constraint(equalTo: metricsPanel.contentView.trailingAnchor, constant: -10),
            metricsStack.topAnchor.constraint(equalTo: metricsPanel.contentView.topAnchor, constant: 7),
            metricsStack.bottomAnchor.constraint(equalTo: metricsPanel.contentView.bottomAnchor, constant: -7)
        ])
    }

    private func configureSlotButton(_ button: UIButton, title: String, action: Selector) {
        button.setTitle(title, for: .normal)
        button.titleLabel?.font = .monospacedSystemFont(ofSize: 14, weight: .bold)
        button.layer.cornerRadius = 10
        button.addTarget(self, action: action, for: .touchUpInside)
        button.heightAnchor.constraint(equalToConstant: 40).isActive = true
    }

    private func configureCallbacks() {
        frameMonitor.onFirstFrame = { [weak self] size in
            guard let self else { return }
            self.renderedSize = size
            if let switchStartedAt = self.switchStartedAt {
                self.lastSwitchMilliseconds = Int(Date().timeIntervalSince(switchStartedAt) * 1000)
            }
            self.switchStartedAt = nil
            self.touchOverlay.isUserInteractionEnabled = self.configuredInputToken() != nil
            self.setStatus("Slot \(self.selectedSlot) 首幀完成", good: true)
            self.updateMetrics()
        }
        frameMonitor.onSizeChanged = { [weak self] size in
            self?.renderedSize = size
            self?.updateMetrics()
        }
        whepClient.onStateChanged = { [weak self] state in
            self?.setStatus(state, good: state == "ICE 已連線" || state == "解碼中")
        }
        whepClient.onError = { [weak self] error in
            self?.setStatus(error.localizedDescription, good: false)
        }
        touchOverlay.onCommand = { [weak self] command in
            self?.enqueueInput(command)
        }
    }

    @objc private func selectSlot1() { connect(slot: 1) }
    @objc private func selectSlot15() { connect(slot: 15) }

    private func connect(slot: Int) {
        guard let baseURL = configuredBaseURL() else {
            presentHostSettings()
            return
        }
        selectedSlot = slot
        connectionSequence += 1
        let sequence = connectionSequence
        switchStartedAt = Date()
        lastSwitchMilliseconds = nil
        renderedFPS = 0
        renderedSize = .zero
        lastInputRTTMilliseconds = nil
        lastHostToHIDMilliseconds = nil
        inputQueue.removeAll()
        inputInFlight = false
        touchOverlay.isUserInteractionEnabled = false
        frameMonitor.reset()
        whepClient.stop()
        refreshButtonState()
        setStatus("檢查 Slot \(slot) 視窗", good: false)
        updateMetrics()

        api.fetchSources(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, sequence == self.connectionSequence else { return }
                switch result {
                case .failure(let error):
                    self.setStatus(error.localizedDescription, good: false)
                case .success(let response):
                    self.latestResponse = response
                    guard let source = response.sources.first(where: { $0.slot == slot }), source.ok else {
                        let message = response.sources.first(where: { $0.slot == slot })?.error ?? "Slot \(slot) 不可用"
                        self.setStatus(message, good: false)
                        return
                    }
                    guard source.aspectIs16x9 == true else {
                        self.setStatus("拒絕非 16:9 來源", good: false)
                        return
                    }
                    self.updateSourceLabel(source: source, response: response)
                    self.whepClient.connect(
                        endpoint: StreamEndpoint.whep(base: baseURL, slot: slot),
                        renderer: self.frameMonitor
                    )
                }
            }
        }
    }

    private func updateSourceLabel(source: StreamSource, response: StreamSourcesResponse) {
        let logical = source.clientLogical.map { "\($0.w)x\($0.h)" } ?? "?"
        let capture = source.capturePhysicalExpected.map { "\($0.w)x\($0.h)" } ?? "?"
        let network = response.networkUnderlay
        let defaultRoute = network.overallDefaultIsSelectedEthernet == true
            ? "ETH"
            : (network.overallDefaultAlias ?? "UNKNOWN")
        lastInputBackend = response.input.reportMode ?? "disabled"
        sourceLabel.text = "SRC \(logical) | WGC \(capture) | OUT \(response.profile.encoded.w)x\(response.profile.encoded.h) | \(response.encoder)\nETH \(network.selectedAlias) m=\(network.selectedEffectiveMetric) | USB-WIN \(network.usbSharingCanWin ? "YES" : "NO") | DEFAULT \(defaultRoute) | PICO \(lastInputBackend)"
    }

    private func refreshButtonState() {
        style(slot1Button, selected: selectedSlot == 1)
        style(slot15Button, selected: selectedSlot == 15)
    }

    private func style(_ button: UIButton, selected: Bool) {
        button.backgroundColor = selected
            ? UIColor(red: 0.54, green: 0.9, blue: 0.4, alpha: 0.94)
            : UIColor(white: 0.34, alpha: 0.75)
        button.setTitleColor(selected ? UIColor(red: 0.04, green: 0.13, blue: 0.07, alpha: 1) : .white, for: .normal)
    }

    private func setStatus(_ text: String, good: Bool) {
        statusLabel.text = text
        statusLabel.textColor = good
            ? UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
            : UIColor(red: 1, green: 0.76, blue: 0.31, alpha: 1)
    }

    @objc private func sampleRenderedFrames() {
        let snapshot = frameMonitor.snapshot()
        renderedFPS = snapshot.framesSinceLastRead
        if snapshot.size != .zero { renderedSize = snapshot.size }
        updateMetrics()
    }

    private func updateMetrics() {
        let sizeText = renderedSize == .zero
            ? "0x0"
            : "\(Int(renderedSize.width))x\(Int(renderedSize.height))"
        let switchText = lastSwitchMilliseconds.map { "\($0)ms" } ?? "--"
        let inputRTT = lastInputRTTMilliseconds.map { "\($0)ms" } ?? "--"
        let hostToHID = lastHostToHIDMilliseconds.map { String(format: "%.1fms", $0) } ?? "--"
        metricsLabel.text = "SLOT \(selectedSlot)   VIDEO \(sizeText)   FPS \(renderedFPS)   SWITCH \(switchText)\nINPUT RTT \(inputRTT)   HOST→HID \(hostToHID)   BACKEND \(lastInputBackend)"
        if let switchMs = lastSwitchMilliseconds, let inputMs = lastInputRTTMilliseconds {
            targetLabel.textColor = switchMs <= 1000 && inputMs <= 300
                ? UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
                : UIColor(red: 1, green: 0.35, blue: 0.26, alpha: 1)
        }
    }

    private func enqueueInput(_ command: TouchOverlayView.Command) {
        guard configuredBaseURL() != nil, configuredInputToken() != nil else {
            setStatus("尚未設定 pairing token", good: false)
            return
        }
        let item = QueuedInput(command: command, enqueuedAt: Date())
        if command.action == "move", inputQueue.last?.command.action == "move" {
            inputQueue[inputQueue.count - 1] = item
        } else {
            inputQueue.append(item)
        }
        drainInputQueue()
    }

    private func drainInputQueue() {
        guard !inputInFlight, !inputQueue.isEmpty,
              let baseURL = configuredBaseURL(),
              let token = configuredInputToken() else { return }
        inputInFlight = true
        let queued = inputQueue.removeFirst()
        let sentAt = Int64(Date().timeIntervalSince1970 * 1000)
        let request = StreamInputRequest(
            slot: selectedSlot,
            action: queued.command.action,
            x: queued.command.x,
            y: queued.command.y,
            pointerID: 0,
            clientSentAtMs: sentAt
        )
        let requestStartedAt = Date()
        api.sendInput(baseURL: baseURL, token: token, input: request) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                self.inputInFlight = false
                switch result {
                case .success(let response):
                    self.lastInputRTTMilliseconds = Int(Date().timeIntervalSince(requestStartedAt) * 1000)
                    self.lastHostToHIDMilliseconds = response.hostToHIDAckMs
                    self.lastInputBackend = response.backend
                    self.updateMetrics()
                case .failure(let error):
                    self.inputQueue.removeAll()
                    self.setStatus(error.localizedDescription, good: false)
                }
                self.drainInputQueue()
            }
        }
    }

    private func configuredBaseURL() -> URL? {
        guard let value = UserDefaults.standard.string(forKey: Defaults.host) else { return nil }
        return try? StreamEndpoint.normalizedHost(value)
    }

    private func configuredInputToken() -> String? {
        guard let token = UserDefaults.standard.string(forKey: Defaults.inputToken)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !token.isEmpty else { return nil }
        return token
    }

    @objc private func presentHostSettings() {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(
            title: "Tailnet Windows 主機",
            message: "輸入啟動器顯示的 HTTPS 主機及本機 pairing token。不要輸入 Tailscale auth key。",
            preferredStyle: .alert
        )
        alert.addTextField { field in
            field.placeholder = "https://windows-host.example.ts.net"
            field.text = UserDefaults.standard.string(forKey: Defaults.host)
            field.keyboardType = .URL
            field.autocapitalizationType = .none
            field.autocorrectionType = .no
        }
        alert.addTextField { field in
            field.placeholder = "本機 pairing token"
            field.text = UserDefaults.standard.string(forKey: Defaults.inputToken)
            field.autocapitalizationType = .none
            field.autocorrectionType = .no
        }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel))
        alert.addAction(UIAlertAction(title: "儲存並連線", style: .default) { [weak self, weak alert] _ in
            guard let self,
                  let value = alert?.textFields?.first?.text,
                  let token = alert?.textFields?[1].text?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !token.isEmpty else { return }
            do {
                let base = try StreamEndpoint.normalizedHost(value)
                UserDefaults.standard.set(base.absoluteString, forKey: Defaults.host)
                UserDefaults.standard.set(token, forKey: Defaults.inputToken)
                self.connect(slot: self.selectedSlot)
            } catch {
                self.setStatus(error.localizedDescription, good: false)
            }
        })
        present(alert, animated: true)
    }

    private func requestLandscape() {
        guard #available(iOS 16.0, *), let scene = view.window?.windowScene else { return }
        setNeedsUpdateOfSupportedInterfaceOrientations()
        scene.requestGeometryUpdate(.iOS(interfaceOrientations: .landscapeRight))
    }
}
