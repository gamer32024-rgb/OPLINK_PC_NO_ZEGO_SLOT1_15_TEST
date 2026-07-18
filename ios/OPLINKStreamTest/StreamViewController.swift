import UIKit
import WebRTC

final class StreamViewController: UIViewController {
    private enum Defaults {
        static let host = "oplink.streamTest.host"
        static let inputToken = "oplink.streamTest.inputToken"
    }

    private struct QueuedInput {
        let command: TouchOverlayView.Command
    }

    private let videoView = RTCMTLVideoView(frame: .zero)
    private let touchOverlay = TouchOverlayView(frame: .zero)
    private let chrome = UIVisualEffectView(effect: UIBlurEffect(style: .systemThinMaterialDark))
    private let previousButton = UIButton(type: .system)
    private let currentSlotButton = UIButton(type: .system)
    private let nextButton = UIButton(type: .system)
    private let guiButton = UIButton(type: .system)
    private let settingsButton = UIButton(type: .system)
    private let statusLabel = UILabel()
    private let sourceLabel = UILabel()
    private let metricsLabel = UILabel()
    private let targetLabel = UILabel()
    private let streamSlotPicker = StreamSlotPickerView(frame: .zero)
    private let guiPanel = GUIControlPanelView(frame: .zero)
    private let streamAPI = StreamAPI()
    private let guiAPI = GUIBridgeAPI()
    private lazy var frameMonitor = VideoFrameMonitor(target: videoView)
    private lazy var whepClient = WHEPClient()

    private var selectedSlot = 1
    private var connectionSequence = 0
    private var switchStartedAt: Date?
    private var latestResponse: StreamSourcesResponse?
    private var availableStreamSlots = Set<Int>(1...15)
    private var renderedSize = CGSize.zero
    private var renderedFPS = 0
    private var lastSwitchMilliseconds: Int?
    private var lastPublisherActivationMilliseconds: Int?
    private var lastInputRTTMilliseconds: Int?
    private var lastHostToHIDMilliseconds: Double?
    private var lastInputBackend = "disabled"
    private var inputQueue: [QueuedInput] = []
    private var inputInFlight = false
    private var metricsTimer: Timer?
    private var bridgeTimer: Timer?

    private var bridgeTargetRunningSlots = Set<Int>()
    private var bridgeHeartbeatRunningSlots = Set<Int>()
    private var bridgePlayingSlots = Set<Int>()
    private var bridgeSlotPlaybackStatus: [String: String] = [:]
    private var bridgeModules: [String: [String]] = [:]
    private var bridgeHeartbeatFresh = false

    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .landscape }
    override var preferredInterfaceOrientationForPresentation: UIInterfaceOrientation { .landscapeRight }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    override var prefersStatusBarHidden: Bool { true }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.015, green: 0.035, blue: 0.04, alpha: 1)
        buildLayout()
        configureCallbacks()
        refreshStreamControls()
        updateMetrics()
        metricsTimer = Timer.scheduledTimer(
            timeInterval: 1,
            target: self,
            selector: #selector(sampleRenderedFrames),
            userInfo: nil,
            repeats: true
        )
        bridgeTimer = Timer.scheduledTimer(
            timeInterval: 3,
            target: self,
            selector: #selector(periodicBridgeRefresh),
            userInfo: nil,
            repeats: true
        )
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        requestLandscape()
        if configuredBaseURL() != nil {
            connect(slot: selectedSlot)
            refreshGUIBridgeState()
        } else {
            presentHostSettings()
        }
    }

    deinit {
        metricsTimer?.invalidate()
        bridgeTimer?.invalidate()
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

        buildTopChrome()
        buildMetricsPanel()

        streamSlotPicker.translatesAutoresizingMaskIntoConstraints = false
        streamSlotPicker.isHidden = true
        view.addSubview(streamSlotPicker)
        guiPanel.translatesAutoresizingMaskIntoConstraints = false
        guiPanel.isHidden = true
        view.addSubview(guiPanel)
        NSLayoutConstraint.activate([
            streamSlotPicker.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            streamSlotPicker.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            streamSlotPicker.topAnchor.constraint(equalTo: view.topAnchor),
            streamSlotPicker.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            guiPanel.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            guiPanel.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            guiPanel.topAnchor.constraint(equalTo: view.topAnchor),
            guiPanel.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
    }

    private func buildTopChrome() {
        chrome.translatesAutoresizingMaskIntoConstraints = false
        chrome.layer.cornerRadius = 15
        chrome.clipsToBounds = true
        view.addSubview(chrome)

        configureIconButton(previousButton, systemName: "chevron.left", label: "上一個遊戲")
        previousButton.addTarget(self, action: #selector(previousSlotTapped), for: .touchUpInside)

        currentSlotButton.setTitle("GAME 01", for: .normal)
        currentSlotButton.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 14, weight: .bold)
        currentSlotButton.setTitleColor(UIColor(red: 0.04, green: 0.13, blue: 0.07, alpha: 1), for: .normal)
        currentSlotButton.backgroundColor = UIColor(red: 0.54, green: 0.9, blue: 0.4, alpha: 0.94)
        currentSlotButton.layer.cornerRadius = 10
        currentSlotButton.addTarget(self, action: #selector(showStreamSlotPicker), for: .touchUpInside)
        currentSlotButton.widthAnchor.constraint(equalToConstant: 86).isActive = true
        currentSlotButton.heightAnchor.constraint(equalToConstant: 40).isActive = true

        configureIconButton(nextButton, systemName: "chevron.right", label: "下一個遊戲")
        nextButton.addTarget(self, action: #selector(nextSlotTapped), for: .touchUpInside)

        guiButton.setTitle("GUI", for: .normal)
        guiButton.titleLabel?.font = .monospacedSystemFont(ofSize: 12, weight: .heavy)
        guiButton.setTitleColor(.white, for: .normal)
        guiButton.backgroundColor = UIColor(red: 0.08, green: 0.42, blue: 0.47, alpha: 0.95)
        guiButton.layer.cornerRadius = 10
        guiButton.accessibilityLabel = "開啟 GUI_TEST_PC 控制面板"
        guiButton.addTarget(self, action: #selector(showGUIPanel), for: .touchUpInside)
        guiButton.widthAnchor.constraint(equalToConstant: 54).isActive = true
        guiButton.heightAnchor.constraint(equalToConstant: 40).isActive = true

        configureIconButton(settingsButton, systemName: "network", label: "設定 Tailnet 主機")
        settingsButton.addTarget(self, action: #selector(presentHostSettings), for: .touchUpInside)

        let switchRow = UIStackView(arrangedSubviews: [previousButton, currentSlotButton, nextButton, guiButton])
        switchRow.axis = .horizontal
        switchRow.spacing = 7
        switchRow.alignment = .center

        statusLabel.font = .monospacedSystemFont(ofSize: 12, weight: .semibold)
        statusLabel.textColor = .white
        statusLabel.text = "未連線"
        statusLabel.numberOfLines = 1

        sourceLabel.font = .monospacedSystemFont(ofSize: 10, weight: .regular)
        sourceLabel.textColor = UIColor.white.withAlphaComponent(0.76)
        sourceLabel.numberOfLines = 2

        let textStack = UIStackView(arrangedSubviews: [statusLabel, sourceLabel])
        textStack.axis = .vertical
        textStack.spacing = 2

        let topRow = UIStackView(arrangedSubviews: [switchRow, textStack, settingsButton])
        topRow.axis = .horizontal
        topRow.alignment = .center
        topRow.spacing = 12
        chrome.contentView.addSubview(topRow)
        topRow.translatesAutoresizingMaskIntoConstraints = false

        NSLayoutConstraint.activate([
            chrome.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 10),
            chrome.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 8),
            chrome.widthAnchor.constraint(lessThanOrEqualToConstant: 760),
            topRow.leadingAnchor.constraint(equalTo: chrome.contentView.leadingAnchor, constant: 10),
            topRow.trailingAnchor.constraint(equalTo: chrome.contentView.trailingAnchor, constant: -10),
            topRow.topAnchor.constraint(equalTo: chrome.contentView.topAnchor, constant: 8),
            topRow.bottomAnchor.constraint(equalTo: chrome.contentView.bottomAnchor, constant: -8)
        ])
    }

    private func buildMetricsPanel() {
        let panel = UIVisualEffectView(effect: UIBlurEffect(style: .systemUltraThinMaterialDark))
        panel.translatesAutoresizingMaskIntoConstraints = false
        panel.layer.cornerRadius = 13
        panel.clipsToBounds = true
        view.addSubview(panel)

        metricsLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .medium)
        metricsLabel.textColor = .white
        metricsLabel.numberOfLines = 2
        targetLabel.font = .systemFont(ofSize: 10, weight: .bold)
        targetLabel.textColor = UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
        targetLabel.text = "TARGET 1080P / 30 FPS / SWITCH < 1000 MS / INPUT RTT < 300 MS"

        let stack = UIStackView(arrangedSubviews: [metricsLabel, targetLabel])
        stack.axis = .vertical
        stack.spacing = 2
        panel.contentView.addSubview(stack)
        stack.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            panel.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 10),
            panel.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -8),
            stack.leadingAnchor.constraint(equalTo: panel.contentView.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: panel.contentView.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: panel.contentView.topAnchor, constant: 7),
            stack.bottomAnchor.constraint(equalTo: panel.contentView.bottomAnchor, constant: -7)
        ])
    }

    private func configureIconButton(_ button: UIButton, systemName: String, label: String) {
        button.setImage(UIImage(systemName: systemName), for: .normal)
        button.tintColor = .white
        button.backgroundColor = UIColor.white.withAlphaComponent(0.1)
        button.layer.cornerRadius = 10
        button.accessibilityLabel = label
        button.widthAnchor.constraint(equalToConstant: 40).isActive = true
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
                && self.latestResponse?.input.enabled == true
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

        streamSlotPicker.onClose = { [weak self] in self?.streamSlotPicker.isHidden = true }
        streamSlotPicker.onSelectSlot = { [weak self] slot in
            self?.streamSlotPicker.isHidden = true
            self?.connect(slot: slot)
        }

        guiPanel.onClose = { [weak self] in self?.guiPanel.isHidden = true }
        guiPanel.onRefresh = { [weak self] in self?.refreshGUIBridgeState() }
        guiPanel.onPlay = { [weak self] slots, modules in self?.sendModuleChain(slots: slots, modules: modules) }
        guiPanel.onStopAll = { [weak self] in self?.sendStopAll() }
        guiPanel.onStopSlot = { [weak self] slot in self?.sendStopSlot(slot) }
        guiPanel.onLauncher = { [weak self] action, slots in self?.sendLauncher(action: action, slots: slots) }
        guiPanel.onArrange = { [weak self] slots in self?.sendArrange(slots: slots) }
    }

    @objc private func previousSlotTapped() {
        connect(slot: adjacentAvailableSlot(step: -1))
    }

    @objc private func nextSlotTapped() {
        connect(slot: adjacentAvailableSlot(step: 1))
    }

    @objc private func showStreamSlotPicker() {
        streamSlotPicker.apply(selectedSlot: selectedSlot, availableSlots: availableStreamSlots)
        streamSlotPicker.isHidden = false
        view.bringSubviewToFront(streamSlotPicker)
    }

    @objc private func showGUIPanel() {
        guiPanel.isHidden = false
        view.bringSubviewToFront(guiPanel)
        refreshGUIBridgeState()
    }

    private func adjacentAvailableSlot(step: Int) -> Int {
        for offset in 1...15 {
            let zeroBased = (selectedSlot - 1 + step * offset + 150) % 15
            let candidate = zeroBased + 1
            if availableStreamSlots.contains(candidate) { return candidate }
        }
        return selectedSlot
    }

    private func connect(slot: Int) {
        guard (1...15).contains(slot) else { return }
        guard let baseURL = configuredBaseURL() else {
            presentHostSettings()
            return
        }
        selectedSlot = slot
        connectionSequence += 1
        let sequence = connectionSequence
        switchStartedAt = Date()
        lastSwitchMilliseconds = nil
        lastPublisherActivationMilliseconds = nil
        renderedFPS = 0
        renderedSize = .zero
        lastInputRTTMilliseconds = nil
        lastHostToHIDMilliseconds = nil
        inputQueue.removeAll()
        inputInFlight = false
        touchOverlay.isUserInteractionEnabled = false
        frameMonitor.reset()
        whepClient.stop()
        refreshStreamControls()
        setStatus("檢查 Slot \(slot) 視窗", good: false)
        updateMetrics()

        if let response = latestResponse,
           let source = response.sources.first(where: { $0.slot == slot }),
           source.ok,
           source.aspectIs16x9 == true {
            activateVerifiedSource(
                source,
                response: response,
                baseURL: baseURL,
                slot: slot,
                sequence: sequence
            )
            return
        }

        streamAPI.fetchSources(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, sequence == self.connectionSequence else { return }
                switch result {
                case .failure(let error):
                    self.setStatus(error.localizedDescription, good: false)
                case .success(let response):
                    self.latestResponse = response
                    self.availableStreamSlots = Set(response.sources.filter { $0.ok }.map(\.slot))
                    self.refreshStreamControls()
                    guard let source = response.sources.first(where: { $0.slot == slot }), source.ok else {
                        let message = response.sources.first(where: { $0.slot == slot })?.error ?? "Slot \(slot) 不可用"
                        self.setStatus(message, good: false)
                        return
                    }
                    guard source.aspectIs16x9 == true else {
                        self.setStatus("拒絕非 16:9 來源", good: false)
                        return
                    }
                    self.activateVerifiedSource(
                        source,
                        response: response,
                        baseURL: baseURL,
                        slot: slot,
                        sequence: sequence
                    )
                }
            }
        }
    }

    private func activateVerifiedSource(
        _ source: StreamSource,
        response: StreamSourcesResponse,
        baseURL: URL,
        slot: Int,
        sequence: Int
    ) {
        updateSourceLabel(source: source, response: response)
        setStatus("切換主機 publisher 至 Slot \(slot)", good: false)
        streamAPI.activate(baseURL: baseURL, slot: slot) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, sequence == self.connectionSequence else { return }
                switch result {
                case .failure(let error):
                    self.latestResponse = nil
                    self.setStatus(error.localizedDescription, good: false)
                case .success(let activation):
                    guard activation.ok,
                          activation.publisherAlive,
                          activation.activeSlot == slot else {
                        self.latestResponse = nil
                        self.setStatus("主機未能啟動 Slot \(slot) publisher", good: false)
                        return
                    }
                    self.lastPublisherActivationMilliseconds = activation.activationMs
                    self.updateMetrics()
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

    private func refreshStreamControls() {
        currentSlotButton.setTitle(String(format: "GAME %02d", selectedSlot), for: .normal)
        streamSlotPicker.apply(selectedSlot: selectedSlot, availableSlots: availableStreamSlots)
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
        let activationText = lastPublisherActivationMilliseconds.map { "\($0)ms" } ?? "--"
        let inputRTT = lastInputRTTMilliseconds.map { "\($0)ms" } ?? "--"
        let hostToHID = lastHostToHIDMilliseconds.map { String(format: "%.1fms", $0) } ?? "--"
        metricsLabel.text = "SLOT \(selectedSlot)   VIDEO \(sizeText)   FPS \(renderedFPS)   SWITCH \(switchText)\nPUBLISHER \(activationText)   INPUT RTT \(inputRTT)   HOST→HID \(hostToHID)   BACKEND \(lastInputBackend)"
        if let switchMs = lastSwitchMilliseconds {
            targetLabel.textColor = switchMs <= 1000
                ? UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
                : UIColor(red: 1, green: 0.35, blue: 0.26, alpha: 1)
        }
    }

    private func enqueueInput(_ command: TouchOverlayView.Command) {
        guard configuredBaseURL() != nil, configuredInputToken() != nil else {
            setStatus("尚未設定可選的 input pairing token", good: false)
            return
        }
        let item = QueuedInput(command: command)
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
        streamAPI.sendInput(baseURL: baseURL, token: token, input: request) { [weak self] result in
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

    @objc private func periodicBridgeRefresh() {
        guard configuredBaseURL() != nil else { return }
        refreshGUIBridgeState()
    }

    private func refreshGUIBridgeState() {
        guard let baseURL = configuredBaseURL() else { return }

        guiAPI.fetchTargets(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let response):
                    self.bridgeTargetRunningSlots = Set(response.targetSlots.filter(\.running).map(\.slot))
                    self.applyGUIBridgeState()
                case .failure(let error):
                    if !self.guiPanel.isHidden { self.guiPanel.setStatus(error.localizedDescription, good: false) }
                }
            }
        }

        guiAPI.fetchModules(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let response):
                    self.bridgeModules = response.modules
                    self.applyGUIBridgeState()
                case .failure(let error):
                    if !self.guiPanel.isHidden { self.guiPanel.setStatus(error.localizedDescription, good: false) }
                }
            }
        }

        guiAPI.fetchJobs(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let response):
                    let heartbeat = response.gui
                    self.bridgeHeartbeatFresh = heartbeat?.isFresh == true
                    self.bridgeHeartbeatRunningSlots = Set(heartbeat?.runningSlots ?? [])
                    self.bridgePlayingSlots = Set(heartbeat?.playingSlots ?? [])
                    self.bridgeSlotPlaybackStatus = heartbeat?.slotPlaybackStatus ?? [:]
                    self.applyGUIBridgeState()
                    if response.executionOwner != "GUI_TEST_PC" || heartbeat?.executionOwner != "GUI_TEST_PC" {
                        self.guiPanel.setStatus("拒絕：bridge execution owner 不是 GUI_TEST_PC。", good: false)
                    } else if !self.guiPanel.isHidden {
                        self.guiPanel.setStatus(
                            self.bridgeHeartbeatFresh ? "GUI_TEST_PC 已連線，等待橋接命令。" : "GUI_TEST_PC heartbeat 已過期。",
                            good: self.bridgeHeartbeatFresh
                        )
                    }
                case .failure(let error):
                    if !self.guiPanel.isHidden { self.guiPanel.setStatus(error.localizedDescription, good: false) }
                }
            }
        }
    }

    private func applyGUIBridgeState() {
        let running = bridgeHeartbeatFresh ? bridgeHeartbeatRunningSlots : bridgeTargetRunningSlots
        guiPanel.apply(
            runningSlots: running,
            playingSlots: bridgePlayingSlots,
            slotPlaybackStatus: bridgeSlotPlaybackStatus,
            modules: bridgeModules,
            heartbeatFresh: bridgeHeartbeatFresh
        )
    }

    private func sendModuleChain(slots: [Int], modules: [String]) {
        guard let baseURL = configuredBaseURL() else { return }
        guiAPI.playModuleChain(baseURL: baseURL, slots: slots, modules: modules) { [weak self] result in
            self?.handleBridgeResult(result, success: "模組串列已交給 GUI_TEST_PC")
        }
    }

    private func sendStopSlot(_ slot: Int) {
        guard let baseURL = configuredBaseURL() else { return }
        guiAPI.stopSlot(baseURL: baseURL, slot: slot) { [weak self] result in
            self?.handleBridgeResult(result, success: "GAME \(slot) 單槽中止已交給 GUI_TEST_PC")
        }
    }

    private func sendStopAll() {
        guard let baseURL = configuredBaseURL() else { return }
        guiAPI.stopAll(baseURL: baseURL) { [weak self] result in
            self?.handleBridgeResult(result, success: "全部中止已交給 GUI_TEST_PC")
        }
    }

    private func sendLauncher(action: String, slots: [Int]) {
        guard let baseURL = configuredBaseURL() else { return }
        guiAPI.launcher(baseURL: baseURL, action: action, slots: slots) { [weak self] result in
            self?.handleBridgeResult(result, success: "\(action) 已交給 GUI_TEST_PC", delayedRefresh: true)
        }
    }

    private func sendArrange(slots: [Int]) {
        guard let baseURL = configuredBaseURL() else { return }
        guiAPI.ensureLayout(baseURL: baseURL, slots: slots) { [weak self] result in
            self?.handleBridgeResult(result, success: "視窗排列已交給 GUI_TEST_PC", delayedRefresh: true)
        }
    }

    private func handleBridgeResult(
        _ result: Result<GUIBridgeResponse, Error>,
        success: String,
        delayedRefresh: Bool = false
    ) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            switch result {
            case .success(let response):
                guard response.relayedTo == "GUI_TEST_PC" else {
                    self.guiPanel.setStatus("拒絕：命令未 relay 到 GUI_TEST_PC。", good: false)
                    return
                }
                self.guiPanel.setStatus("\(success)，job=\(response.job.id)", good: true)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
                    self?.refreshGUIBridgeState()
                }
                if delayedRefresh {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
                        self?.refreshGUIBridgeState()
                    }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
                        self?.refreshGUIBridgeState()
                    }
                }
            case .failure(let error):
                self.guiPanel.setStatus(error.localizedDescription, good: false)
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
            message: "HTTPS 主機供串流及 GUI_TEST_PC bridge 使用。Input pairing token 只供直接觸控，觀看模組播放時可留空。不要輸入 Tailscale auth key。",
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
            field.placeholder = "Input pairing token（可留空）"
            field.text = UserDefaults.standard.string(forKey: Defaults.inputToken)
            field.autocapitalizationType = .none
            field.autocorrectionType = .no
        }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel))
        alert.addAction(UIAlertAction(title: "儲存並連線", style: .default) { [weak self, weak alert] _ in
            guard let self,
                  let value = alert?.textFields?.first?.text else { return }
            do {
                let base = try StreamEndpoint.normalizedHost(value)
                let token = alert?.textFields?[1].text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                UserDefaults.standard.set(base.absoluteString, forKey: Defaults.host)
                if token.isEmpty {
                    UserDefaults.standard.removeObject(forKey: Defaults.inputToken)
                } else {
                    UserDefaults.standard.set(token, forKey: Defaults.inputToken)
                }
                self.connect(slot: self.selectedSlot)
                self.refreshGUIBridgeState()
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
