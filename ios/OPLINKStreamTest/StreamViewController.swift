import UIKit
import WebRTC

final class StreamViewController: UIViewController {
    private enum Defaults {
        static let host = "oplink.streamTest.host"
    }

    private let videoView = RTCMTLVideoView(frame: .zero)
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
        NSLayoutConstraint.activate([
            videoView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            videoView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            videoView.topAnchor.constraint(equalTo: view.topAnchor),
            videoView.bottomAnchor.constraint(equalTo: view.bottomAnchor)
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
        metricsLabel.numberOfLines = 1
        targetLabel.font = .systemFont(ofSize: 10, weight: .bold)
        targetLabel.textColor = UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
        targetLabel.text = "TARGET 720P / 30 FPS / SWITCH < 1000 MS"

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
                    self.updateSourceLabel(source: source, profile: response.profile, encoder: response.encoder)
                    self.whepClient.connect(
                        endpoint: StreamEndpoint.whep(base: baseURL, slot: slot),
                        renderer: self.frameMonitor
                    )
                }
            }
        }
    }

    private func updateSourceLabel(source: StreamSource, profile: StreamProfile, encoder: String) {
        let logical = source.clientLogical.map { "\($0.w)x\($0.h)" } ?? "?"
        let capture = source.capturePhysicalExpected.map { "\($0.w)x\($0.h)" } ?? "?"
        sourceLabel.text = "SRC \(logical) | WGC \(capture) | OUT \(profile.encoded.w)x\(profile.encoded.h) | \(encoder)"
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
        metricsLabel.text = "SLOT \(selectedSlot)   VIDEO \(sizeText)   FPS \(renderedFPS)   SWITCH \(switchText)"
        if let milliseconds = lastSwitchMilliseconds {
            targetLabel.textColor = milliseconds <= 1000
                ? UIColor(red: 0.69, green: 0.93, blue: 0.47, alpha: 1)
                : UIColor(red: 1, green: 0.35, blue: 0.26, alpha: 1)
        }
    }

    private func configuredBaseURL() -> URL? {
        guard let value = UserDefaults.standard.string(forKey: Defaults.host) else { return nil }
        return try? StreamEndpoint.normalizedHost(value)
    }

    @objc private func presentHostSettings() {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(
            title: "Tailnet Windows 主機",
            message: "輸入啟動器顯示的 HTTPS 主機，不要加入 /oplink-test 或 key。",
            preferredStyle: .alert
        )
        alert.addTextField { field in
            field.placeholder = "https://windows-host.example.ts.net"
            field.text = UserDefaults.standard.string(forKey: Defaults.host)
            field.keyboardType = .URL
            field.autocapitalizationType = .none
            field.autocorrectionType = .no
        }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel))
        alert.addAction(UIAlertAction(title: "儲存並連線", style: .default) { [weak self, weak alert] _ in
            guard let self, let value = alert?.textFields?.first?.text else { return }
            do {
                let base = try StreamEndpoint.normalizedHost(value)
                UserDefaults.standard.set(base.absoluteString, forKey: Defaults.host)
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

