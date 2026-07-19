import UIKit
import WebRTC

final class StreamViewController: UIViewController {
    private enum Defaults {
        static let host = "oplink.streamTest.host"
        static let inputToken = "oplink.streamTest.inputToken"
    }

    private struct QueuedInput {
        let request: StreamInputRequest
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
    private let streamSlotPicker = StreamSlotPickerView(effect: nil)
    private let guiPanel = GUIControlPanelView(frame: .zero)
    private let legacyControls = LegacyStreamControlsView()
    private let fixedRightRail = FixedRightRailView()
    private let inputToast = UILabel()
    private let keyboardPanel = UIVisualEffectView(effect: UIBlurEffect(style: .systemThinMaterialDark))
    private let keyboardTextField = UITextField()
    private let keyboardEnterButton = UIButton(type: .system)
    private let keyboardBackspaceButton = UIButton(type: .system)
    private let streamAPI = StreamAPI()
    private let guiAPI = GUIBridgeAPI()
    private lazy var frameMonitor = VideoFrameMonitor(target: videoView)
    private var whepClients: [Int: WHEPClient] = [:]
    private var activeWHEPSlot: Int?
    private var pendingWHEPSlot: Int?
    private var desiredWarmSlots = Set<Int>()
    private var prewarmSequence = 0

    private var selectedSlot = 1
    private var connectionSequence = 0
    private var switchStartedAt: Date?
    private var latestResponse: StreamSourcesResponse?
    private var availableStreamSlots = Set<Int>(1...15)
    private var renderedSize = CGSize.zero
    private var renderedFPS = 0
    private var lastSwitchMilliseconds: Int?
    private var lastPublisherActivationMilliseconds: Int?
    private var lastWHEPConnectMilliseconds: Int?
    private var lastInputRTTMilliseconds: Int?
    private var lastHostToHIDMilliseconds: Double?
    private var lastInputBackend = "disabled"
    private var inputQueue: [QueuedInput] = []
    private var inputInFlight = false
    private var metricsTimer: Timer?
    private var bridgeTimer: Timer?
    private var viewerHeartbeatTimer: Timer?
    private var viewerIsForeground = false

    private var bridgeTargetRunningSlots = Set<Int>()
    private var bridgeHeartbeatRunningSlots = Set<Int>()
    private var bridgePlayingSlots = Set<Int>()
    private var bridgeSlotPlaybackStatus: [String: String] = [:]
    private var bridgeModules: [String: [String]] = [:]
    private var bridgeModuleGroups: [GUIModuleGroup] = []
    private var bridgeModulePresets: [GUIModuleChainPreset] = []
    private var bridgeHeartbeatFresh = false
    private var suppressTouchSequenceForControlCollapse = false
    private var controlCenterXConstraint: NSLayoutConstraint?
    private var controlCenterYConstraint: NSLayoutConstraint?
    private var controlDragStart = CGPoint.zero
    private var controlPositionReady = false
    private var inputToastTask: DispatchWorkItem?
    private var keyboardFlushTask: DispatchWorkItem?
    private var keyboardPanelCenterXConstraint: NSLayoutConstraint?
    private var keyboardPanelCenterYConstraint: NSLayoutConstraint?
    private var keyboardPanelWidthConstraint: NSLayoutConstraint?
    private var keyboardPanelPositionReady = false
    private var keyboardPanelDragStart = CGPoint.zero

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
        keyboardTextField.delegate = self
        keyboardTextField.addTarget(self, action: #selector(keyboardTextChanged), for: .editingChanged)
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(applicationDidEnterBackground),
            name: UIApplication.didEnterBackgroundNotification,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(applicationDidBecomeActive),
            name: UIApplication.didBecomeActiveNotification,
            object: nil
        )
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        requestLandscape()
        if configuredBaseURL() != nil {
            beginViewerSession()
            connect(slot: selectedSlot)
            refreshGUIBridgeState()
        } else {
            presentHostSettings()
        }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        positionLegacyControlsIfNeeded()
        clampLegacyControls(save: false)
        positionKeyboardPanelIfNeeded()
        updateKeyboardPanelWidth()
        clampKeyboardPanel(save: false)
    }

    deinit {
        metricsTimer?.invalidate()
        bridgeTimer?.invalidate()
        viewerHeartbeatTimer?.invalidate()
        keyboardFlushTask?.cancel()
        NotificationCenter.default.removeObserver(self)
        whepClients.values.forEach { $0.stop() }
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

        streamSlotPicker.translatesAutoresizingMaskIntoConstraints = false
        streamSlotPicker.isHidden = true
        view.addSubview(streamSlotPicker)
        guiPanel.translatesAutoresizingMaskIntoConstraints = false
        guiPanel.isHidden = true
        view.addSubview(guiPanel)
        buildLegacyControls()
        buildKeyboardPanel()
        buildInputToast()
        NSLayoutConstraint.activate([
            streamSlotPicker.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 4),
            streamSlotPicker.centerYAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerYAnchor),
            streamSlotPicker.widthAnchor.constraint(equalToConstant: 82),
            streamSlotPicker.heightAnchor.constraint(equalToConstant: 300),
            guiPanel.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            guiPanel.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            guiPanel.topAnchor.constraint(equalTo: view.topAnchor),
            guiPanel.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
    }

    private func buildLegacyControls() {
        view.addSubview(legacyControls)
        view.addSubview(fixedRightRail)

        let centerX = legacyControls.centerXAnchor.constraint(equalTo: view.leadingAnchor)
        let centerY = legacyControls.centerYAnchor.constraint(equalTo: view.topAnchor)
        controlCenterXConstraint = centerX
        controlCenterYConstraint = centerY

        NSLayoutConstraint.activate([
            centerX,
            centerY,
            fixedRightRail.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            fixedRightRail.centerYAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerYAnchor)
        ])

        let pan = UIPanGestureRecognizer(target: self, action: #selector(handleLegacyControlPan(_:)))
        pan.cancelsTouchesInView = false
        legacyControls.addGestureRecognizer(pan)
    }

    private func buildKeyboardPanel() {
        keyboardPanel.translatesAutoresizingMaskIntoConstraints = false
        keyboardPanel.layer.cornerRadius = 8
        keyboardPanel.layer.masksToBounds = true
        keyboardPanel.isHidden = true
        view.addSubview(keyboardPanel)

        keyboardTextField.translatesAutoresizingMaskIntoConstraints = false
        keyboardTextField.borderStyle = .none
        keyboardTextField.backgroundColor = UIColor.white.withAlphaComponent(0.18)
        keyboardTextField.textColor = .white
        keyboardTextField.tintColor = .systemGreen
        keyboardTextField.returnKeyType = .send
        keyboardTextField.autocorrectionType = .no
        keyboardTextField.autocapitalizationType = .none
        keyboardTextField.layer.cornerRadius = 6
        keyboardTextField.layer.masksToBounds = true
        keyboardTextField.attributedPlaceholder = NSAttributedString(
            string: "Text",
            attributes: [.foregroundColor: UIColor.white.withAlphaComponent(0.55)]
        )

        configureKeyboardIcon(keyboardEnterButton, systemName: "return", label: "Enter")
        configureKeyboardIcon(keyboardBackspaceButton, systemName: "delete.left.fill", label: "Backspace")
        keyboardEnterButton.addTarget(self, action: #selector(sendKeyboardEnter), for: .touchUpInside)
        keyboardBackspaceButton.addTarget(self, action: #selector(sendKeyboardBackspace), for: .touchUpInside)

        let stack = UIStackView(arrangedSubviews: [keyboardTextField, keyboardEnterButton, keyboardBackspaceButton])
        stack.axis = .horizontal
        stack.spacing = 6
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        keyboardPanel.contentView.addSubview(stack)

        let centerX = keyboardPanel.centerXAnchor.constraint(equalTo: view.leadingAnchor)
        let centerY = keyboardPanel.centerYAnchor.constraint(equalTo: view.topAnchor)
        let width = keyboardPanel.widthAnchor.constraint(equalToConstant: 480)
        keyboardPanelCenterXConstraint = centerX
        keyboardPanelCenterYConstraint = centerY
        keyboardPanelWidthConstraint = width

        NSLayoutConstraint.activate([
            centerX,
            centerY,
            width,
            keyboardPanel.heightAnchor.constraint(equalToConstant: 40),
            stack.leadingAnchor.constraint(equalTo: keyboardPanel.contentView.leadingAnchor, constant: 8),
            stack.trailingAnchor.constraint(equalTo: keyboardPanel.contentView.trailingAnchor, constant: -8),
            stack.topAnchor.constraint(equalTo: keyboardPanel.contentView.topAnchor, constant: 6),
            stack.bottomAnchor.constraint(equalTo: keyboardPanel.contentView.bottomAnchor, constant: -6),
            keyboardEnterButton.widthAnchor.constraint(equalToConstant: 34),
            keyboardBackspaceButton.widthAnchor.constraint(equalToConstant: 34)
        ])

        let pan = UIPanGestureRecognizer(target: self, action: #selector(handleKeyboardPanelPan(_:)))
        pan.cancelsTouchesInView = false
        keyboardPanel.addGestureRecognizer(pan)
    }

    private func configureKeyboardIcon(_ button: UIButton, systemName: String, label: String) {
        button.setImage(UIImage(systemName: systemName), for: .normal)
        button.tintColor = .white
        button.backgroundColor = UIColor.white.withAlphaComponent(0.25)
        button.layer.cornerRadius = 6
        button.accessibilityLabel = label
    }

    private func buildInputToast() {
        inputToast.translatesAutoresizingMaskIntoConstraints = false
        inputToast.font = .monospacedDigitSystemFont(ofSize: 11, weight: .bold)
        inputToast.textColor = .white
        inputToast.backgroundColor = UIColor.black.withAlphaComponent(0.58)
        inputToast.textAlignment = .center
        inputToast.layer.cornerRadius = 11
        inputToast.layer.masksToBounds = true
        inputToast.alpha = 0
        view.addSubview(inputToast)
        NSLayoutConstraint.activate([
            inputToast.centerXAnchor.constraint(equalTo: view.safeAreaLayoutGuide.centerXAnchor),
            inputToast.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -8),
            inputToast.heightAnchor.constraint(equalToConstant: 24),
            inputToast.widthAnchor.constraint(greaterThanOrEqualToConstant: 150)
        ])
    }

    private func positionLegacyControlsIfNeeded() {
        guard !controlPositionReady,
              view.bounds.width > 0,
              view.bounds.height > 0,
              let centerX = controlCenterXConstraint,
              let centerY = controlCenterYConstraint else { return }
        let savedX = UserDefaults.standard.object(forKey: "oplink.pc.control.xRatio") as? Double
        let savedY = UserDefaults.standard.object(forKey: "oplink.pc.control.yRatio") as? Double
        if let savedX, let savedY {
            centerX.constant = CGFloat(savedX) * view.bounds.width
            centerY.constant = CGFloat(savedY) * view.bounds.height
        } else {
            let safe = view.safeAreaLayoutGuide.layoutFrame
            centerX.constant = safe.maxX - 86
            centerY.constant = safe.minY + safe.height * 0.66
        }
        controlPositionReady = true
    }

    @objc private func handleLegacyControlPan(_ gesture: UIPanGestureRecognizer) {
        guard let centerX = controlCenterXConstraint,
              let centerY = controlCenterYConstraint else { return }
        switch gesture.state {
        case .began:
            controlDragStart = CGPoint(x: centerX.constant, y: centerY.constant)
            legacyControls.layer.borderWidth = 1
            legacyControls.layer.borderColor = UIColor.systemGreen.withAlphaComponent(0.55).cgColor
        case .changed:
            let translation = gesture.translation(in: view)
            centerX.constant = controlDragStart.x + translation.x
            centerY.constant = controlDragStart.y + translation.y
            clampLegacyControls(save: false)
        case .ended, .cancelled, .failed:
            clampLegacyControls(save: true)
            legacyControls.layer.borderWidth = 0
            legacyControls.layer.borderColor = nil
        default:
            break
        }
    }

    private func clampLegacyControls(save: Bool) {
        guard controlPositionReady,
              let centerX = controlCenterXConstraint,
              let centerY = controlCenterYConstraint,
              view.bounds.width > 0,
              view.bounds.height > 0 else { return }
        let safe = view.safeAreaLayoutGuide.layoutFrame
        let halfWidth = max(19, legacyControls.bounds.width / 2)
        let halfHeight = max(19, legacyControls.bounds.height / 2)
        centerX.constant = min(
            max(centerX.constant, safe.minX + halfWidth),
            safe.maxX - 54 - halfWidth
        )
        centerY.constant = min(
            max(centerY.constant, safe.minY + halfHeight),
            safe.maxY - halfHeight
        )
        if save {
            UserDefaults.standard.set(centerX.constant / view.bounds.width, forKey: "oplink.pc.control.xRatio")
            UserDefaults.standard.set(centerY.constant / view.bounds.height, forKey: "oplink.pc.control.yRatio")
        }
    }

    private func positionKeyboardPanelIfNeeded() {
        guard !keyboardPanelPositionReady,
              view.bounds.width > 0,
              view.bounds.height > 0,
              let centerX = keyboardPanelCenterXConstraint,
              let centerY = keyboardPanelCenterYConstraint else { return }
        let savedX = UserDefaults.standard.object(forKey: "oplink.pc.keyboard.xRatio") as? Double
        let savedY = UserDefaults.standard.object(forKey: "oplink.pc.keyboard.yRatio") as? Double
        if let savedX, let savedY {
            centerX.constant = CGFloat(savedX) * view.bounds.width
            centerY.constant = CGFloat(savedY) * view.bounds.height
        } else {
            let safe = view.safeAreaLayoutGuide.layoutFrame
            centerX.constant = safe.midX
            centerY.constant = safe.minY + 34
        }
        keyboardPanelPositionReady = true
    }

    private func updateKeyboardPanelWidth() {
        guard let width = keyboardPanelWidthConstraint else { return }
        let safeWidth = view.safeAreaLayoutGuide.layoutFrame.width
        guard safeWidth > 0 else { return }
        width.constant = max(260, min(520, floor(safeWidth * 0.75)))
    }

    @objc private func handleKeyboardPanelPan(_ gesture: UIPanGestureRecognizer) {
        guard let centerX = keyboardPanelCenterXConstraint,
              let centerY = keyboardPanelCenterYConstraint else { return }
        switch gesture.state {
        case .began:
            keyboardPanelDragStart = CGPoint(x: centerX.constant, y: centerY.constant)
            keyboardPanel.layer.borderWidth = 1
            keyboardPanel.layer.borderColor = UIColor.systemGreen.withAlphaComponent(0.65).cgColor
        case .changed:
            let translation = gesture.translation(in: view)
            centerX.constant = keyboardPanelDragStart.x + translation.x
            centerY.constant = keyboardPanelDragStart.y + translation.y
            clampKeyboardPanel(save: false)
        case .ended, .cancelled, .failed:
            clampKeyboardPanel(save: true)
            keyboardPanel.layer.borderWidth = 0
            keyboardPanel.layer.borderColor = nil
        default:
            break
        }
    }

    private func clampKeyboardPanel(save: Bool) {
        guard keyboardPanelPositionReady,
              let centerX = keyboardPanelCenterXConstraint,
              let centerY = keyboardPanelCenterYConstraint,
              view.bounds.width > 0,
              view.bounds.height > 0 else { return }
        let safe = view.safeAreaLayoutGuide.layoutFrame
        let halfWidth = max(1, keyboardPanel.bounds.width / 2)
        let halfHeight = max(1, keyboardPanel.bounds.height / 2)
        centerX.constant = min(max(centerX.constant, safe.minX + halfWidth), safe.maxX - halfWidth)
        centerY.constant = min(max(centerY.constant, safe.minY + halfHeight), safe.maxY - halfHeight)
        if save {
            UserDefaults.standard.set(centerX.constant / view.bounds.width, forKey: "oplink.pc.keyboard.xRatio")
            UserDefaults.standard.set(centerY.constant / view.bounds.height, forKey: "oplink.pc.keyboard.yRatio")
        }
    }

    private func collapseLegacyControls() {
        legacyControls.setExpanded(false, animated: true)
        streamSlotPicker.isHidden = true
    }

    private func showInputToast(_ text: String, good: Bool = true) {
        inputToastTask?.cancel()
        inputToast.text = "  \(text)  "
        inputToast.textColor = good ? .white : UIColor.systemRed
        view.bringSubviewToFront(inputToast)
        UIView.animate(withDuration: 0.12) { self.inputToast.alpha = 1 }
        let task = DispatchWorkItem { [weak self] in
            UIView.animate(withDuration: 0.2) { self?.inputToast.alpha = 0 }
        }
        inputToastTask = task
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2, execute: task)
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
            self.touchOverlay.isUserInteractionEnabled = self.latestResponse?.input.enabled == true
                && self.latestResponse?.input.executionOwner == "GUI_TEST_PC"
            self.setStatus("Slot \(self.selectedSlot) 首幀完成", good: true)
            self.updateMetrics()
            if self.viewerIsForeground, let baseURL = self.configuredBaseURL() {
                self.prewarmAdjacentStreams(baseURL: baseURL, around: self.selectedSlot)
            }
        }
        frameMonitor.onSizeChanged = { [weak self] size in
            self?.renderedSize = size
            self?.updateMetrics()
        }
        touchOverlay.onCommand = { [weak self] command in
            guard let self else { return }
            if command.action == "down",
               self.legacyControls.isExpanded || !self.streamSlotPicker.isHidden {
                self.collapseLegacyControls()
                self.suppressTouchSequenceForControlCollapse = true
                return
            }
            if self.suppressTouchSequenceForControlCollapse {
                if command.action == "up" || command.action == "cancel" {
                    self.suppressTouchSequenceForControlCollapse = false
                }
                return
            }
            self.enqueueInput(command)
        }
        touchOverlay.onTouchOutsideVideo = { [weak self] in
            guard let self,
                  self.legacyControls.isExpanded || !self.streamSlotPicker.isHidden else { return }
            self.collapseLegacyControls()
            self.suppressTouchSequenceForControlCollapse = false
        }

        streamSlotPicker.onSelectSlot = { [weak self] slot in
            self?.connect(slot: slot)
        }

        legacyControls.onPrevious = { [weak self] in self?.previousSlotTapped() }
        legacyControls.onList = { [weak self] in self?.showStreamSlotPicker() }
        legacyControls.onNext = { [weak self] in self?.nextSlotTapped() }
        legacyControls.onSettings = { [weak self] in self?.presentHostSettings() }
        legacyControls.onExpandedChanged = { [weak self] _ in
            self?.view.layoutIfNeeded()
            self?.clampLegacyControls(save: false)
        }
        fixedRightRail.onControlPanel = { [weak self] in self?.showGUIPanel() }
        fixedRightRail.onKeyboard = { [weak self] in self?.toggleKeyboardPanel() }

        guiPanel.onClose = { [weak self] in self?.guiPanel.isHidden = true }
        guiPanel.onRefresh = { [weak self] in self?.refreshGUIBridgeState() }
        guiPanel.onPlay = { [weak self] slots, modules in self?.sendModuleChain(slots: slots, modules: modules) }
        guiPanel.onStopAll = { [weak self] in self?.sendStopAll() }
        guiPanel.onStopSlot = { [weak self] slot in self?.sendStopSlot(slot) }
        guiPanel.onLauncher = { [weak self] action, slots in self?.sendLauncher(action: action, slots: slots) }
        guiPanel.onArrange = { [weak self] slots in self?.sendArrange(slots: slots) }
        guiPanel.onRequestPresetSave = { [weak self] index, currentName, modules in
            self?.promptPresetName(index: index, currentName: currentName, modules: modules)
        }
    }

    @objc private func previousSlotTapped() {
        connect(slot: adjacentAvailableSlot(step: -1))
    }

    @objc private func nextSlotTapped() {
        connect(slot: adjacentAvailableSlot(step: 1))
    }

    @objc private func showStreamSlotPicker() {
        legacyControls.setExpanded(false, animated: true)
        streamSlotPicker.apply(selectedSlot: selectedSlot, availableSlots: availableStreamSlots)
        streamSlotPicker.isHidden.toggle()
        guard !streamSlotPicker.isHidden else { return }
        view.bringSubviewToFront(streamSlotPicker)
        view.bringSubviewToFront(legacyControls)
    }

    @objc private func showGUIPanel() {
        collapseLegacyControls()
        closeKeyboardPanel()
        guiPanel.prepareForPresentation(streamSlot: selectedSlot)
        guiPanel.isHidden = false
        view.bringSubviewToFront(guiPanel)
        refreshGUIBridgeState()
    }

    private func toggleKeyboardPanel() {
        if keyboardPanel.isHidden {
            collapseLegacyControls()
            guiPanel.isHidden = true
            keyboardPanel.isHidden = false
            view.bringSubviewToFront(keyboardPanel)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.04) { [weak self] in
                self?.keyboardTextField.becomeFirstResponder()
            }
        } else {
            closeKeyboardPanel()
        }
    }

    private func closeKeyboardPanel() {
        keyboardFlushTask?.cancel()
        keyboardFlushTask = nil
        keyboardTextField.text = ""
        keyboardTextField.resignFirstResponder()
        keyboardPanel.isHidden = true
    }

    private func keyboardFlushDelay(for text: String) -> TimeInterval {
        if text.unicodeScalars.contains(where: { !$0.isASCII }) { return 0.08 }
        if text.count <= 16,
           text.range(of: #"^[A-Za-z0-9']+$"#, options: .regularExpression) != nil {
            return 1.6
        }
        return 0.36
    }

    @objc private func keyboardTextChanged() {
        scheduleKeyboardFlush()
    }

    private func scheduleKeyboardFlush() {
        keyboardFlushTask?.cancel()
        guard let text = keyboardTextField.text, !text.isEmpty else { return }
        let task = DispatchWorkItem { [weak self] in
            self?.sendKeyboardFieldPayload(sendEnter: false, cancelPending: false)
        }
        keyboardFlushTask = task
        DispatchQueue.main.asyncAfter(deadline: .now() + keyboardFlushDelay(for: text), execute: task)
    }

    @objc private func sendKeyboardEnter() {
        sendKeyboardFieldPayload(sendEnter: true)
    }

    @objc private func sendKeyboardBackspace() {
        enqueueKeyboardKey("backspace")
    }

    private func sendKeyboardFieldPayload(sendEnter: Bool, cancelPending: Bool = true) {
        if cancelPending {
            keyboardFlushTask?.cancel()
        }
        keyboardFlushTask = nil
        if keyboardTextField.markedTextRange != nil {
            scheduleKeyboardFlush()
            return
        }
        let text = keyboardTextField.text ?? ""
        if !text.isEmpty {
            keyboardTextField.text = ""
            enqueueKeyboardText(text)
        }
        if sendEnter {
            enqueueKeyboardKey("enter")
        }
    }

    private func beginViewerSession() {
        guard !viewerIsForeground else { return }
        viewerIsForeground = true
        sendViewerState("active", slot: selectedSlot)
        viewerHeartbeatTimer?.invalidate()
        let timer = Timer(
            timeInterval: 3,
            target: self,
            selector: #selector(sendViewerHeartbeat),
            userInfo: nil,
            repeats: true
        )
        viewerHeartbeatTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    @objc private func sendViewerHeartbeat() {
        guard viewerIsForeground else { return }
        sendViewerState("active", slot: selectedSlot)
    }

    @objc private func applicationDidEnterBackground() {
        guard viewerIsForeground else { return }
        viewerIsForeground = false
        viewerHeartbeatTimer?.invalidate()
        viewerHeartbeatTimer = nil
        connectionSequence += 1
        prewarmSequence += 1
        sendViewerState("background", slot: selectedSlot, allowBackgroundExecution: true)
        resetWHEPClients()
        touchOverlay.isUserInteractionEnabled = false
    }

    @objc private func applicationDidBecomeActive() {
        guard isViewLoaded, view.window != nil, !viewerIsForeground else { return }
        beginViewerSession()
        connect(slot: selectedSlot)
        refreshGUIBridgeState()
    }

    private func sendViewerState(
        _ state: String,
        slot: Int?,
        allowBackgroundExecution: Bool = false
    ) {
        guard let baseURL = configuredBaseURL() else { return }
        let backgroundTask = allowBackgroundExecution
            ? UIApplication.shared.beginBackgroundTask(withName: "OPLINK viewer state", expirationHandler: nil)
            : UIBackgroundTaskIdentifier.invalid
        streamAPI.updateViewer(baseURL: baseURL, state: state, slot: slot) { _ in
            guard backgroundTask != .invalid else { return }
            DispatchQueue.main.async {
                UIApplication.shared.endBackgroundTask(backgroundTask)
            }
        }
    }

    private func adjacentAvailableSlot(step: Int) -> Int {
        adjacentAvailableSlot(from: selectedSlot, step: step)
    }

    private func connect(slot: Int) {
        guard (1...15).contains(slot) else { return }
        guard let baseURL = configuredBaseURL() else {
            presentHostSettings()
            return
        }
        if viewerIsForeground {
            sendViewerState("active", slot: slot)
        }
        selectedSlot = slot
        connectionSequence += 1
        let sequence = connectionSequence
        switchStartedAt = Date()
        pendingWHEPSlot = slot
        lastSwitchMilliseconds = nil
        lastPublisherActivationMilliseconds = nil
        lastWHEPConnectMilliseconds = nil
        lastInputRTTMilliseconds = nil
        lastHostToHIDMilliseconds = nil
        inputQueue.removeAll()
        inputInFlight = false
        touchOverlay.isUserInteractionEnabled = false
        refreshStreamControls()
        setStatus("檢查 Slot \(slot) 視窗", good: false)
        updateMetrics()

        if desiredWarmSlots.contains(slot),
           let client = whepClients[slot],
           client.isReady,
           let response = latestResponse,
           let source = response.sources.first(where: { $0.slot == slot }),
           source.ok,
           source.aspectIs16x9 == true {
            updateSourceLabel(source: source, response: response)
            lastPublisherActivationMilliseconds = 0
            lastWHEPConnectMilliseconds = 0
            displayWHEP(slot: slot)
            activateWarmPublisherInBackground(baseURL: baseURL, slot: slot, sequence: sequence)
            return
        }

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
                    self.connectOrDisplayWHEP(baseURL: baseURL, slot: slot)
                }
            }
        }
    }

    private func activateWarmPublisherInBackground(baseURL: URL, slot: Int, sequence: Int) {
        streamAPI.activate(baseURL: baseURL, slot: slot) { [weak self] result in
            DispatchQueue.main.async {
                guard let self,
                      sequence == self.connectionSequence,
                      self.selectedSlot == slot else { return }
                switch result {
                case .failure(let error):
                    self.setStatus("畫面已切換；主機狀態更新失敗：\(error.localizedDescription)", good: false)
                case .success(let activation):
                    guard activation.ok,
                          activation.publisherAlive,
                          activation.activeSlot == slot else {
                        self.setStatus("畫面已切換；主機 active Slot 更新失敗", good: false)
                        return
                    }
                    self.lastPublisherActivationMilliseconds = activation.activationMs
                    self.updateMetrics()
                }
            }
        }
    }

    private func whepClient(baseURL _: URL, slot: Int) -> WHEPClient {
        if let existing = whepClients[slot] { return existing }
        let client = WHEPClient()
        client.onReady = { [weak self] elapsedMs in
            guard let self else { return }
            if self.selectedSlot == slot {
                self.lastWHEPConnectMilliseconds = elapsedMs
                self.displayWHEP(slot: slot)
            }
        }
        client.onStateChanged = { [weak self] state in
            guard let self, self.selectedSlot == slot else { return }
            self.setStatus(state, good: state == "ICE 已連線" || state == "解碼中")
        }
        client.onError = { [weak self] error in
            guard let self else { return }
            self.whepClients[slot]?.stop()
            self.whepClients.removeValue(forKey: slot)
            if self.selectedSlot == slot {
                self.setStatus(error.localizedDescription, good: false)
            }
        }
        whepClients[slot] = client
        return client
    }

    private func connectOrDisplayWHEP(baseURL: URL, slot: Int) {
        let client = whepClient(baseURL: baseURL, slot: slot)
        if client.isReady {
            lastWHEPConnectMilliseconds = 0
            displayWHEP(slot: slot)
            return
        }
        guard !client.isStarted else { return }
        client.connect(endpoint: StreamEndpoint.whep(base: baseURL, slot: slot))
    }

    private func displayWHEP(slot: Int) {
        guard selectedSlot == slot,
              pendingWHEPSlot == slot,
              let client = whepClients[slot],
              client.isReady else { return }
        if let activeWHEPSlot, activeWHEPSlot != slot {
            whepClients[activeWHEPSlot]?.setRenderer(nil)
        }
        frameMonitor.reset()
        renderedFPS = 0
        renderedSize = .zero
        client.setRenderer(frameMonitor)
        activeWHEPSlot = slot
        pendingWHEPSlot = nil
        pruneWHEPClients()
    }

    private func adjacentAvailableSlot(from origin: Int, step: Int) -> Int {
        for offset in 1...15 {
            let zeroBased = (origin - 1 + step * offset + 150) % 15
            let candidate = zeroBased + 1
            if availableStreamSlots.contains(candidate) { return candidate }
        }
        return origin
    }

    private func prewarmAdjacentStreams(baseURL: URL, around slot: Int) {
        let previous = adjacentAvailableSlot(from: slot, step: -1)
        let next = adjacentAvailableSlot(from: slot, step: 1)
        var ordered = [slot]
        if !ordered.contains(previous) { ordered.append(previous) }
        if !ordered.contains(next) { ordered.append(next) }
        desiredWarmSlots = Set(ordered)
        prewarmSequence += 1
        let sequence = prewarmSequence
        streamAPI.prewarm(baseURL: baseURL, slots: ordered) { [weak self] result in
            DispatchQueue.main.async {
                guard let self,
                      sequence == self.prewarmSequence,
                      self.selectedSlot == slot else { return }
                guard case .success(let response) = result else { return }
                for warmSlot in ordered where response.warmSlots.contains(warmSlot) {
                    let client = self.whepClient(baseURL: baseURL, slot: warmSlot)
                    if !client.isReady && !client.isStarted {
                        client.connect(endpoint: StreamEndpoint.whep(base: baseURL, slot: warmSlot))
                    }
                }
                self.pruneWHEPClients()
            }
        }
    }

    private func pruneWHEPClients() {
        var keep = desiredWarmSlots
        if let activeWHEPSlot { keep.insert(activeWHEPSlot) }
        for slot in Array(whepClients.keys) where !keep.contains(slot) {
            whepClients[slot]?.stop()
            whepClients.removeValue(forKey: slot)
        }
    }

    private func resetWHEPClients() {
        whepClients.values.forEach { $0.stop() }
        whepClients.removeAll()
        activeWHEPSlot = nil
        pendingWHEPSlot = nil
        desiredWarmSlots.removeAll()
        prewarmSequence += 1
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
        let whepText = lastWHEPConnectMilliseconds.map { "\($0)ms" } ?? "--"
        let inputRTT = lastInputRTTMilliseconds.map { "\($0)ms" } ?? "--"
        let hostToHID = lastHostToHIDMilliseconds.map { String(format: "%.1fms", $0) } ?? "--"
        metricsLabel.text = "SLOT \(selectedSlot)   VIDEO \(sizeText)   FPS \(renderedFPS)   SWITCH \(switchText)\nPUBLISHER \(activationText)   WHEP \(whepText)   INPUT RTT \(inputRTT)   HOST→HID \(hostToHID)   BACKEND \(lastInputBackend)"
        if let switchMs = lastSwitchMilliseconds {
            targetLabel.textColor = switchMs <= 1000
                ? UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
                : UIColor(red: 1, green: 0.35, blue: 0.26, alpha: 1)
        }
    }

    private func enqueueInput(_ command: TouchOverlayView.Command) {
        let sentAt = Int64(Date().timeIntervalSince1970 * 1000)
        enqueueRemoteInput(.touch(slot: selectedSlot, command: command, sentAtMs: sentAt))
    }

    private func enqueueKeyboardText(_ text: String) {
        let sentAt = Int64(Date().timeIntervalSince1970 * 1000)
        enqueueRemoteInput(.text(slot: selectedSlot, value: text, sentAtMs: sentAt))
    }

    private func enqueueKeyboardKey(_ key: String) {
        let sentAt = Int64(Date().timeIntervalSince1970 * 1000)
        enqueueRemoteInput(.key(slot: selectedSlot, value: key, sentAtMs: sentAt))
    }

    private func enqueueRemoteInput(_ request: StreamInputRequest) {
        guard configuredBaseURL() != nil else {
            setStatus("Tailnet host URL is required", good: false)
            showInputToast("HOST URL REQUIRED", good: false)
            closeKeyboardPanel()
            presentHostSettings()
            return
        }
        guard configuredInputToken() != nil else {
            setStatus("Input pairing token is required", good: false)
            showInputToast("INPUT TOKEN REQUIRED", good: false)
            closeKeyboardPanel()
            presentHostSettings()
            return
        }
        let item = QueuedInput(request: request)
        if request.action == "move", inputQueue.last?.request.action == "move" {
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
        let requestStartedAt = Date()
        streamAPI.sendInput(baseURL: baseURL, token: token, input: queued.request) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                self.inputInFlight = false
                switch result {
                case .success(let response):
                    guard response.executionOwner == "GUI_TEST_PC",
                          response.relayedTo == "GUI_TEST_PC" else {
                        self.inputQueue.removeAll()
                        self.setStatus("Rejected input response outside GUI_TEST_PC", good: false)
                        self.showInputToast("INPUT OWNER REJECTED", good: false)
                        return
                    }
                    self.lastInputRTTMilliseconds = Int(Date().timeIntervalSince(requestStartedAt) * 1000)
                    self.lastHostToHIDMilliseconds = response.hostToHIDAckMs
                    self.lastInputBackend = response.backend
                    self.updateMetrics()
                    let rtt = self.lastInputRTTMilliseconds ?? 0
                    self.showInputToast("\(response.action.uppercased())  RTT \(rtt)ms  HID \(String(format: "%.1f", response.hostToHIDAckMs))ms")
                case .failure(let error):
                    self.inputQueue.removeAll()
                    self.setStatus(error.localizedDescription, good: false)
                    self.showInputToast(error.localizedDescription, good: false)
                    if let inputError = error as? StreamInputError,
                       inputError.isInvalidPairingToken {
                        UserDefaults.standard.removeObject(forKey: Defaults.inputToken)
                        self.closeKeyboardPanel()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
                            self?.showHostSettings(clearToken: true)
                        }
                    }
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

        guiAPI.fetchModuleGroups(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let response):
                    self.bridgeModuleGroups = response.groups
                    self.applyGUIBridgeState()
                case .failure(let error):
                    self.bridgeModuleGroups = []
                    if !self.guiPanel.isHidden { self.guiPanel.setStatus(error.localizedDescription, good: false) }
                }
            }
        }

        guiAPI.fetchModuleChainPresets(baseURL: baseURL) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let response):
                    self.bridgeModulePresets = response.presets
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
            groups: bridgeModuleGroups,
            presets: bridgeModulePresets,
            heartbeatFresh: bridgeHeartbeatFresh
        )
    }

    private func promptPresetName(index: Int, currentName: String, modules: [String]) {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(
            title: "儲存連串 \(index)",
            message: modules.joined(separator: " > "),
            preferredStyle: .alert
        )
        alert.addTextField { field in
            field.text = currentName
            field.placeholder = "連串名稱"
            field.clearButtonMode = .whileEditing
        }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel))
        alert.addAction(UIAlertAction(title: "儲存", style: .default) { [weak self, weak alert] _ in
            guard let self else { return }
            let name = alert?.textFields?.first?.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            self.savePreset(index: index, name: name, modules: modules)
        })
        present(alert, animated: true)
    }

    private func savePreset(index: Int, name: String, modules: [String]) {
        guard let baseURL = configuredBaseURL() else { return }
        guard !name.isEmpty else {
            guiPanel.setStatus("連串名稱不可留空。", good: false)
            return
        }
        guiAPI.saveModuleChainPreset(
            baseURL: baseURL,
            index: index,
            name: name,
            modules: modules
        ) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let response):
                    self.bridgeModulePresets = response.presets
                    self.guiPanel.finishPresetSave(response.presets)
                    self.guiPanel.setStatus("連串 \(index) 已儲存：\(response.preset.name)", good: true)
                case .failure(let error):
                    self.guiPanel.setStatus(error.localizedDescription, good: false)
                }
            }
        }
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
        showHostSettings(clearToken: false)
    }

    private func showHostSettings(clearToken: Bool) {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(
            title: "Tailnet Windows 主機",
            message: clearToken
                ? "Input pairing token 已失效。請輸入 Windows 串流主機目前顯示的 token；不要輸入 Tailscale auth key。"
                : "HTTPS 主機供串流及 GUI_TEST_PC bridge 使用。Input pairing token 在使用觸控或鍵盤控制時必填；純觀看才可留空。不要輸入 Tailscale auth key。",
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
            field.placeholder = "Input pairing token（控制時必填）"
            field.text = clearToken ? "" : UserDefaults.standard.string(forKey: Defaults.inputToken)
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
                self.resetWHEPClients()
                self.beginViewerSession()
                self.connect(slot: self.selectedSlot)
                self.refreshGUIBridgeState()
            } catch {
                self.setStatus(error.localizedDescription, good: false)
            }
        })
        let shouldFocusToken = clearToken || configuredInputToken() == nil
        present(alert, animated: true) {
            if shouldFocusToken {
                alert.textFields?[1].becomeFirstResponder()
            }
        }
    }

    private func requestLandscape() {
        guard #available(iOS 16.0, *), let scene = view.window?.windowScene else { return }
        setNeedsUpdateOfSupportedInterfaceOrientations()
        scene.requestGeometryUpdate(.iOS(interfaceOrientations: .landscapeRight))
    }
}

extension StreamViewController: UITextFieldDelegate {
    func textFieldDidEndEditing(_ textField: UITextField) {
        guard textField === keyboardTextField else { return }
        keyboardFlushTask?.cancel()
        keyboardFlushTask = nil
        keyboardTextField.text = ""
        keyboardPanel.isHidden = true
    }

    func textField(
        _ textField: UITextField,
        shouldChangeCharactersIn range: NSRange,
        replacementString string: String
    ) -> Bool {
        guard textField === keyboardTextField else { return true }
        if string.isEmpty, range.length == 0, (textField.text ?? "").isEmpty {
            enqueueKeyboardKey("backspace")
        }
        return true
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        guard textField === keyboardTextField else { return true }
        sendKeyboardFieldPayload(sendEnter: true)
        return true
    }
}
