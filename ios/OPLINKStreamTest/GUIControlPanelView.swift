import UIKit

final class GUIControlPanelView: UIView {
    var onClose: (() -> Void)?
    var onRefresh: (() -> Void)?
    var onPlay: (([Int], [String]) -> Void)?
    var onStopAll: (() -> Void)?
    var onStopSlot: ((Int) -> Void)?
    var onLauncher: ((String, [Int]) -> Void)?
    var onArrange: (([Int]) -> Void)?
    var onRequestPresetSave: ((Int, String, [String]) -> Void)?

    private let card = UIView()
    private let slotSummaryLabel = UILabel()
    private let moduleSummaryLabel = UILabel()
    private let statusLabel = UILabel()
    private let slotButtons = (1...15).map { _ in UIButton(type: .system) }
    private let chainButtons = (0..<10).map { _ in UIButton(type: .system) }
    private let presetButtons = (0..<10).map { _ in UIButton(type: .system) }
    private let moduleChooser = UIView()
    private let moduleChooserTitle = UILabel()
    private let moduleGroupsStack = UIStackView()
    private let chooserStepButtons = (0..<10).map { _ in UIButton(type: .system) }
    private let savePresetButton = UIButton(type: .system)

    private var runningSlots = Set<Int>()
    private var playingSlots = Set<Int>()
    private var selectedSlots = Set<Int>()
    private var slotPlaybackStatus: [String: String] = [:]
    private var moduleNames: [String] = []
    private var moduleGroups: [GUIModuleGroup] = []
    private var moduleChain: [String?] = Array(repeating: nil, count: 10)
    private var presets: [GUIModuleChainPreset] = []
    private var activeChainIndex = 0
    private var activePresetIndex: Int?
    private var heartbeatFresh = false

    override init(frame: CGRect) {
        super.init(frame: frame)
        build()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        build()
    }

    func prepareForPresentation(streamSlot: Int) {
        selectedSlots = (1...15).contains(streamSlot) ? [streamSlot] : []
        activePresetIndex = nil
        moduleChooser.isHidden = true
        refreshSlotButtons()
        refreshChainButtons()
    }

    func apply(
        runningSlots: Set<Int>,
        playingSlots: Set<Int>,
        slotPlaybackStatus: [String: String],
        modules: [String: [String]],
        groups: [GUIModuleGroup],
        presets: [GUIModuleChainPreset],
        heartbeatFresh: Bool
    ) {
        self.runningSlots = runningSlots
        self.playingSlots = playingSlots
        self.slotPlaybackStatus = slotPlaybackStatus
        self.heartbeatFresh = heartbeatFresh
        self.presets = normalizedPresets(presets)

        let available = Set(modules.keys)
        var orderedGroups: [GUIModuleGroup] = []
        var seen = Set<String>()
        for group in groups {
            let names = group.modules.filter { available.contains($0) && seen.insert($0).inserted }
            if !names.isEmpty {
                orderedGroups.append(GUIModuleGroup(name: group.name, modules: names))
            }
        }
        let remaining = modules.keys.filter { !seen.contains($0) }.sortedLocalized()
        if !remaining.isEmpty {
            orderedGroups.append(GUIModuleGroup(name: "未分組", modules: remaining))
        }
        if orderedGroups.isEmpty, !modules.isEmpty {
            orderedGroups = [GUIModuleGroup(name: "未分組", modules: modules.keys.sortedLocalized())]
        }
        moduleGroups = orderedGroups
        moduleNames = orderedGroups.flatMap(\.modules)
        moduleChain = moduleChain.map { name in
            guard let name, available.contains(name) else { return nil }
            return name
        }

        rebuildModuleButtons()
        refreshSlotButtons()
        refreshChainButtons()
        refreshPresetButtons()
        let heartbeat = heartbeatFresh ? "HEARTBEAT 正常" : "HEARTBEAT 過期"
        slotSummaryLabel.text = "視窗 \(runningSlots.count) / 15  \(heartbeat)"
    }

    func finishPresetSave(_ updatedPresets: [GUIModuleChainPreset]) {
        presets = normalizedPresets(updatedPresets)
        activePresetIndex = nil
        moduleChooser.isHidden = true
        refreshPresetButtons()
        refreshChainButtons()
    }

    func setStatus(_ text: String, good: Bool) {
        statusLabel.text = text
        statusLabel.textColor = good
            ? UIColor(red: 0.52, green: 0.94, blue: 0.72, alpha: 1)
            : UIColor(red: 1, green: 0.48, blue: 0.4, alpha: 1)
    }

    private func build() {
        backgroundColor = .clear

        card.translatesAutoresizingMaskIntoConstraints = false
        card.backgroundColor = UIColor.black.withAlphaComponent(0.50)
        card.layer.cornerRadius = 18
        card.layer.borderWidth = 1
        card.layer.borderColor = UIColor.white.withAlphaComponent(0.18).cgColor
        addSubview(card)

        let title = UILabel()
        title.text = "GUI_TEST_PC"
        title.textColor = .white
        title.font = .monospacedSystemFont(ofSize: 18, weight: .heavy)
        let subtitle = UILabel()
        subtitle.text = "BRIDGE ONLY  •  EXECUTION OWNER: WINDOWS GUI"
        subtitle.textColor = UIColor(red: 0.43, green: 0.86, blue: 0.94, alpha: 1)
        subtitle.font = .monospacedSystemFont(ofSize: 9, weight: .bold)
        let titleStack = UIStackView(arrangedSubviews: [title, subtitle])
        titleStack.axis = .vertical
        titleStack.spacing = 1

        let refresh = iconButton("arrow.clockwise", label: "重新整理")
        refresh.addTarget(self, action: #selector(refreshTapped), for: .touchUpInside)
        let close = iconButton("xmark", label: "關閉控制面板")
        close.addTarget(self, action: #selector(closeTapped), for: .touchUpInside)
        let header = UIStackView(arrangedSubviews: [titleStack, UIView(), refresh, close])
        header.axis = .horizontal
        header.alignment = .center
        header.spacing = 8

        let body = UIStackView(arrangedSubviews: [buildSlotsColumn(), buildModulesColumn()])
        body.axis = .horizontal
        body.spacing = 18
        body.distribution = .fillEqually

        let mainStack = UIStackView(arrangedSubviews: [header, divider(), body])
        mainStack.axis = .vertical
        mainStack.spacing = 8
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(mainStack)

        let preferredWidth = card.widthAnchor.constraint(equalToConstant: 700)
        preferredWidth.priority = .defaultHigh
        let preferredHeight = card.heightAnchor.constraint(equalToConstant: 382)
        preferredHeight.priority = .defaultHigh
        NSLayoutConstraint.activate([
            card.centerXAnchor.constraint(equalTo: safeAreaLayoutGuide.centerXAnchor),
            card.centerYAnchor.constraint(equalTo: safeAreaLayoutGuide.centerYAnchor),
            preferredWidth,
            preferredHeight,
            card.widthAnchor.constraint(lessThanOrEqualTo: safeAreaLayoutGuide.widthAnchor, multiplier: 0.94),
            card.heightAnchor.constraint(lessThanOrEqualTo: safeAreaLayoutGuide.heightAnchor, multiplier: 0.95),
            mainStack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            mainStack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            mainStack.topAnchor.constraint(equalTo: card.topAnchor, constant: 12),
            mainStack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -12)
        ])

        buildModuleChooser()
        refreshSlotButtons()
        refreshChainButtons()
        refreshPresetButtons()
    }

    private func buildSlotsColumn() -> UIView {
        slotSummaryLabel.text = "視窗 0 / 15"
        styleSectionLabel(slotSummaryLabel)

        let grid = UIStackView()
        grid.axis = .vertical
        grid.spacing = 5
        grid.distribution = .fillEqually
        for rowIndex in 0..<3 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 5
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let slot = rowIndex * 5 + columnIndex + 1
                let button = slotButtons[slot - 1]
                button.tag = slot
                button.setTitle(String(format: "%02d", slot), for: .normal)
                button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 14, weight: .bold)
                button.layer.cornerRadius = 8
                button.addTarget(self, action: #selector(slotTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(equalToConstant: 31).isActive = true
                row.addArrangedSubview(button)
            }
            grid.addArrangedSubview(row)
        }

        let selectAll = textButton("全選", color: UIColor(white: 0.32, alpha: 1))
        selectAll.addTarget(self, action: #selector(selectAllTapped), for: .touchUpInside)
        let clear = textButton("清選", color: UIColor(white: 0.32, alpha: 1))
        clear.addTarget(self, action: #selector(clearSlotsTapped), for: .touchUpInside)
        let restart = textButton("重啟", color: UIColor(red: 0.76, green: 0.43, blue: 0.13, alpha: 1))
        restart.addTarget(self, action: #selector(restartSelectedTapped), for: .touchUpInside)

        let hint = UILabel()
        hint.text = "灰：未選　綠：已選　紅：播放中（點紅只停該槽）"
        hint.textColor = UIColor.white.withAlphaComponent(0.72)
        hint.font = .systemFont(ofSize: 9, weight: .medium)
        hint.adjustsFontSizeToFitWidth = true

        let stack = UIStackView(arrangedSubviews: [slotSummaryLabel, grid, actionRow([selectAll, clear, restart]), hint])
        stack.axis = .vertical
        stack.spacing = 6
        return stack
    }

    private func buildModulesColumn() -> UIView {
        moduleSummaryLabel.text = "模組連串 0 / 10"
        styleSectionLabel(moduleSummaryLabel)

        let chainGrid = UIStackView()
        chainGrid.axis = .vertical
        chainGrid.spacing = 5
        for rowIndex in 0..<2 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 5
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let index = rowIndex * 5 + columnIndex
                let button = chainButtons[index]
                button.tag = index
                button.layer.cornerRadius = 7
                button.titleLabel?.font = .systemFont(ofSize: 9, weight: .bold)
                button.titleLabel?.numberOfLines = 2
                button.titleLabel?.textAlignment = .center
                button.addTarget(self, action: #selector(chainTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(equalToConstant: 31).isActive = true
                row.addArrangedSubview(button)
            }
            chainGrid.addArrangedSubview(row)
        }

        let play = textButton("播放", color: UIColor(red: 0.08, green: 0.62, blue: 0.32, alpha: 1))
        play.addTarget(self, action: #selector(playTapped), for: .touchUpInside)
        let clear = textButton("清除", color: UIColor(white: 0.32, alpha: 1))
        clear.addTarget(self, action: #selector(clearChainTapped), for: .touchUpInside)
        let stop = textButton("全止", color: UIColor(red: 0.8, green: 0.15, blue: 0.14, alpha: 1))
        stop.addTarget(self, action: #selector(stopAllTapped), for: .touchUpInside)

        let startAll = textButton("全開", color: UIColor(red: 0.08, green: 0.48, blue: 0.55, alpha: 1))
        startAll.addTarget(self, action: #selector(startAllTapped), for: .touchUpInside)
        let startSelected = textButton("開選", color: UIColor(red: 0.08, green: 0.48, blue: 0.55, alpha: 1))
        startSelected.addTarget(self, action: #selector(startSelectedTapped), for: .touchUpInside)
        let arrange = textButton("排列", color: UIColor(red: 0.2, green: 0.36, blue: 0.58, alpha: 1))
        arrange.addTarget(self, action: #selector(arrangeTapped), for: .touchUpInside)
        let closeSelected = textButton("關選", color: UIColor(red: 0.5, green: 0.3, blue: 0.14, alpha: 1))
        closeSelected.addTarget(self, action: #selector(closeSelectedTapped), for: .touchUpInside)
        let closeAll = textButton("全關", color: UIColor(red: 0.63, green: 0.21, blue: 0.16, alpha: 1))
        closeAll.addTarget(self, action: #selector(closeAllTapped), for: .touchUpInside)

        statusLabel.text = "等待 GUI_TEST_PC 狀態"
        statusLabel.textColor = UIColor.white.withAlphaComponent(0.78)
        statusLabel.font = .monospacedSystemFont(ofSize: 9, weight: .semibold)
        statusLabel.numberOfLines = 2

        let presetTitle = UILabel()
        presetTitle.text = "連串預設（點擊後編輯及命名）"
        presetTitle.textColor = UIColor.white.withAlphaComponent(0.9)
        presetTitle.font = .systemFont(ofSize: 10, weight: .bold)
        let presetGrid = UIStackView()
        presetGrid.axis = .vertical
        presetGrid.spacing = 4
        for rowIndex in 0..<2 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 4
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let index = rowIndex * 5 + columnIndex
                let button = presetButtons[index]
                button.tag = index + 1
                button.layer.cornerRadius = 7
                button.layer.borderWidth = 1
                button.titleLabel?.font = .systemFont(ofSize: 8.5, weight: .bold)
                button.titleLabel?.numberOfLines = 2
                button.titleLabel?.textAlignment = .center
                button.addTarget(self, action: #selector(presetTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(equalToConstant: 27).isActive = true
                row.addArrangedSubview(button)
            }
            presetGrid.addArrangedSubview(row)
        }

        let stack = UIStackView(arrangedSubviews: [
            moduleSummaryLabel,
            chainGrid,
            actionRow([play, clear, stop]),
            actionRow([startAll, startSelected, arrange]),
            actionRow([closeSelected, closeAll]),
            statusLabel,
            presetTitle,
            presetGrid
        ])
        stack.axis = .vertical
        stack.spacing = 5
        return stack
    }

    private func buildModuleChooser() {
        moduleChooser.translatesAutoresizingMaskIntoConstraints = false
        moduleChooser.backgroundColor = UIColor.black.withAlphaComponent(0.68)
        moduleChooser.layer.cornerRadius = 15
        moduleChooser.layer.borderWidth = 1
        moduleChooser.layer.borderColor = UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 0.8).cgColor
        moduleChooser.isHidden = true
        card.addSubview(moduleChooser)

        moduleChooserTitle.textColor = .white
        moduleChooserTitle.font = .systemFont(ofSize: 15, weight: .bold)

        savePresetButton.setTitle("儲存命名", for: .normal)
        savePresetButton.setTitleColor(.white, for: .normal)
        savePresetButton.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
        savePresetButton.backgroundColor = UIColor(red: 0.08, green: 0.58, blue: 0.35, alpha: 1)
        savePresetButton.layer.cornerRadius = 8
        savePresetButton.heightAnchor.constraint(equalToConstant: 28).isActive = true
        savePresetButton.widthAnchor.constraint(equalToConstant: 76).isActive = true
        savePresetButton.addTarget(self, action: #selector(savePresetTapped), for: .touchUpInside)
        savePresetButton.isHidden = true

        let clear = textButton("清除此格", color: UIColor(white: 0.3, alpha: 1))
        clear.addTarget(self, action: #selector(clearActiveStepTapped), for: .touchUpInside)
        clear.widthAnchor.constraint(equalToConstant: 76).isActive = true
        let close = iconButton("xmark", label: "關閉模組選單")
        close.addTarget(self, action: #selector(closeModuleChooserTapped), for: .touchUpInside)
        let header = UIStackView(arrangedSubviews: [moduleChooserTitle, UIView(), savePresetButton, clear, close])
        header.axis = .horizontal
        header.alignment = .center
        header.spacing = 7

        let stepRow = UIStackView()
        stepRow.axis = .horizontal
        stepRow.spacing = 4
        stepRow.distribution = .fillEqually
        for index in 0..<10 {
            let button = chooserStepButtons[index]
            button.tag = index
            button.setTitle("\(index + 1)", for: .normal)
            button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 10, weight: .bold)
            button.layer.cornerRadius = 6
            button.addTarget(self, action: #selector(chooserStepTapped(_:)), for: .touchUpInside)
            button.heightAnchor.constraint(equalToConstant: 28).isActive = true
            stepRow.addArrangedSubview(button)
        }

        let scroll = UIScrollView()
        scroll.showsVerticalScrollIndicator = true
        moduleGroupsStack.axis = .vertical
        moduleGroupsStack.spacing = 8
        moduleGroupsStack.translatesAutoresizingMaskIntoConstraints = false
        scroll.addSubview(moduleGroupsStack)

        let stack = UIStackView(arrangedSubviews: [header, stepRow, scroll])
        stack.axis = .vertical
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        moduleChooser.addSubview(stack)

        NSLayoutConstraint.activate([
            moduleChooser.centerXAnchor.constraint(equalTo: card.centerXAnchor),
            moduleChooser.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            moduleChooser.widthAnchor.constraint(equalTo: card.widthAnchor, multiplier: 0.72),
            moduleChooser.heightAnchor.constraint(equalTo: card.heightAnchor, multiplier: 0.9),
            stack.leadingAnchor.constraint(equalTo: moduleChooser.leadingAnchor, constant: 14),
            stack.trailingAnchor.constraint(equalTo: moduleChooser.trailingAnchor, constant: -14),
            stack.topAnchor.constraint(equalTo: moduleChooser.topAnchor, constant: 12),
            stack.bottomAnchor.constraint(equalTo: moduleChooser.bottomAnchor, constant: -12),
            moduleGroupsStack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            moduleGroupsStack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            moduleGroupsStack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            moduleGroupsStack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
            moduleGroupsStack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor)
        ])
    }

    private func rebuildModuleButtons() {
        clearStack(moduleGroupsStack)
        guard !moduleGroups.isEmpty else {
            let empty = UILabel()
            empty.text = "GUI_TEST_PC 沒有可用模組"
            empty.textColor = UIColor.white.withAlphaComponent(0.7)
            empty.textAlignment = .center
            moduleGroupsStack.addArrangedSubview(empty)
            return
        }

        for group in moduleGroups {
            let label = UILabel()
            label.text = group.name
            label.textColor = .white
            label.font = .systemFont(ofSize: 10, weight: .bold)

            let grid = UIStackView()
            grid.axis = .vertical
            grid.spacing = 5
            for start in stride(from: 0, to: group.modules.count, by: 3) {
                let row = UIStackView()
                row.axis = .horizontal
                row.spacing = 5
                row.distribution = .fillEqually
                for offset in 0..<3 {
                    let moduleIndex = start + offset
                    if group.modules.indices.contains(moduleIndex),
                       let globalIndex = moduleNames.firstIndex(of: group.modules[moduleIndex]) {
                        let button = textButton(group.modules[moduleIndex], color: UIColor(white: 0.32, alpha: 1))
                        button.tag = globalIndex
                        button.titleLabel?.numberOfLines = 2
                        button.titleLabel?.textAlignment = .center
                        button.addTarget(self, action: #selector(moduleTapped(_:)), for: .touchUpInside)
                        row.addArrangedSubview(button)
                    } else {
                        row.addArrangedSubview(UIView())
                    }
                }
                grid.addArrangedSubview(row)
            }
            let groupStack = UIStackView(arrangedSubviews: [label, grid])
            groupStack.axis = .vertical
            groupStack.spacing = 4
            moduleGroupsStack.addArrangedSubview(groupStack)
        }
    }

    private func refreshSlotButtons() {
        for button in slotButtons {
            let slot = button.tag
            let running = runningSlots.contains(slot)
            let playing = playingSlots.contains(slot)
            let selected = selectedSlots.contains(slot)
            if playing {
                button.backgroundColor = UIColor(red: 0.86, green: 0.12, blue: 0.12, alpha: 1)
                button.setTitleColor(.white, for: .normal)
            } else if selected {
                button.backgroundColor = UIColor(red: 0.28, green: 0.84, blue: 0.42, alpha: 1)
                button.setTitleColor(UIColor(red: 0.02, green: 0.13, blue: 0.06, alpha: 1), for: .normal)
            } else {
                button.backgroundColor = UIColor(white: running ? 0.34 : 0.18, alpha: 1)
                button.setTitleColor(.white, for: .normal)
            }
            button.alpha = running || selected || playing ? 1 : 0.93
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
                ? UIColor(white: 0.24, alpha: 1)
                : UIColor(red: 0.06, green: 0.48, blue: 0.36, alpha: 1)
            button.setTitleColor(.white, for: .normal)
        }
        for button in chooserStepButtons {
            let active = button.tag == activeChainIndex
            button.backgroundColor = active
                ? UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1)
                : UIColor(white: 0.28, alpha: 1)
            button.setTitleColor(active ? .black : .white, for: .normal)
        }
        moduleSummaryLabel.text = "模組連串 \(moduleChain.compactMap { $0 }.count) / 10"
        let presetName = activePresetIndex.flatMap { index in presets.first { $0.index == index }?.name }
        moduleChooserTitle.text = presetName.map { "\($0) · 第 \(activeChainIndex + 1) 格" }
            ?? "第 \(activeChainIndex + 1) 格：選擇模組"
        savePresetButton.isHidden = activePresetIndex == nil
    }

    private func refreshPresetButtons() {
        for button in presetButtons {
            let index = button.tag
            let preset = presets.first { $0.index == index }
                ?? GUIModuleChainPreset(index: index, name: "連串 \(index)", modules: [])
            button.setTitle("\(index)\n\(preset.modules.isEmpty ? "＋" : preset.name)", for: .normal)
            button.setTitleColor(.white, for: .normal)
            button.backgroundColor = preset.modules.isEmpty
                ? UIColor(red: 0.12, green: 0.34, blue: 0.55, alpha: 1)
                : UIColor(red: 0.08, green: 0.5, blue: 0.45, alpha: 1)
            button.layer.borderColor = UIColor.white.withAlphaComponent(0.35).cgColor
        }
    }

    private func normalizedPresets(_ values: [GUIModuleChainPreset]) -> [GUIModuleChainPreset] {
        let byIndex = Dictionary(uniqueKeysWithValues: values.filter { (1...10).contains($0.index) }.map { ($0.index, $0) })
        return (1...10).map { index in
            byIndex[index] ?? GUIModuleChainPreset(index: index, name: "連串 \(index)", modules: [])
        }
    }

    private func iconButton(_ systemName: String, label: String) -> UIButton {
        let button = UIButton(type: .system)
        button.setImage(UIImage(systemName: systemName), for: .normal)
        button.tintColor = .white
        button.backgroundColor = UIColor.white.withAlphaComponent(0.25)
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
        button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
        button.titleLabel?.lineBreakMode = .byTruncatingTail
        button.backgroundColor = color
        button.layer.cornerRadius = 7
        button.heightAnchor.constraint(equalToConstant: 27).isActive = true
        return button
    }

    private func actionRow(_ buttons: [UIButton]) -> UIStackView {
        let row = UIStackView(arrangedSubviews: buttons)
        row.axis = .horizontal
        row.spacing = 5
        row.distribution = .fillEqually
        return row
    }

    private func divider() -> UIView {
        let view = UIView()
        view.backgroundColor = UIColor.white.withAlphaComponent(0.16)
        view.heightAnchor.constraint(equalToConstant: 1).isActive = true
        return view
    }

    private func styleSectionLabel(_ label: UILabel) {
        label.textColor = .white
        label.font = .systemFont(ofSize: 11, weight: .bold)
        label.adjustsFontSizeToFitWidth = true
    }

    private func clearStack(_ stack: UIStackView) {
        for view in stack.arrangedSubviews {
            stack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
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
            setStatus("正在中止 GAME \(slot)...", good: true)
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
        activePresetIndex = nil
        activeChainIndex = sender.tag
        refreshChainButtons()
        moduleChooser.isHidden = false
    }

    @objc private func presetTapped(_ sender: UIButton) {
        let index = sender.tag
        let preset = presets.first { $0.index == index }
            ?? GUIModuleChainPreset(index: index, name: "連串 \(index)", modules: [])
        activePresetIndex = index
        moduleChain = Array(preset.modules.prefix(10)).map { Optional($0) }
        while moduleChain.count < 10 { moduleChain.append(nil) }
        activeChainIndex = min(preset.modules.count, 9)
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
        if activeChainIndex < 9 { activeChainIndex += 1 }
        refreshChainButtons()
    }

    @objc private func clearActiveStepTapped() {
        moduleChain[activeChainIndex] = nil
        refreshChainButtons()
    }

    @objc private func closeModuleChooserTapped() {
        activePresetIndex = nil
        moduleChooser.isHidden = true
        refreshChainButtons()
    }

    @objc private func savePresetTapped() {
        guard let index = activePresetIndex else { return }
        let modules = moduleChain.compactMap { $0 }
        guard !modules.isEmpty else {
            setStatus("連串預設至少需要一個模組。", good: false)
            return
        }
        let currentName = presets.first { $0.index == index }?.name ?? "連串 \(index)"
        onRequestPresetSave?(index, currentName, modules)
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
        selectedSlots.removeAll()
        refreshSlotButtons()
        setStatus("正在把模組連串交給 GUI_TEST_PC...", good: true)
        onPlay?(slots, plan)
    }

    @objc private func stopAllTapped() { onStopAll?() }
    @objc private func startAllTapped() { onLauncher?("start-missing", Array(1...15)) }

    @objc private func startSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("start", slots)
    }

    @objc private func closeSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("stop", slots)
    }

    @objc private func closeAllTapped() { onLauncher?("stop", Array(1...15)) }

    @objc private func arrangeTapped() {
        let slots = runningSlots.sorted()
        guard !slots.isEmpty else {
            setStatus("目前沒有運行中的遊戲視窗。", good: false)
            return
        }
        onArrange?(slots)
    }
}

private extension Collection where Element == String {
    func sortedLocalized() -> [String] {
        sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }
}
