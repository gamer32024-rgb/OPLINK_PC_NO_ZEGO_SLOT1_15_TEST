import UIKit

final class GUIControlPanelView: UIView {
    var onClose: (() -> Void)?
    var onRefresh: (() -> Void)?
    var onPlay: (([Int], [String]) -> Void)?
    var onStopAll: (() -> Void)?
    var onStopSlot: ((Int) -> Void)?
    var onLauncher: ((String, [Int]) -> Void)?
    var onArrange: (([Int]) -> Void)?

    private let card = UIView()
    private let statusLabel = UILabel()
    private let slotSummaryLabel = UILabel()
    private let moduleSummaryLabel = UILabel()
    private let moduleChooser = UIView()
    private let moduleChooserTitle = UILabel()
    private let moduleButtonStack = UIStackView()
    private var slotButtons: [UIButton] = []
    private var chainButtons: [UIButton] = []
    private var chooserStepButtons: [UIButton] = []

    private var selectedSlots = Set<Int>()
    private var runningSlots = Set<Int>()
    private var playingSlots = Set<Int>()
    private var slotPlaybackStatus: [String: String] = [:]
    private var modules: [String: [String]] = [:]
    private var moduleNames: [String] = []
    private var moduleChain = Array<String?>(repeating: nil, count: 10)
    private var activeChainIndex = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        buildLayout()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func apply(
        runningSlots: Set<Int>,
        playingSlots: Set<Int>,
        slotPlaybackStatus: [String: String],
        modules: [String: [String]]? = nil,
        heartbeatFresh: Bool
    ) {
        self.runningSlots = runningSlots
        self.playingSlots = playingSlots
        self.slotPlaybackStatus = slotPlaybackStatus
        if let modules {
            self.modules = modules
            moduleNames = modules.keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
            rebuildModuleButtons()
        }
        slotSummaryLabel.text = "視窗 \(runningSlots.count) / 15\(heartbeatFresh ? "" : "  HEARTBEAT 過期")"
        moduleSummaryLabel.text = "模組串列 \(moduleChain.compactMap { $0 }.count) / 10"
        refreshSlotButtons()
        refreshChainButtons()
    }

    func setStatus(_ text: String, good: Bool) {
        statusLabel.text = text
        statusLabel.textColor = good
            ? UIColor(red: 0.55, green: 0.95, blue: 0.62, alpha: 1)
            : UIColor(red: 1, green: 0.55, blue: 0.35, alpha: 1)
    }

    private func buildLayout() {
        backgroundColor = UIColor.black.withAlphaComponent(0.64)

        card.translatesAutoresizingMaskIntoConstraints = false
        card.backgroundColor = UIColor(red: 0.035, green: 0.065, blue: 0.075, alpha: 0.985)
        card.layer.cornerRadius = 18
        card.layer.borderWidth = 1
        card.layer.borderColor = UIColor.white.withAlphaComponent(0.14).cgColor
        addSubview(card)

        let title = UILabel()
        title.text = "GUI_TEST_PC"
        title.textColor = .white
        title.font = .systemFont(ofSize: 18, weight: .heavy)

        let owner = UILabel()
        owner.text = "BRIDGE ONLY  •  EXECUTION OWNER: WINDOWS GUI"
        owner.textColor = UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1)
        owner.font = .monospacedSystemFont(ofSize: 10, weight: .bold)

        let titleStack = UIStackView(arrangedSubviews: [title, owner])
        titleStack.axis = .vertical
        titleStack.spacing = 0

        let refresh = iconButton("arrow.clockwise", label: "更新 GUI_TEST_PC 狀態")
        refresh.addTarget(self, action: #selector(refreshTapped), for: .touchUpInside)
        let close = iconButton("xmark", label: "關閉控制面板")
        close.addTarget(self, action: #selector(closeTapped), for: .touchUpInside)
        let header = UIStackView(arrangedSubviews: [titleStack, UIView(), refresh, close])
        header.axis = .horizontal
        header.alignment = .center
        header.spacing = 8

        let slotsColumn = buildSlotsColumn()
        let modulesColumn = buildModulesColumn()
        let body = UIStackView(arrangedSubviews: [slotsColumn, modulesColumn])
        body.axis = .horizontal
        body.spacing = 18
        body.distribution = .fillEqually

        let mainStack = UIStackView(arrangedSubviews: [header, divider(), body])
        mainStack.axis = .vertical
        mainStack.spacing = 10
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(mainStack)

        NSLayoutConstraint.activate([
            card.leadingAnchor.constraint(equalTo: safeAreaLayoutGuide.leadingAnchor, constant: 16),
            card.trailingAnchor.constraint(equalTo: safeAreaLayoutGuide.trailingAnchor, constant: -16),
            card.topAnchor.constraint(equalTo: safeAreaLayoutGuide.topAnchor, constant: 12),
            card.bottomAnchor.constraint(equalTo: safeAreaLayoutGuide.bottomAnchor, constant: -12),
            mainStack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            mainStack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            mainStack.topAnchor.constraint(equalTo: card.topAnchor, constant: 12),
            mainStack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -12)
        ])

        buildModuleChooser()
        refreshSlotButtons()
        refreshChainButtons()
    }

    private func buildSlotsColumn() -> UIView {
        slotSummaryLabel.text = "視窗 0 / 15"
        styleSectionLabel(slotSummaryLabel)

        let grid = UIStackView()
        grid.axis = .vertical
        grid.spacing = 6
        grid.distribution = .fillEqually
        for rowIndex in 0..<3 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 6
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let slot = rowIndex * 5 + columnIndex + 1
                let button = UIButton(type: .system)
                button.tag = slot
                button.setTitle(String(format: "%02d", slot), for: .normal)
                button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 14, weight: .bold)
                button.layer.cornerRadius = 8
                button.addTarget(self, action: #selector(slotTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(equalToConstant: 35).isActive = true
                slotButtons.append(button)
                row.addArrangedSubview(button)
            }
            grid.addArrangedSubview(row)
        }

        let selectAll = textButton("全選", color: UIColor(white: 0.28, alpha: 1))
        selectAll.addTarget(self, action: #selector(selectAllTapped), for: .touchUpInside)
        let clear = textButton("清除", color: UIColor(white: 0.28, alpha: 1))
        clear.addTarget(self, action: #selector(clearSlotsTapped), for: .touchUpInside)
        let restart = textButton("重啟所選", color: UIColor(red: 0.72, green: 0.43, blue: 0.16, alpha: 1))
        restart.addTarget(self, action: #selector(restartSelectedTapped), for: .touchUpInside)
        let slotActions = actionRow([selectAll, clear, restart])

        let hint = UILabel()
        hint.text = "灰：未選　綠：已選　紅：播放中（點擊只中止該槽）"
        hint.textColor = UIColor.white.withAlphaComponent(0.55)
        hint.font = .systemFont(ofSize: 9, weight: .medium)

        let stack = UIStackView(arrangedSubviews: [slotSummaryLabel, grid, slotActions, hint])
        stack.axis = .vertical
        stack.spacing = 7
        return stack
    }

    private func buildModulesColumn() -> UIView {
        moduleSummaryLabel.text = "模組串列 0 / 10"
        styleSectionLabel(moduleSummaryLabel)

        let chainGrid = UIStackView()
        chainGrid.axis = .vertical
        chainGrid.spacing = 6
        chainGrid.distribution = .fillEqually
        for rowIndex in 0..<2 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 6
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let index = rowIndex * 5 + columnIndex
                let button = UIButton(type: .system)
                button.tag = index
                button.layer.cornerRadius = 8
                button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
                button.titleLabel?.numberOfLines = 2
                button.titleLabel?.textAlignment = .center
                button.addTarget(self, action: #selector(chainTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(equalToConstant: 42).isActive = true
                chainButtons.append(button)
                row.addArrangedSubview(button)
            }
            chainGrid.addArrangedSubview(row)
        }

        let play = textButton("交給 GUI 播放", color: UIColor(red: 0.1, green: 0.55, blue: 0.32, alpha: 1))
        play.addTarget(self, action: #selector(playTapped), for: .touchUpInside)
        let clear = textButton("清除串列", color: UIColor(white: 0.28, alpha: 1))
        clear.addTarget(self, action: #selector(clearChainTapped), for: .touchUpInside)
        let stop = textButton("中止全部", color: UIColor(red: 0.72, green: 0.18, blue: 0.16, alpha: 1))
        stop.addTarget(self, action: #selector(stopAllTapped), for: .touchUpInside)
        let moduleActions = actionRow([play, clear, stop])

        let launcherTitle = UILabel()
        launcherTitle.text = "啟動器（同樣只送 GUI bridge 命令）"
        styleSectionLabel(launcherTitle)

        let startAll = textButton("啟動全部", color: UIColor(red: 0.12, green: 0.44, blue: 0.5, alpha: 1))
        startAll.addTarget(self, action: #selector(startAllTapped), for: .touchUpInside)
        let startSelected = textButton("啟動所選", color: UIColor(red: 0.12, green: 0.44, blue: 0.5, alpha: 1))
        startSelected.addTarget(self, action: #selector(startSelectedTapped), for: .touchUpInside)
        let closeSelected = textButton("關閉所選", color: UIColor(red: 0.43, green: 0.3, blue: 0.18, alpha: 1))
        closeSelected.addTarget(self, action: #selector(closeSelectedTapped), for: .touchUpInside)
        let closeAll = textButton("關閉全部", color: UIColor(red: 0.55, green: 0.23, blue: 0.18, alpha: 1))
        closeAll.addTarget(self, action: #selector(closeAllTapped), for: .touchUpInside)
        let arrange = textButton("排列視窗", color: UIColor(red: 0.23, green: 0.34, blue: 0.5, alpha: 1))
        arrange.addTarget(self, action: #selector(arrangeTapped), for: .touchUpInside)
        let launchRow1 = actionRow([startAll, startSelected, arrange])
        let launchRow2 = actionRow([closeSelected, closeAll])

        statusLabel.text = "等待 GUI_TEST_PC 狀態"
        statusLabel.textColor = UIColor.white.withAlphaComponent(0.65)
        statusLabel.font = .monospacedSystemFont(ofSize: 10, weight: .semibold)
        statusLabel.numberOfLines = 2

        let stack = UIStackView(arrangedSubviews: [
            moduleSummaryLabel,
            chainGrid,
            moduleActions,
            launcherTitle,
            launchRow1,
            launchRow2,
            statusLabel
        ])
        stack.axis = .vertical
        stack.spacing = 7
        return stack
    }

    private func buildModuleChooser() {
        moduleChooser.translatesAutoresizingMaskIntoConstraints = false
        moduleChooser.backgroundColor = UIColor(red: 0.025, green: 0.055, blue: 0.065, alpha: 0.995)
        moduleChooser.layer.cornerRadius = 15
        moduleChooser.layer.borderWidth = 1
        moduleChooser.layer.borderColor = UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 0.65).cgColor
        moduleChooser.isHidden = true
        card.addSubview(moduleChooser)

        moduleChooserTitle.text = "選擇模組"
        moduleChooserTitle.textColor = .white
        moduleChooserTitle.font = .systemFont(ofSize: 16, weight: .bold)

        let close = iconButton("xmark", label: "關閉模組選單")
        close.addTarget(self, action: #selector(closeModuleChooserTapped), for: .touchUpInside)
        let clear = textButton("清除此格", color: UIColor(white: 0.25, alpha: 1))
        clear.addTarget(self, action: #selector(clearActiveStepTapped), for: .touchUpInside)
        clear.widthAnchor.constraint(equalToConstant: 90).isActive = true

        let header = UIStackView(arrangedSubviews: [moduleChooserTitle, UIView(), clear, close])
        header.axis = .horizontal
        header.alignment = .center
        header.spacing = 8

        let stepRow = UIStackView()
        stepRow.axis = .horizontal
        stepRow.spacing = 5
        stepRow.distribution = .fillEqually
        for index in 0..<10 {
            let button = UIButton(type: .system)
            button.tag = index
            button.setTitle("\(index + 1)", for: .normal)
            button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 11, weight: .bold)
            button.layer.cornerRadius = 7
            button.addTarget(self, action: #selector(chooserStepTapped(_:)), for: .touchUpInside)
            chooserStepButtons.append(button)
            stepRow.addArrangedSubview(button)
        }

        let scroll = UIScrollView()
        scroll.showsVerticalScrollIndicator = true
        moduleButtonStack.axis = .vertical
        moduleButtonStack.spacing = 6
        moduleButtonStack.translatesAutoresizingMaskIntoConstraints = false
        scroll.addSubview(moduleButtonStack)

        let stack = UIStackView(arrangedSubviews: [header, stepRow, scroll])
        stack.axis = .vertical
        stack.spacing = 10
        stack.translatesAutoresizingMaskIntoConstraints = false
        moduleChooser.addSubview(stack)

        NSLayoutConstraint.activate([
            moduleChooser.centerXAnchor.constraint(equalTo: card.centerXAnchor),
            moduleChooser.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            moduleChooser.widthAnchor.constraint(equalTo: card.widthAnchor, multiplier: 0.62),
            moduleChooser.heightAnchor.constraint(equalTo: card.heightAnchor, multiplier: 0.86),
            stack.leadingAnchor.constraint(equalTo: moduleChooser.leadingAnchor, constant: 14),
            stack.trailingAnchor.constraint(equalTo: moduleChooser.trailingAnchor, constant: -14),
            stack.topAnchor.constraint(equalTo: moduleChooser.topAnchor, constant: 12),
            stack.bottomAnchor.constraint(equalTo: moduleChooser.bottomAnchor, constant: -12),
            moduleButtonStack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            moduleButtonStack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            moduleButtonStack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            moduleButtonStack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
            moduleButtonStack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor)
        ])
    }

    private func rebuildModuleButtons() {
        for view in moduleButtonStack.arrangedSubviews {
            moduleButtonStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        if moduleNames.isEmpty {
            let empty = UILabel()
            empty.text = "GUI_TEST_PC 沒有可用模組"
            empty.textColor = UIColor.white.withAlphaComponent(0.6)
            empty.textAlignment = .center
            moduleButtonStack.addArrangedSubview(empty)
            return
        }
        for (index, name) in moduleNames.enumerated() {
            let button = textButton(name, color: UIColor(white: 0.19, alpha: 1))
            button.tag = index
            button.contentHorizontalAlignment = .left
            button.contentEdgeInsets = UIEdgeInsets(top: 0, left: 12, bottom: 0, right: 12)
            button.addTarget(self, action: #selector(moduleTapped(_:)), for: .touchUpInside)
            moduleButtonStack.addArrangedSubview(button)
        }
    }

    private func refreshSlotButtons() {
        for button in slotButtons {
            let slot = button.tag
            let running = runningSlots.contains(slot)
            let playing = playingSlots.contains(slot)
            let selected = selectedSlots.contains(slot)
            let background: UIColor
            let foreground: UIColor
            if playing {
                background = UIColor(red: 0.83, green: 0.17, blue: 0.16, alpha: 1)
                foreground = .white
            } else if selected {
                background = UIColor(red: 0.42, green: 0.88, blue: 0.49, alpha: 1)
                foreground = UIColor(red: 0.02, green: 0.13, blue: 0.06, alpha: 1)
            } else {
                background = UIColor(white: running ? 0.3 : 0.15, alpha: 1)
                foreground = .white
            }
            button.backgroundColor = background
            button.setTitleColor(foreground, for: .normal)
            button.alpha = running || selected || playing ? 1 : 0.62
            button.accessibilityHint = playing
                ? slotPlaybackStatus[String(slot)] ?? "播放中，點擊只中止此槽"
                : (running ? "在線" : "離線")
        }
    }

    private func refreshChainButtons() {
        for button in chainButtons {
            let index = button.tag
            let name = moduleChain[index]
            button.setTitle(name.map { "\(index + 1)\n\($0)" } ?? "\(index + 1)\n＋", for: .normal)
            button.backgroundColor = name == nil
                ? UIColor(white: 0.18, alpha: 1)
                : UIColor(red: 0.1, green: 0.4, blue: 0.35, alpha: 1)
            button.setTitleColor(.white, for: .normal)
        }
        for button in chooserStepButtons {
            let active = button.tag == activeChainIndex
            button.backgroundColor = active
                ? UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1)
                : UIColor(white: 0.2, alpha: 1)
            button.setTitleColor(active ? .black : .white, for: .normal)
        }
        moduleSummaryLabel.text = "模組串列 \(moduleChain.compactMap { $0 }.count) / 10"
        moduleChooserTitle.text = "第 \(activeChainIndex + 1) 格：選擇模組"
    }

    private func iconButton(_ systemName: String, label: String) -> UIButton {
        let button = UIButton(type: .system)
        button.setImage(UIImage(systemName: systemName), for: .normal)
        button.tintColor = .white
        button.backgroundColor = UIColor.white.withAlphaComponent(0.1)
        button.layer.cornerRadius = 15
        button.accessibilityLabel = label
        button.widthAnchor.constraint(equalToConstant: 30).isActive = true
        button.heightAnchor.constraint(equalToConstant: 30).isActive = true
        return button
    }

    private func textButton(_ title: String, color: UIColor) -> UIButton {
        let button = UIButton(type: .system)
        button.setTitle(title, for: .normal)
        button.setTitleColor(.white, for: .normal)
        button.titleLabel?.font = .systemFont(ofSize: 11, weight: .bold)
        button.titleLabel?.lineBreakMode = .byTruncatingTail
        button.backgroundColor = color
        button.layer.cornerRadius = 8
        button.heightAnchor.constraint(equalToConstant: 32).isActive = true
        return button
    }

    private func actionRow(_ buttons: [UIButton]) -> UIStackView {
        let row = UIStackView(arrangedSubviews: buttons)
        row.axis = .horizontal
        row.spacing = 6
        row.distribution = .fillEqually
        return row
    }

    private func divider() -> UIView {
        let view = UIView()
        view.backgroundColor = UIColor.white.withAlphaComponent(0.12)
        view.heightAnchor.constraint(equalToConstant: 1).isActive = true
        return view
    }

    private func styleSectionLabel(_ label: UILabel) {
        label.textColor = .white
        label.font = .systemFont(ofSize: 12, weight: .bold)
    }

    private func requireSelectedSlots() -> [Int]? {
        let slots = selectedSlots.sorted()
        if slots.isEmpty {
            setStatus("請先選擇至少一個遊戲視窗。", good: false)
            return nil
        }
        return slots
    }

    @objc private func closeTapped() { onClose?() }
    @objc private func refreshTapped() { onRefresh?() }

    @objc private func slotTapped(_ sender: UIButton) {
        let slot = sender.tag
        if playingSlots.contains(slot) {
            setStatus("正在送出 GAME \(slot) 單槽中止命令...", good: true)
            onStopSlot?(slot)
            return
        }
        if selectedSlots.contains(slot) {
            selectedSlots.remove(slot)
        } else {
            selectedSlots.insert(slot)
        }
        refreshSlotButtons()
    }

    @objc private func selectAllTapped() {
        selectedSlots = Set(1...15)
        refreshSlotButtons()
    }

    @objc private func clearSlotsTapped() {
        selectedSlots.removeAll()
        refreshSlotButtons()
    }

    @objc private func restartSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("restart", slots)
    }

    @objc private func chainTapped(_ sender: UIButton) {
        activeChainIndex = sender.tag
        refreshChainButtons()
        moduleChooser.isHidden = false
    }

    @objc private func chooserStepTapped(_ sender: UIButton) {
        activeChainIndex = sender.tag
        refreshChainButtons()
    }

    @objc private func moduleTapped(_ sender: UIButton) {
        guard moduleNames.indices.contains(sender.tag) else { return }
        moduleChain[activeChainIndex] = moduleNames[sender.tag]
        refreshChainButtons()
        moduleChooser.isHidden = true
    }

    @objc private func clearActiveStepTapped() {
        moduleChain[activeChainIndex] = nil
        refreshChainButtons()
    }

    @objc private func closeModuleChooserTapped() {
        moduleChooser.isHidden = true
    }

    @objc private func clearChainTapped() {
        moduleChain = Array(repeating: nil, count: 10)
        refreshChainButtons()
    }

    @objc private func playTapped() {
        guard let slots = requireSelectedSlots() else { return }
        let plan = moduleChain.compactMap { $0 }
        guard !plan.isEmpty else {
            setStatus("請先設定至少一個模組。", good: false)
            return
        }
        setStatus("正在把模組串列交給 GUI_TEST_PC...", good: true)
        onPlay?(slots, plan)
    }

    @objc private func stopAllTapped() { onStopAll?() }

    @objc private func startAllTapped() {
        onLauncher?("start-missing", Array(1...15))
    }

    @objc private func startSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("start", slots)
    }

    @objc private func closeSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("stop", slots)
    }

    @objc private func closeAllTapped() {
        onLauncher?("stop", Array(1...15))
    }

    @objc private func arrangeTapped() {
        let slots = runningSlots.sorted()
        guard !slots.isEmpty else {
            setStatus("目前沒有運行中的遊戲視窗。", good: false)
            return
        }
        onArrange?(slots)
    }
}
