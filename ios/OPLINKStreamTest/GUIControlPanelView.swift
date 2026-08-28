import UIKit

final class GUIControlPanelView: UIView {
    private enum ModulePanelMetrics {
        static let compactScale: CGFloat = 0.8
        static let chainButtonHeight: CGFloat = 31 * compactScale
        static let chainFontSize: CGFloat = 10 * compactScale
        static let chainCornerRadius: CGFloat = 7 * compactScale
        static let chainSpacing: CGFloat = 5 * compactScale
        static let quickButtonHeight: CGFloat = 42 * compactScale
        static let quickFontSize: CGFloat = 15 * compactScale
    }

    var onClose: (() -> Void)?
    var onRefresh: (() -> Void)?
    var onPlay: (([Int], [String]) -> Void)?
    var onCreateScheduled: (([Int], [String], Date) -> Void)?
    var onStartLoop: (([Int], [String], Int?, Int) -> Void)?
    var onCancelAutomation: ((String) -> Void)?
    var onStopAll: (() -> Void)?
    var onStopSlot: ((Int) -> Void)?
    var onLauncher: ((String, [Int]) -> Void)?
    var onArrange: (([Int]) -> Void)?
    var onRequestPresetSave: ((Int, String, [String]) -> Void)?
    var onRestartController: (() -> Void)?

    private let card = UIView()
    private let statusLabel = UILabel()
    private let slotButtons = OPLINKSlots.range.map { _ in UIButton(type: .system) }
    private let chainButtons = (0..<10).map { _ in UIButton(type: .system) }
    private let presetButtons = (0..<20).map { _ in UIButton(type: .system) }
    private let moduleChooser = UIView()
    private let moduleChooserTitle = UILabel()
    private let moduleGroupsStack = UIStackView()
    private let quickModuleGroupsStack = UIStackView()
    private let chooserStepButtons = (0..<10).map { _ in UIButton(type: .system) }
    private let savePresetButton = UIButton(type: .system)
    private let automationSheet = UIView()
    private let automationSheetTitle = UILabel()
    private let automationSheetContent = UIStackView()
    private let automationSheetActions = UIStackView()
    private let loopCountField = UITextField()
    private let loopCooldownField = UITextField()
    private let scheduledSecondField = UITextField()
    private let scheduledDatePicker = UIDatePicker()
    private let restartControllerButton = UIButton(type: .system)
    private weak var playActionButton: UIButton?

    private var runningSlots = Set<Int>()
    private var queuedOpeningSlots = Set<Int>()
    private var openingSlots = Set<Int>()
    private var closingSlots = Set<Int>()
    private var restartingSlots = Set<Int>()
    private var playingSlots = Set<Int>()
    private var queuedPlaybackSlots = Set<Int>()
    private var selectedSlots = Set<Int>()
    private var slotPlaybackStatus: [String: String] = [:]
    private var slotCurrentModule: [String: String] = [:]
    private var picoActivitySlot: Int?
    private var moduleNames: [String] = []
    private var moduleGroups: [GUIModuleGroup] = []
    private var moduleGroupSignature: [String]?
    private var moduleChain: [String?] = Array(repeating: nil, count: 10)
    private var presets: [GUIModuleChainPreset] = []
    private var playbackAutomations: [GUIPlaybackAutomation] = []
    private var activeChainIndex = 0
    private var activePresetIndex: Int?
    private var automationSheetMode: String?
    private var pendingScheduledSlots: [Int] = []
    private var pendingScheduledModules: [String] = []
    private var pendingSubmissionSlots = Set<Int>()
    private var pendingSubmissionExpiry: [Int: Date] = [:]
    private var autoSelectedSlot: Int?
    private var heartbeatFresh = false

    private enum ChainInsertionMode: Equatable {
        case head
        case tail
        case index
    }

    private struct ChainInsertion {
        var position: Int
        var start: Int
        var mode: ChainInsertionMode
        var activeCell: Int
    }

    private var chainInsertion = ChainInsertion(position: 0, start: 1, mode: .index, activeCell: 1)

    private enum LoopDefaults {
        static let repeatCount = "oplink.module.loop.repeatCount"
        static let cooldownSeconds = "oplink.module.loop.cooldownSeconds"
    }
    override init(frame: CGRect) {
        super.init(frame: frame)
        build()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        build()
    }

    func prepareForPresentation(streamSlot: Int) {
        updateStreamSlot(streamSlot)
        activePresetIndex = nil
        moduleChooser.isHidden = true
        automationSheet.isHidden = true
        automationSheetMode = nil
        refreshSlotButtons()
        refreshChainButtons()
    }

    func updateStreamSlot(_ streamSlot: Int) {
        guard OPLINKSlots.range.contains(streamSlot), !automaticSelectionBlockedSlots.contains(streamSlot) else {
            selectedSlots.removeAll()
            autoSelectedSlot = nil
            refreshSlotButtons()
            return
        }
        selectedSlots = [streamSlot]
        autoSelectedSlot = streamSlot
        refreshSlotButtons()
    }

    func apply(
        runningSlots: Set<Int>,
        queuedOpeningSlots: Set<Int>,
        openingSlots: Set<Int>,
        closingSlots: Set<Int>,
        restartingSlots: Set<Int>,
        playingSlots: Set<Int>,
        queuedPlaybackSlots: Set<Int>,
        slotPlaybackStatus: [String: String],
        slotCurrentModule: [String: String],
        picoActivitySlot: Int?,
        modules: [String: [String]],
        groups: [GUIModuleGroup],
        presets: [GUIModuleChainPreset],
        playbackAutomations: [GUIPlaybackAutomation],
        heartbeatFresh: Bool
    ) {
        self.runningSlots = runningSlots
        self.queuedOpeningSlots = queuedOpeningSlots
        self.openingSlots = openingSlots
        self.closingSlots = closingSlots
        self.restartingSlots = restartingSlots
        self.playingSlots = playingSlots
        self.queuedPlaybackSlots = queuedPlaybackSlots
        self.slotPlaybackStatus = slotPlaybackStatus
        self.slotCurrentModule = slotCurrentModule
        self.picoActivitySlot = picoActivitySlot
        self.presets = normalizedPresets(presets)
        self.playbackAutomations = playbackAutomations.filter(\.isActive)
        self.heartbeatFresh = heartbeatFresh
        selectedSlots.subtract(launcherTransitionSlots)
        if let autoSelectedSlot, automaticSelectionBlockedSlots.contains(autoSelectedSlot) {
            selectedSlots.remove(autoSelectedSlot)
            self.autoSelectedSlot = nil
        }
        let bridgePlaybackSlots = queuedPlaybackSlots
            .union(playingSlots)
            .union(activeAutomationSlots)
        pendingSubmissionSlots.subtract(bridgePlaybackSlots)
        let expiredPendingSlots = pendingSubmissionSlots.filter {
            (pendingSubmissionExpiry[$0] ?? .distantPast) <= Date()
        }
        for slot in expiredPendingSlots {
            pendingSubmissionSlots.remove(slot)
            pendingSubmissionExpiry.removeValue(forKey: slot)
        }
        restartControllerButton.isHidden = heartbeatFresh

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
        let nextSignature = orderedGroups.map { group in
            group.name + "\u{001D}" + group.modules.joined(separator: "\u{001E}")
        }
        let groupsChanged = nextSignature != moduleGroupSignature
        moduleGroups = orderedGroups
        moduleNames = orderedGroups.flatMap(\.modules)
        moduleGroupSignature = nextSignature
        moduleChain = moduleChain.map { name in
            guard let name, available.contains(name) else { return nil }
            return name
        }

        if groupsChanged {
            rebuildModuleButtons()
        }
        refreshSlotButtons()
        refreshChainButtons()
        refreshPresetButtons()
        refreshOpenAutomationSheet()
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

    func finishPlaybackSubmission(
        requestedSlots: [Int],
        acceptedSlots: [Int],
        skippedSlots: [Int],
        error: String?
    ) {
        if let error {
            pendingSubmissionSlots.subtract(requestedSlots)
            for slot in requestedSlots { pendingSubmissionExpiry.removeValue(forKey: slot) }
            let restorable = Set(requestedSlots).subtracting(launcherTransitionSlots)
            selectedSlots.formUnion(restorable)
            setStatus(error, good: false)
        } else {
            pendingSubmissionSlots.subtract(skippedSlots)
            for slot in skippedSlots { pendingSubmissionExpiry.removeValue(forKey: slot) }
            selectedSlots.subtract(acceptedSlots)
            let acceptedText = acceptedSlots.map(String.init).joined(separator: ",")
            if skippedSlots.isEmpty {
                setStatus("已送出 Slot \(acceptedText)", good: true)
            } else {
                let skippedText = skippedSlots.map(String.init).joined(separator: ",")
                setStatus("已送出 Slot \(acceptedText)；Slot \(skippedText) 未接受", good: true)
            }
        }
        refreshSlotButtons()
    }

    private func build() {
        backgroundColor = .clear

        card.translatesAutoresizingMaskIntoConstraints = false
        card.backgroundColor = UIColor.black.withAlphaComponent(0.38)
        card.layer.cornerRadius = 18
        card.layer.borderWidth = 1
        card.layer.borderColor = UIColor.white.withAlphaComponent(0.18).cgColor
        addSubview(card)

        let close = iconButton("xmark", label: "關閉控制面板", size: 32, symbolPointSize: 14)
        close.addTarget(self, action: #selector(closeTapped), for: .touchUpInside)

        let slotsColumn = buildSlotsColumn()
        let modulesColumn = buildModulesColumn(closeButton: close)
        let body = UIStackView(arrangedSubviews: [slotsColumn, modulesColumn])
        body.axis = .horizontal
        body.spacing = 10
        body.distribution = .fill
        slotsColumn.widthAnchor.constraint(equalTo: body.widthAnchor, multiplier: 0.35).isActive = true

        statusLabel.font = .systemFont(ofSize: 12, weight: .semibold)
        statusLabel.numberOfLines = 2
        statusLabel.text = "選擇 Slot 與模組後播放"
        statusLabel.textColor = UIColor.white.withAlphaComponent(0.82)
        restartControllerButton.setTitle("重啟控制器", for: .normal)
        restartControllerButton.titleLabel?.font = .systemFont(ofSize: 12, weight: .bold)
        restartControllerButton.setTitleColor(.white, for: .normal)
        restartControllerButton.backgroundColor = UIColor(red: 0.78, green: 0.18, blue: 0.16, alpha: 1)
        restartControllerButton.layer.cornerRadius = 7
        restartControllerButton.contentEdgeInsets = UIEdgeInsets(top: 6, left: 10, bottom: 6, right: 10)
        restartControllerButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 30).isActive = true
        restartControllerButton.setContentCompressionResistancePriority(.required, for: .vertical)
        restartControllerButton.isHidden = true
        restartControllerButton.addTarget(self, action: #selector(restartControllerTapped), for: .touchUpInside)
        let statusRow = UIStackView(arrangedSubviews: [statusLabel, restartControllerButton])
        statusRow.axis = .horizontal
        statusRow.spacing = 8
        statusRow.alignment = .center
        statusRow.setContentCompressionResistancePriority(.required, for: .vertical)
        let mainStack = UIStackView(arrangedSubviews: [statusRow, body])
        mainStack.axis = .vertical
        mainStack.spacing = 6
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(mainStack)

        let preferredHeight = card.heightAnchor.constraint(equalToConstant: 420)
        preferredHeight.priority = .defaultHigh
        NSLayoutConstraint.activate([
            card.centerXAnchor.constraint(equalTo: safeAreaLayoutGuide.centerXAnchor),
            card.centerYAnchor.constraint(equalTo: safeAreaLayoutGuide.centerYAnchor),
            card.widthAnchor.constraint(equalTo: safeAreaLayoutGuide.widthAnchor, multiplier: 0.97),
            preferredHeight,
            card.heightAnchor.constraint(lessThanOrEqualTo: safeAreaLayoutGuide.heightAnchor, multiplier: 0.95),
            mainStack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 10),
            mainStack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -10),
            mainStack.topAnchor.constraint(equalTo: card.topAnchor, constant: 12),
            mainStack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -12)
        ])

        buildModuleChooser()
        buildAutomationSheet()
        refreshSlotButtons()
        refreshChainButtons()
        refreshPresetButtons()
    }

    private func buildSlotsColumn() -> UIView {
        let slotGrid = UIStackView()
        slotGrid.axis = .vertical
        slotGrid.spacing = 5
        slotGrid.distribution = .fillEqually
        for rowIndex in 0..<4 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 5
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let slot = rowIndex * 5 + columnIndex + 1
                let button = slotButtons[slot - 1]
                button.tag = slot
                button.setTitle(String(format: "%02d", slot), for: .normal)
                button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 15, weight: .bold)
                button.titleLabel?.adjustsFontSizeToFitWidth = true
                button.titleLabel?.minimumScaleFactor = 0.35
                button.titleLabel?.numberOfLines = 2
                button.titleLabel?.textAlignment = .center
                button.titleLabel?.lineBreakMode = .byWordWrapping
                button.layer.cornerRadius = 8
                button.addTarget(self, action: #selector(slotTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(equalToConstant: 31).isActive = true
                row.addArrangedSubview(button)
            }
            slotGrid.addArrangedSubview(row)
        }

        let startAll = compactTextButton("全開", color: UIColor(red: 0.08, green: 0.48, blue: 0.55, alpha: 1))
        startAll.addTarget(self, action: #selector(startAllTapped), for: .touchUpInside)
        let closeAll = compactTextButton("全關", color: UIColor(red: 0.63, green: 0.21, blue: 0.16, alpha: 1))
        closeAll.addTarget(self, action: #selector(closeAllTapped), for: .touchUpInside)
        let startSelected = compactTextButton("開選", color: UIColor(red: 0.08, green: 0.48, blue: 0.55, alpha: 1))
        startSelected.addTarget(self, action: #selector(startSelectedTapped), for: .touchUpInside)
        let closeSelected = compactTextButton("關選", color: UIColor(red: 0.63, green: 0.21, blue: 0.16, alpha: 1))
        closeSelected.addTarget(self, action: #selector(closeSelectedTapped), for: .touchUpInside)

        let scheduledSettings = textButton(
            "定時設定",
            color: UIColor(red: 0.14, green: 0.46, blue: 0.58, alpha: 1),
            height: 27,
            fontSize: 10
        )
        scheduledSettings.widthAnchor.constraint(equalToConstant: 57).isActive = true
        scheduledSettings.addTarget(self, action: #selector(scheduledSettingsTapped), for: .touchUpInside)

        let loopSettings = iconButton("gearshape.fill", label: "循環播放設定", size: 27, symbolPointSize: 11)
        loopSettings.addTarget(self, action: #selector(loopSettingsTapped), for: .touchUpInside)
        let loopStart = textButton(
            "循環播放",
            color: UIColor(red: 0.77, green: 0.39, blue: 0.08, alpha: 1),
            height: 27,
            fontSize: 10
        )
        loopStart.widthAnchor.constraint(equalToConstant: 57).isActive = true
        loopStart.addTarget(self, action: #selector(loopStartTapped), for: .touchUpInside)
        let log = textButton("LOG", color: UIColor(white: 0.28, alpha: 1), height: 27, fontSize: 10)
        log.widthAnchor.constraint(equalToConstant: 42).isActive = true
        log.addTarget(self, action: #selector(automationLogTapped), for: .touchUpInside)
        let automationRow = UIStackView(arrangedSubviews: [scheduledSettings, loopSettings, loopStart, log, UIView()])
        automationRow.axis = .horizontal
        automationRow.spacing = 4
        automationRow.alignment = .center

        let stack = UIStackView(arrangedSubviews: [
            slotGrid,
            compactActionRow([startAll, closeAll, startSelected, closeSelected]),
            buildPresetGrid(),
            automationRow
        ])
        stack.axis = .vertical
        stack.spacing = 6
        return stack
    }

    private func buildModulesColumn(closeButton: UIButton) -> UIView {
        let timed = compactTextButton("定時播放", color: UIColor(red: 0.92, green: 0.68, blue: 0.08, alpha: 1))
        timed.setTitleColor(UIColor(red: 0.12, green: 0.09, blue: 0.01, alpha: 1), for: .normal)
        timed.addTarget(self, action: #selector(scheduledPlayTapped), for: .touchUpInside)
        let play = compactTextButton("播放", color: UIColor(red: 0.08, green: 0.62, blue: 0.32, alpha: 1))
        playActionButton = play
        play.addTarget(self, action: #selector(playTapped), for: .touchUpInside)
        let clear = compactTextButton("清除", color: UIColor(white: 0.32, alpha: 1))
        clear.addTarget(self, action: #selector(clearChainTapped), for: .touchUpInside)
        let stop = compactTextButton("全止", color: UIColor(red: 0.8, green: 0.15, blue: 0.14, alpha: 1))
        stop.addTarget(self, action: #selector(stopAllTapped), for: .touchUpInside)

        let modulesScroll = UIScrollView()
        modulesScroll.showsVerticalScrollIndicator = true
        modulesScroll.alwaysBounceVertical = false
        quickModuleGroupsStack.axis = .vertical
        quickModuleGroupsStack.spacing = 7
        quickModuleGroupsStack.translatesAutoresizingMaskIntoConstraints = false
        modulesScroll.addSubview(quickModuleGroupsStack)
        NSLayoutConstraint.activate([
            modulesScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 155),
            quickModuleGroupsStack.leadingAnchor.constraint(equalTo: modulesScroll.contentLayoutGuide.leadingAnchor),
            quickModuleGroupsStack.trailingAnchor.constraint(equalTo: modulesScroll.contentLayoutGuide.trailingAnchor),
            quickModuleGroupsStack.topAnchor.constraint(equalTo: modulesScroll.contentLayoutGuide.topAnchor),
            quickModuleGroupsStack.bottomAnchor.constraint(equalTo: modulesScroll.contentLayoutGuide.bottomAnchor),
            quickModuleGroupsStack.widthAnchor.constraint(equalTo: modulesScroll.frameLayoutGuide.widthAnchor)
        ])

        let chainGrid = buildChainGrid()
        let chainRow = UIStackView(arrangedSubviews: [chainGrid, UIView()])
        chainRow.axis = .horizontal
        chainRow.spacing = 0
        chainGrid.widthAnchor.constraint(
            equalTo: chainRow.widthAnchor,
            multiplier: ModulePanelMetrics.compactScale
        ).isActive = true

        let moduleContent = UIStackView(arrangedSubviews: [modulesScroll, chainRow])
        moduleContent.axis = .vertical
        moduleContent.spacing = ModulePanelMetrics.chainSpacing

        let actionRail = UIStackView(arrangedSubviews: [UIView(), timed, play, clear, stop, closeButton])
        actionRail.axis = .vertical
        actionRail.spacing = 5
        actionRail.alignment = .center

        let contentRow = UIStackView(arrangedSubviews: [moduleContent, actionRail])
        contentRow.axis = .horizontal
        contentRow.spacing = 6
        contentRow.alignment = .fill

        return contentRow
    }

    private func buildChainGrid() -> UIStackView {
        let grid = UIStackView()
        grid.axis = .vertical
        grid.spacing = ModulePanelMetrics.chainSpacing
        for rowIndex in 0..<2 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = ModulePanelMetrics.chainSpacing
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let index = rowIndex * 5 + columnIndex
                let button = chainButtons[index]
                button.tag = index
                button.layer.cornerRadius = ModulePanelMetrics.chainCornerRadius
                button.titleLabel?.font = .systemFont(
                    ofSize: ModulePanelMetrics.chainFontSize,
                    weight: .bold
                )
                button.titleLabel?.numberOfLines = 2
                button.titleLabel?.textAlignment = .center
                button.titleLabel?.adjustsFontSizeToFitWidth = true
                button.titleLabel?.minimumScaleFactor = 0.68
                button.addTarget(self, action: #selector(chainTapped(_:)), for: .touchUpInside)
                button.heightAnchor.constraint(
                    equalToConstant: ModulePanelMetrics.chainButtonHeight
                ).isActive = true
                row.addArrangedSubview(button)
            }
            grid.addArrangedSubview(row)
        }
        return grid
    }

    private func buildPresetGrid() -> UIStackView {
        let grid = UIStackView()
        grid.axis = .vertical
        grid.spacing = 4
        for rowIndex in 0..<4 {
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
                button.titleLabel?.font = .systemFont(ofSize: 9.5, weight: .bold)
                button.titleLabel?.numberOfLines = 1
                button.titleLabel?.textAlignment = .center
                button.titleLabel?.lineBreakMode = .byClipping
                button.addTarget(self, action: #selector(presetTapped(_:)), for: .touchUpInside)
                let longPress = UILongPressGestureRecognizer(target: self, action: #selector(presetLongPressed(_:)))
                longPress.minimumPressDuration = 0.45
                button.addGestureRecognizer(longPress)
                button.heightAnchor.constraint(equalToConstant: 25).isActive = true
                row.addArrangedSubview(button)
            }
            grid.addArrangedSubview(row)
        }
        return grid
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
            button.setTitle("\(index + 1)\n＋", for: .normal)
            button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
            button.titleLabel?.numberOfLines = 2
            button.titleLabel?.textAlignment = .center
            button.titleLabel?.adjustsFontSizeToFitWidth = true
            button.titleLabel?.minimumScaleFactor = 0.65
            button.layer.cornerRadius = 6
            button.addTarget(self, action: #selector(chooserStepTapped(_:)), for: .touchUpInside)
            button.heightAnchor.constraint(equalToConstant: 36).isActive = true
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
            moduleChooser.widthAnchor.constraint(equalTo: card.widthAnchor, multiplier: 0.92),
            moduleChooser.heightAnchor.constraint(equalTo: card.heightAnchor, multiplier: 0.92),
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

    private func buildAutomationSheet() {
        automationSheet.translatesAutoresizingMaskIntoConstraints = false
        automationSheet.backgroundColor = UIColor.black.withAlphaComponent(0.9)
        automationSheet.layer.cornerRadius = 15
        automationSheet.layer.borderWidth = 1
        automationSheet.layer.borderColor = UIColor.white.withAlphaComponent(0.3).cgColor
        automationSheet.isHidden = true
        card.addSubview(automationSheet)

        automationSheetTitle.textColor = .white
        automationSheetTitle.font = .systemFont(ofSize: 15, weight: .bold)
        automationSheetTitle.numberOfLines = 2
        let close = iconButton("xmark", label: "關閉設定", size: 28, symbolPointSize: 12)
        close.addTarget(self, action: #selector(closeAutomationSheetTapped), for: .touchUpInside)
        let header = UIStackView(arrangedSubviews: [automationSheetTitle, UIView(), close])
        header.axis = .horizontal
        header.alignment = .center
        header.spacing = 8

        let scroll = UIScrollView()
        scroll.showsVerticalScrollIndicator = true
        automationSheetContent.axis = .vertical
        automationSheetContent.spacing = 7
        automationSheetContent.translatesAutoresizingMaskIntoConstraints = false
        scroll.addSubview(automationSheetContent)

        automationSheetActions.axis = .horizontal
        automationSheetActions.spacing = 7
        automationSheetActions.alignment = .center

        let stack = UIStackView(arrangedSubviews: [header, scroll, automationSheetActions])
        stack.axis = .vertical
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        automationSheet.addSubview(stack)

        configureNumberField(loopCountField, placeholder: "留空代表無限")
        configureNumberField(loopCooldownField, placeholder: "0–3600")
        configureNumberField(scheduledSecondField, placeholder: "00–59")
        scheduledDatePicker.datePickerMode = .dateAndTime
        scheduledDatePicker.preferredDatePickerStyle = .wheels
        scheduledDatePicker.locale = Locale(identifier: "zh_Hant_TW")
        scheduledDatePicker.timeZone = TimeZone(identifier: "Asia/Taipei")

        NSLayoutConstraint.activate([
            automationSheet.centerXAnchor.constraint(equalTo: card.centerXAnchor),
            automationSheet.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            automationSheet.widthAnchor.constraint(equalTo: card.widthAnchor, multiplier: 0.94),
            automationSheet.heightAnchor.constraint(equalTo: card.heightAnchor, multiplier: 0.94),
            stack.leadingAnchor.constraint(equalTo: automationSheet.leadingAnchor, constant: 14),
            stack.trailingAnchor.constraint(equalTo: automationSheet.trailingAnchor, constant: -14),
            stack.topAnchor.constraint(equalTo: automationSheet.topAnchor, constant: 12),
            stack.bottomAnchor.constraint(equalTo: automationSheet.bottomAnchor, constant: -12),
            automationSheetContent.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            automationSheetContent.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            automationSheetContent.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor),
            automationSheetContent.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor),
            automationSheetContent.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor)
        ])
    }

    private func configureNumberField(_ field: UITextField, placeholder: String) {
        field.placeholder = placeholder
        field.keyboardType = .numberPad
        field.textColor = .white
        field.tintColor = .white
        field.backgroundColor = UIColor.white.withAlphaComponent(0.13)
        field.layer.cornerRadius = 7
        field.font = .monospacedDigitSystemFont(ofSize: 13, weight: .semibold)
        field.textAlignment = .center
        field.heightAnchor.constraint(equalToConstant: 32).isActive = true
    }

    private func presentAutomationSheet(title: String, mode: String) {
        automationSheetMode = mode
        automationSheetTitle.text = title
        automationSheetTitle.textColor = .white
        clearStack(automationSheetContent)
        clearStack(automationSheetActions)
        automationSheet.isHidden = false
        moduleChooser.isHidden = true
        card.bringSubviewToFront(automationSheet)
    }

    private func addSheetAction(_ title: String, color: UIColor, selector: Selector) {
        let button = textButton(title, color: color, height: 30, fontSize: 11)
        button.widthAnchor.constraint(greaterThanOrEqualToConstant: 72).isActive = true
        button.addTarget(self, action: selector, for: .touchUpInside)
        automationSheetActions.addArrangedSubview(button)
    }

    private func sheetText(_ text: String, color: UIColor = .white, fontSize: CGFloat = 12) -> UILabel {
        let label = UILabel()
        label.text = text
        label.textColor = color
        label.font = .systemFont(ofSize: fontSize, weight: .semibold)
        label.numberOfLines = 0
        label.lineBreakMode = .byWordWrapping
        return label
    }

    private func sheetFieldRow(_ title: String, field: UITextField) -> UIStackView {
        let label = sheetText(title, color: UIColor.white.withAlphaComponent(0.86), fontSize: 12)
        label.widthAnchor.constraint(equalToConstant: 118).isActive = true
        field.widthAnchor.constraint(equalToConstant: 130).isActive = true
        let row = UIStackView(arrangedSubviews: [label, field, UIView()])
        row.axis = .horizontal
        row.alignment = .center
        row.spacing = 8
        return row
    }

    private func showLoopSettings() {
        presentAutomationSheet(title: "循環播放設定", mode: "loop")
        let defaults = UserDefaults.standard
        if let count = defaults.object(forKey: LoopDefaults.repeatCount) as? NSNumber {
            loopCountField.text = count.stringValue
        } else {
            loopCountField.text = ""
        }
        let cooldown = defaults.object(forKey: LoopDefaults.cooldownSeconds) as? NSNumber
        loopCooldownField.text = String(cooldown?.intValue ?? 30)
        automationSheetContent.addArrangedSubview(sheetText("循環次數留空代表一直循環。每輪會完整播放目前的模組連串。"))
        automationSheetContent.addArrangedSubview(sheetFieldRow("循環次數", field: loopCountField))
        automationSheetContent.addArrangedSubview(sheetFieldRow("每輪冷卻秒數", field: loopCooldownField))
        automationSheetActions.addArrangedSubview(UIView())
        addSheetAction(
            "儲存設定",
            color: UIColor(red: 0.77, green: 0.39, blue: 0.08, alpha: 1),
            selector: #selector(saveLoopSettingsTapped)
        )
    }

    private func showScheduledCreate(slots: [Int], modules: [String]) {
        pendingScheduledSlots = slots
        pendingScheduledModules = modules
        presentAutomationSheet(title: "建立定時播放", mode: "scheduled-create")
        let initialDate = Date().addingTimeInterval(60)
        scheduledDatePicker.minimumDate = Date().addingTimeInterval(1)
        scheduledDatePicker.date = initialDate
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Taipei") ?? .current
        scheduledSecondField.text = String(format: "%02d", calendar.component(.second, from: initialDate))
        automationSheetContent.addArrangedSubview(
            sheetText("SLOT \(slots.map(String.init).joined(separator: ", "))｜\(modules.joined(separator: " > "))")
        )
        automationSheetContent.addArrangedSubview(
            sheetText("以下日期和時間一律使用台北時間（24 小時制）。", color: UIColor(red: 1, green: 0.82, blue: 0.28, alpha: 1))
        )
        automationSheetContent.addArrangedSubview(scheduledDatePicker)
        automationSheetContent.addArrangedSubview(sheetFieldRow("秒數", field: scheduledSecondField))
        automationSheetActions.addArrangedSubview(UIView())
        addSheetAction(
            "建立定時播放",
            color: UIColor(red: 0.92, green: 0.68, blue: 0.08, alpha: 1),
            selector: #selector(confirmScheduledPlaybackTapped)
        )
    }

    private func showScheduledSettings() {
        presentAutomationSheet(title: "定時設定：等待中的命令", mode: "scheduled-settings")
        renderScheduledSettings()
    }

    private func renderScheduledSettings() {
        guard automationSheetMode == "scheduled-settings" else { return }
        clearStack(automationSheetContent)
        clearStack(automationSheetActions)
        let jobs = playbackAutomations
            .filter { $0.mode == "scheduled_once" && $0.status == "waiting" }
            .sorted { ($0.runAt ?? 0) < ($1.runAt ?? 0) }
        guard !jobs.isEmpty else {
            automationSheetContent.addArrangedSubview(sheetText("目前沒有等待中的定時播放命令。", color: UIColor.white.withAlphaComponent(0.72)))
            return
        }
        for job in jobs {
            let label = sheetText(
                "SLOT \(job.slots.map(String.init).joined(separator: ", "))｜台北時間 \(formatTaipei(job.runAt))\n\(automationTarget(job))",
                fontSize: 11
            )
            let remove = textButton("刪除", color: UIColor(red: 0.72, green: 0.18, blue: 0.16, alpha: 1), height: 30, fontSize: 11)
            remove.widthAnchor.constraint(equalToConstant: 54).isActive = true
            remove.addAction(UIAction { [weak self] _ in self?.onCancelAutomation?(job.id) }, for: .touchUpInside)
            let row = UIStackView(arrangedSubviews: [label, remove])
            row.axis = .horizontal
            row.alignment = .center
            row.spacing = 8
            automationSheetContent.addArrangedSubview(row)
            automationSheetContent.addArrangedSubview(divider())
        }
    }

    private func showAutomationLog() {
        presentAutomationSheet(title: "定時／循環播放狀況", mode: "log")
        renderAutomationLog()
    }

    private func renderAutomationLog() {
        guard automationSheetMode == "log" else { return }
        clearStack(automationSheetContent)
        clearStack(automationSheetActions)
        automationSheetContent.addArrangedSubview(automationLogRow(slot: "Slot", scheduled: "定時播放", loop: "循環播放", header: true))
        for slot in OPLINKSlots.range {
            let scheduled = playbackAutomations
                .filter { $0.mode == "scheduled_once" && ["waiting", "running"].contains($0.status) && $0.slots.contains(slot) }
                .sorted { ($0.runAt ?? 0) < ($1.runAt ?? 0) }
            let scheduledText = scheduled.isEmpty ? "—" : scheduled.map {
                "\($0.status == "running" ? "播放中" : "等待中") \(formatTaipei($0.runAt))\n\(automationTarget($0))"
            }.joined(separator: "\n")
            let loop = playbackAutomations.first {
                $0.mode == "loop" && $0.isActive && $0.slots.contains(slot)
            }
            let loopText = loop.map { "\(loopStatusText($0))\n\(automationTarget($0))" } ?? "—"
            automationSheetContent.addArrangedSubview(
                automationLogRow(slot: String(format: "%02d", slot), scheduled: scheduledText, loop: loopText, header: false)
            )
        }
    }

    private func automationLogRow(slot: String, scheduled: String, loop: String, header: Bool) -> UIStackView {
        let color = header ? UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1) : .white
        let slotLabel = sheetText(slot, color: color, fontSize: header ? 11 : 10)
        slotLabel.textAlignment = .center
        slotLabel.widthAnchor.constraint(equalToConstant: 40).isActive = true
        let scheduledLabel = sheetText(scheduled, color: color, fontSize: header ? 11 : 9.5)
        let loopLabel = sheetText(loop, color: color, fontSize: header ? 11 : 9.5)
        scheduledLabel.widthAnchor.constraint(equalTo: loopLabel.widthAnchor).isActive = true
        let row = UIStackView(arrangedSubviews: [slotLabel, scheduledLabel, loopLabel])
        row.axis = .horizontal
        row.alignment = .top
        row.spacing = 8
        row.layoutMargins = UIEdgeInsets(top: 4, left: 3, bottom: 4, right: 3)
        row.isLayoutMarginsRelativeArrangement = true
        row.backgroundColor = header ? UIColor.white.withAlphaComponent(0.09) : UIColor.clear
        return row
    }

    private func refreshOpenAutomationSheet() {
        if automationSheetMode == "scheduled-settings" {
            renderScheduledSettings()
        } else if automationSheetMode == "log" {
            renderAutomationLog()
        }
    }

    private func automationTarget(_ job: GUIPlaybackAutomation) -> String {
        job.targetKind == "script" ? (job.script ?? "") : job.modules.joined(separator: " > ")
    }

    private func formatTaipei(_ timestamp: Double?) -> String {
        guard let timestamp else { return "—" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_Hant_TW")
        formatter.timeZone = TimeZone(identifier: "Asia/Taipei")
        formatter.dateFormat = "MM/dd HH:mm:ss"
        return formatter.string(from: Date(timeIntervalSince1970: timestamp))
    }

    private func loopStatusText(_ job: GUIPlaybackAutomation) -> String {
        let total = job.repeatCount.map(String.init) ?? "∞"
        if job.status == "running" {
            return "播放中 第 \(job.iteration + 1)/\(total) 次"
        }
        if job.status == "cooling" {
            let remaining = max(0, Int(ceil((job.nextRunAt ?? Date().timeIntervalSince1970) - Date().timeIntervalSince1970)))
            return "冷卻中 \(job.iteration)/\(total)｜下輪 \(remaining)s"
        }
        return "等待中 \(job.iteration)/\(total)"
    }

    private func rebuildModuleButtons() {
        clearStack(moduleGroupsStack)
        clearStack(quickModuleGroupsStack)
        guard !moduleGroups.isEmpty else {
            moduleGroupsStack.addArrangedSubview(emptyModulesLabel())
            quickModuleGroupsStack.addArrangedSubview(emptyModulesLabel())
            return
        }

        populateModuleGroups(
            in: quickModuleGroupsStack,
            columns: 5,
            buttonHeight: ModulePanelMetrics.quickButtonHeight,
            fontSize: ModulePanelMetrics.quickFontSize,
            fixedButtonWidth: nil,
            singleLine: true,
            groupsPerRow: 2
        )
        populateModuleGroups(
            in: moduleGroupsStack,
            columns: 4,
            buttonHeight: 36,
            fontSize: 12,
            fixedButtonWidth: nil,
            singleLine: false
        )
    }

    private func populateModuleGroups(
        in container: UIStackView,
        columns: Int,
        buttonHeight: CGFloat,
        fontSize: CGFloat,
        fixedButtonWidth: CGFloat?,
        singleLine: Bool,
        groupsPerRow: Int = 1
    ) {
        var groupViews: [UIView] = []
        for group in moduleGroups {
            let label = UILabel()
            label.text = group.name
            label.textColor = .white
            label.font = .systemFont(ofSize: 11, weight: .bold)

            let grid = UIStackView()
            grid.axis = .vertical
            grid.spacing = 5
            for start in stride(from: 0, to: group.modules.count, by: columns) {
                let row = UIStackView()
                row.axis = .horizontal
                row.spacing = 5
                row.distribution = fixedButtonWidth == nil ? .fillEqually : .fill
                for offset in 0..<columns {
                    let moduleIndex = start + offset
                    if group.modules.indices.contains(moduleIndex),
                       let globalIndex = moduleNames.firstIndex(of: group.modules[moduleIndex]) {
                        let button = textButton(
                            group.modules[moduleIndex],
                            color: UIColor(white: 0.32, alpha: 1),
                            height: buttonHeight,
                            fontSize: fontSize
                        )
                        button.tag = globalIndex
                        button.accessibilityLabel = group.modules[moduleIndex]
                        button.titleLabel?.numberOfLines = singleLine ? 1 : 2
                        button.titleLabel?.textAlignment = .center
                        button.titleLabel?.adjustsFontSizeToFitWidth = !singleLine
                        button.titleLabel?.minimumScaleFactor = singleLine ? 1 : 0.72
                        button.titleLabel?.lineBreakMode = singleLine ? .byClipping : .byTruncatingTail
                        if let fixedButtonWidth {
                            button.widthAnchor.constraint(equalToConstant: fixedButtonWidth).isActive = true
                        }
                        button.addTarget(self, action: #selector(moduleTapped(_:)), for: .touchUpInside)
                        row.addArrangedSubview(button)
                    } else if fixedButtonWidth == nil {
                        row.addArrangedSubview(UIView())
                    }
                }
                if fixedButtonWidth != nil {
                    row.addArrangedSubview(UIView())
                }
                grid.addArrangedSubview(row)
            }
            let groupStack = UIStackView(arrangedSubviews: [label, grid])
            groupStack.axis = .vertical
            groupStack.spacing = 4
            groupViews.append(groupStack)
        }

        let rowSize = max(1, groupsPerRow)
        for start in stride(from: 0, to: groupViews.count, by: rowSize) {
            if rowSize == 1 {
                container.addArrangedSubview(groupViews[start])
                continue
            }

            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 8
            row.alignment = .top
            row.distribution = .fillEqually
            for offset in 0..<rowSize {
                let index = start + offset
                row.addArrangedSubview(groupViews.indices.contains(index) ? groupViews[index] : UIView())
            }
            container.addArrangedSubview(row)
        }
    }

    private func emptyModulesLabel() -> UILabel {
        let label = UILabel()
        label.text = "GUI_TEST_PC 沒有可用模組"
        label.textColor = UIColor.white.withAlphaComponent(0.7)
        label.textAlignment = .center
        label.font = .systemFont(ofSize: 11, weight: .semibold)
        return label
    }

    private func refreshSlotButtons() {
        for button in slotButtons {
            let slot = button.tag
            let running = runningSlots.contains(slot)
            let queuedOpening = queuedOpeningSlots.contains(slot)
            let opening = openingSlots.contains(slot)
            let closing = closingSlots.contains(slot)
            let restarting = restartingSlots.contains(slot)
            let loop = activeLoop(for: slot)
            let automation = activeAutomation(for: slot)
            let playing = playingSlots.contains(slot) || playbackAutomations.contains {
                $0.status == "running" && $0.slots.contains(slot)
            }
            let queuedPlayback = queuedPlaybackSlots.contains(slot)
            let reserved = automation != nil || pendingSubmissionSlots.contains(slot) || queuedPlayback
            let selected = selectedSlots.contains(slot)
            let moduleName = displayedModuleName(for: slot)
            if restarting {
                button.setTitle(String(format: "%02d\n重啟", slot), for: .normal)
                button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
                button.accessibilityLabel = "Slot \(slot)，正在重啟"
            } else if closing {
                button.setTitle(String(format: "%02d\n關閉", slot), for: .normal)
                button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
                button.accessibilityLabel = "Slot \(slot)，正在關閉"
            } else if opening {
                button.setTitle(String(format: "%02d\n開啟", slot), for: .normal)
                button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
                button.accessibilityLabel = "Slot \(slot)，正在開啟"
            } else if queuedOpening {
                button.setTitle(String(format: "%02d\n排隊", slot), for: .normal)
                button.titleLabel?.font = .systemFont(ofSize: 10, weight: .bold)
                button.accessibilityLabel = "Slot \(slot)，排隊開啟"
            } else if let moduleName {
                button.setTitle(moduleName, for: .normal)
                button.titleLabel?.font = .systemFont(ofSize: 12, weight: .bold)
                button.accessibilityLabel = "Slot \(slot)，\(moduleName)"
            } else {
                button.setTitle(String(format: "%02d", slot), for: .normal)
                button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 15, weight: .bold)
                button.accessibilityLabel = "Slot \(slot)"
            }
            if playing {
                button.backgroundColor = UIColor(red: 0.86, green: 0.12, blue: 0.12, alpha: 1)
                button.setTitleColor(.white, for: .normal)
            } else if opening || closing || restarting {
                button.backgroundColor = UIColor(red: 0.86, green: 0.12, blue: 0.12, alpha: 1)
                button.setTitleColor(.white, for: .normal)
            } else if reserved || loop != nil || queuedOpening {
                button.backgroundColor = UIColor(red: 0.64, green: 0.10, blue: 0.10, alpha: 1)
                button.setTitleColor(.white, for: .normal)
            } else if selected {
                button.backgroundColor = UIColor(red: 0.28, green: 0.84, blue: 0.42, alpha: 1)
                button.setTitleColor(UIColor(red: 0.02, green: 0.13, blue: 0.06, alpha: 1), for: .normal)
            } else {
                button.backgroundColor = UIColor(white: running ? 0.34 : 0.18, alpha: 1)
                button.setTitleColor(.white, for: .normal)
            }
            button.layer.borderWidth = selected ? 2.5 : 0
            button.layer.borderColor = UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1).cgColor
            button.alpha = running || selected || playing || reserved || opening || closing || restarting || queuedOpening || loop != nil ? 1 : 0.93
            updatePicoActivityAnimation(
                for: button,
                active: picoActivitySlot == slot
            )
            if restarting {
                button.accessibilityHint = "遊戲視窗正在重啟，暫時不能選取"
            } else if closing {
                button.accessibilityHint = "遊戲視窗正在關閉，暫時不能選取"
            } else if opening {
                button.accessibilityHint = "遊戲視窗正在開啟，暫時不能選取"
            } else if queuedOpening {
                button.accessibilityHint = "已送出開啟命令，正在等待 GUI_TEST_PC 接收"
            } else if loop != nil {
                button.accessibilityHint = "循環播放有效；可選取並用新設定取代"
            } else if playing {
                button.accessibilityHint = slotPlaybackStatus[String(slot)] ?? "播放中；可選取並用新設定取代"
            } else if reserved {
                button.accessibilityHint = "已有等待工作；可選取並用新設定取代"
            } else {
                button.accessibilityHint = running ? "在線" : "離線"
            }
        }
    }

    private func displayedModuleName(for slot: Int) -> String? {
        if playingSlots.contains(slot),
           let current = slotCurrentModule[String(slot)]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !current.isEmpty {
            return current
        }
        let jobs = playbackAutomations.filter { $0.isActive && $0.slots.contains(slot) }
        let job = jobs.sorted {
            ($0.runAt ?? $0.nextRunAt ?? .greatestFiniteMagnitude)
                < ($1.runAt ?? $1.nextRunAt ?? .greatestFiniteMagnitude)
        }.first
        if let module = job?.modules.first, !module.isEmpty { return module }
        if let script = job?.script, !script.isEmpty {
            return URL(fileURLWithPath: script).deletingPathExtension().lastPathComponent
        }
        return nil
    }

    private func updatePicoActivityAnimation(for button: UIButton, active: Bool) {
        let key = "oplink.pico.activity"
        if active {
            guard button.layer.animation(forKey: key) == nil else { return }
            let pulse = CABasicAnimation(keyPath: "opacity")
            pulse.fromValue = 1.0
            pulse.toValue = 0.55
            pulse.duration = 0.8
            pulse.autoreverses = true
            pulse.repeatCount = .infinity
            pulse.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            button.layer.add(pulse, forKey: key)
        } else {
            button.layer.removeAnimation(forKey: key)
        }
    }

    private func activeLoop(for slot: Int) -> GUIPlaybackAutomation? {
        playbackAutomations.first { $0.mode == "loop" && $0.isActive && $0.slots.contains(slot) }
    }

    private func activeAutomation(for slot: Int) -> GUIPlaybackAutomation? {
        playbackAutomations.first { $0.isActive && $0.slots.contains(slot) }
    }

    private var activeAutomationSlots: Set<Int> {
        playbackAutomations.reduce(into: Set<Int>()) { slots, automation in
            guard automation.isActive else { return }
            slots.formUnion(automation.slots)
        }
    }

    private var launcherTransitionSlots: Set<Int> {
        queuedOpeningSlots
            .union(openingSlots)
            .union(closingSlots)
            .union(restartingSlots)
    }

    private var automaticSelectionBlockedSlots: Set<Int> {
        playingSlots
            .union(queuedPlaybackSlots)
            .union(activeAutomationSlots)
            .union(pendingSubmissionSlots)
            .union(launcherTransitionSlots)
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
            button.layer.borderWidth = index == chainInsertion.activeCell ? 2 : 0
            button.layer.borderColor = UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1).cgColor
        }
        for button in chooserStepButtons {
            let index = button.tag
            let name = moduleChain[index]
            button.setTitle(name.map { "\(index + 1)\n\($0)" } ?? "\(index + 1)\n＋", for: .normal)
            let active = button.tag == activeChainIndex
            button.backgroundColor = active
                ? UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1)
                : UIColor(white: 0.28, alpha: 1)
            button.setTitleColor(active ? .black : .white, for: .normal)
        }
        activeChainIndex = chainInsertion.activeCell
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
            button.setTitle(preset.modules.isEmpty ? "\(index) ＋" : preset.name, for: .normal)
            button.setTitleColor(.white, for: .normal)
            button.backgroundColor = preset.modules.isEmpty
                ? UIColor(red: 0.12, green: 0.34, blue: 0.55, alpha: 1)
                : UIColor(red: 0.08, green: 0.5, blue: 0.45, alpha: 1)
            button.layer.borderColor = activePresetIndex == index
                ? UIColor(red: 0.47, green: 0.86, blue: 0.94, alpha: 1).cgColor
                : UIColor.white.withAlphaComponent(0.35).cgColor
            button.layer.borderWidth = activePresetIndex == index ? 2 : 1
            button.accessibilityHint = preset.modules.isEmpty
                ? "空白預設，點擊設定"
                : "點擊載入連串；長按編輯預設。\(preset.modules.joined(separator: "，"))"
        }
    }

    private func normalizedPresets(_ values: [GUIModuleChainPreset]) -> [GUIModuleChainPreset] {
        let byIndex = Dictionary(uniqueKeysWithValues: values.filter { (1...20).contains($0.index) }.map { ($0.index, $0) })
        return (1...20).map { index in
            byIndex[index] ?? GUIModuleChainPreset(index: index, name: "連串 \(index)", modules: [])
        }
    }

    private func iconButton(
        _ systemName: String,
        label: String,
        size: CGFloat = 30,
        symbolPointSize: CGFloat? = nil
    ) -> UIButton {
        let button = UIButton(type: .system)
        let image: UIImage?
        if let symbolPointSize {
            image = UIImage(
                systemName: systemName,
                withConfiguration: UIImage.SymbolConfiguration(pointSize: symbolPointSize, weight: .semibold)
            )
        } else {
            image = UIImage(systemName: systemName)
        }
        button.setImage(image, for: .normal)
        button.tintColor = .white
        button.backgroundColor = UIColor.white.withAlphaComponent(0.25)
        button.layer.cornerRadius = size / 2
        button.accessibilityLabel = label
        button.widthAnchor.constraint(equalToConstant: size).isActive = true
        button.heightAnchor.constraint(equalToConstant: size).isActive = true
        return button
    }

    private func textButton(
        _ title: String,
        color: UIColor,
        height: CGFloat = 27,
        fontSize: CGFloat = 10
    ) -> UIButton {
        let button = UIButton(type: .system)
        button.setTitle(title, for: .normal)
        button.setTitleColor(.white, for: .normal)
        button.titleLabel?.font = .systemFont(ofSize: fontSize, weight: .bold)
        button.titleLabel?.lineBreakMode = .byTruncatingTail
        button.backgroundColor = color
        button.layer.cornerRadius = 7
        button.heightAnchor.constraint(equalToConstant: height).isActive = true
        return button
    }

    private func compactTextButton(_ title: String, color: UIColor) -> UIButton {
        let button = textButton(title, color: color, height: 27, fontSize: 11)
        button.widthAnchor.constraint(equalToConstant: 44).isActive = true
        return button
    }

    private func compactActionRow(_ buttons: [UIButton]) -> UIStackView {
        let row = UIStackView(arrangedSubviews: buttons + [UIView()])
        row.axis = .horizontal
        row.spacing = 5
        row.distribution = .fill
        return row
    }

    private func divider() -> UIView {
        let view = UIView()
        view.backgroundColor = UIColor.white.withAlphaComponent(0.16)
        view.heightAnchor.constraint(equalToConstant: 1).isActive = true
        return view
    }

    private func clearStack(_ stack: UIStackView) {
        for view in stack.arrangedSubviews {
            stack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
    }

    private func requireSelectedSlots() -> [Int]? {
        let skipped = selectedSlots.intersection(launcherTransitionSlots).sorted()
        selectedSlots.subtract(skipped)
        let slots = selectedSlots.sorted()
        if slots.isEmpty {
            setStatus(
                skipped.isEmpty ? "請先選擇至少一個遊戲視窗。" : "所選 Slot 正在開啟、關閉或重啟，本次沒有送出。",
                good: false
            )
            refreshSlotButtons()
            return nil
        }
        if !skipped.isEmpty {
            setStatus("Slot \(skipped.map(String.init).joined(separator: ",")) 正在切換遊戲狀態，已略過", good: false)
        }
        return slots
    }

    private func selectedPlaybackPlan() -> (slots: [Int], modules: [String])? {
        guard let slots = requireSelectedSlots() else { return nil }
        let modules = moduleChain.compactMap { $0 }
        guard !modules.isEmpty else {
            setStatus("請先設定至少一個模組。", good: false)
            return nil
        }
        return (slots, modules)
    }

    private func chainValues() -> [String] {
        moduleChain.compactMap { $0 }
    }

    private func chainStart() -> Int {
        moduleChain.firstIndex(where: { $0 != nil }) == 0 ? 0 : 1
    }

    @discardableResult
    private func packChain(_ values: [String], preferredStart: Int) -> Int {
        let start = values.count == 10 ? 0 : max(0, min(preferredStart, 10 - values.count))
        moduleChain = Array(repeating: nil, count: 10)
        for (offset, name) in values.enumerated() {
            moduleChain[start + offset] = name
        }
        return start
    }

    private func mapPresetToEditableCells(_ values: [String]) {
        let mapped = Array(values.prefix(8))
        moduleChain = Array(repeating: nil, count: 10)
        for (offset, name) in mapped.enumerated() {
            moduleChain[offset + 1] = name
        }
        activeChainIndex = min(9, mapped.count + 1)
        chainInsertion = ChainInsertion(
            position: mapped.count,
            start: 1,
            mode: .tail,
            activeCell: activeChainIndex
        )
    }

    private func insertModule(_ moduleName: String) {
        var values = chainValues()
        guard values.count < 10 else {
            setStatus("加入後會超過 10 個模組，本次未加入。", good: false)
            return
        }
        activePresetIndex = nil
        let position = chainInsertion.mode == .tail
            ? values.count
            : min(chainInsertion.position, values.count)
        values.insert(moduleName, at: position)
        let preferredStart = chainInsertion.mode == .head ? 0 : chainInsertion.start
        let start = packChain(values, preferredStart: preferredStart)
        let nextPosition = position + 1
        chainInsertion.position = nextPosition
        chainInsertion.start = start
        chainInsertion.activeCell = min(9, start + nextPosition)
        refreshChainButtons()
        refreshPresetButtons()
    }

    private func setAutomationSheetError(_ text: String) {
        automationSheetTitle.text = text
        automationSheetTitle.textColor = UIColor(red: 1, green: 0.42, blue: 0.35, alpha: 1)
    }

    @objc private func closeTapped() { onClose?() }
    @objc private func refreshTapped() { onRefresh?() }
    @objc private func restartControllerTapped() {
        restartControllerButton.isEnabled = false
        setStatus("正在要求 PC 重啟 GUI_TEST_PC 控制器...", good: true)
        onRestartController?()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.restartControllerButton.isEnabled = true
        }
    }

    @objc private func slotTapped(_ sender: UIButton) {
        let slot = sender.tag
        if openingSlots.contains(slot) || closingSlots.contains(slot) || restartingSlots.contains(slot) || queuedOpeningSlots.contains(slot) {
            let status = restartingSlots.contains(slot)
                ? "正在重啟"
                : (closingSlots.contains(slot)
                ? "正在關閉"
                : (openingSlots.contains(slot) ? "正在開啟" : "正在排隊開啟"))
            setStatus("GAME \(slot) \(status)，不能重複選取。", good: false)
            return
        }
        autoSelectedSlot = nil
        if selectedSlots.contains(slot) {
            selectedSlots.remove(slot)
        } else {
            selectedSlots.insert(slot)
        }
        refreshSlotButtons()
    }

    @objc private func selectAllTapped() {
        autoSelectedSlot = nil
        selectedSlots = Set(OPLINKSlots.range).subtracting(launcherTransitionSlots)
        refreshSlotButtons()
    }

    @objc private func clearSlotsTapped() {
        autoSelectedSlot = nil
        selectedSlots.removeAll()
        refreshSlotButtons()
    }

    @objc private func restartSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("restart", slots)
    }

    @objc private func chainTapped(_ sender: UIButton) {
        activePresetIndex = nil
        let index = sender.tag
        activeChainIndex = index
        if let removedModule = moduleChain[index] {
            moduleChain[index] = nil
            chainInsertion = ChainInsertion(position: 0, start: chainStart(), mode: .index, activeCell: index)
            refreshChainButtons()
            setStatus("已清除第 \(activeChainIndex + 1) 格：\(removedModule)", good: true)
            return
        }
        let position = moduleChain[..<index].compactMap { $0 }.count
        let mode: ChainInsertionMode = index == 0 ? .head : (index == 9 ? .tail : .index)
        chainInsertion = ChainInsertion(
            position: position,
            start: mode == .head ? 0 : chainStart(),
            mode: mode,
            activeCell: index
        )
        refreshChainButtons()
        setStatus("第 \(activeChainIndex + 1) 格等待選擇模組", good: true)
    }

    @objc private func presetTapped(_ sender: UIButton) {
        let index = sender.tag
        let preset = presets.first { $0.index == index }
            ?? GUIModuleChainPreset(index: index, name: "連串 \(index)", modules: [])
        guard !preset.modules.isEmpty else {
            setStatus("這個連串預設目前是空的；長按可儲存目前連串。", good: false)
            return
        }
        activePresetIndex = index
        let values = Array(preset.modules.prefix(8))
        mapPresetToEditableCells(values)
        moduleChooser.isHidden = true
        refreshChainButtons()
        guard let slots = requireSelectedSlots() else {
            setStatus("已載入 \(preset.name) 到第 2–9 格；選擇 Slot 後再按預設即可播放。", good: false)
            return
        }
        beginPlaybackSubmission(slots: slots, feedbackButton: sender)
        onPlay?(slots, values)
    }

    @objc private func presetLongPressed(_ gesture: UILongPressGestureRecognizer) {
        guard gesture.state == .began, let button = gesture.view as? UIButton else { return }
        editPreset(button.tag)
    }

    private func editPreset(_ index: Int) {
        let preset = presets.first { $0.index == index }
            ?? GUIModuleChainPreset(index: index, name: "連串 \(index)", modules: [])
        activePresetIndex = index
        let values = Array(preset.modules.prefix(8))
        mapPresetToEditableCells(values)
        refreshChainButtons()
        moduleChooser.isHidden = false
    }

    @objc private func chooserStepTapped(_ sender: UIButton) {
        activeChainIndex = sender.tag
        chainInsertion = ChainInsertion(
            position: moduleChain[..<sender.tag].compactMap { $0 }.count,
            start: sender.tag == 0 ? 0 : chainStart(),
            mode: sender.tag == 0 ? .head : (sender.tag == 9 ? .tail : .index),
            activeCell: sender.tag
        )
        refreshChainButtons()
    }

    @objc private func moduleTapped(_ sender: UIButton) {
        guard moduleNames.indices.contains(sender.tag) else { return }
        let moduleName = moduleNames[sender.tag]
        if moduleChooser.isHidden {
            insertModule(moduleName)
            setStatus("已加入 \(moduleName)。", good: true)
        } else {
            let insertedIndex = activeChainIndex
            moduleChain[insertedIndex] = moduleName
            if activeChainIndex < 9 { activeChainIndex += 1 }
            chainInsertion.activeCell = activeChainIndex
            refreshChainButtons()
            setStatus("第 \(insertedIndex + 1) 格已加入 \(moduleName)。", good: true)
        }
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
        activePresetIndex = nil
        chainInsertion = ChainInsertion(position: 0, start: 1, mode: .index, activeCell: 1)
        refreshChainButtons()
        refreshPresetButtons()
    }

    @objc private func playTapped() {
        guard let payload = selectedPlaybackPlan() else { return }
        beginPlaybackSubmission(slots: payload.slots)
        onPlay?(payload.slots, payload.modules)
    }

    @objc private func scheduledPlayTapped() {
        guard let payload = selectedPlaybackPlan() else { return }
        showScheduledCreate(slots: payload.slots, modules: payload.modules)
    }

    @objc private func loopSettingsTapped() { showLoopSettings() }

    @objc private func saveLoopSettingsTapped() {
        let rawCount = loopCountField.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let rawCooldown = loopCooldownField.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let repeatCount = rawCount.isEmpty ? nil : Int(rawCount)
        guard rawCount.isEmpty || (repeatCount ?? 0) > 0 else {
            setAutomationSheetError("循環次數必須是正整數或留空")
            return
        }
        guard let cooldown = Int(rawCooldown), (0...3600).contains(cooldown) else {
            setAutomationSheetError("冷卻秒數必須是 0–3600")
            return
        }
        let defaults = UserDefaults.standard
        if let repeatCount {
            defaults.set(repeatCount, forKey: LoopDefaults.repeatCount)
        } else {
            defaults.removeObject(forKey: LoopDefaults.repeatCount)
        }
        defaults.set(cooldown, forKey: LoopDefaults.cooldownSeconds)
        closeAutomationSheetTapped()
    }

    @objc private func loopStartTapped() {
        guard let payload = selectedPlaybackPlan() else { return }
        let defaults = UserDefaults.standard
        let repeatCount = (defaults.object(forKey: LoopDefaults.repeatCount) as? NSNumber)?.intValue
        let cooldown = (defaults.object(forKey: LoopDefaults.cooldownSeconds) as? NSNumber)?.intValue ?? 30
        beginPlaybackSubmission(slots: payload.slots)
        onStartLoop?(payload.slots, payload.modules, repeatCount, cooldown)
    }

    @objc private func confirmScheduledPlaybackTapped() {
        guard !pendingScheduledSlots.isEmpty, !pendingScheduledModules.isEmpty else {
            setAutomationSheetError("定時播放內容已失效，請重新選擇")
            return
        }
        let rawSecond = scheduledSecondField.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard let second = Int(rawSecond), (0...59).contains(second) else {
            setAutomationSheetError("秒數必須是 00–59")
            return
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Taipei") ?? .current
        var components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: scheduledDatePicker.date)
        components.second = second
        components.timeZone = calendar.timeZone
        guard let targetDate = calendar.date(from: components), targetDate > Date() else {
            setAutomationSheetError("定時播放時間必須晚於目前台北時間")
            return
        }
        let slots = pendingScheduledSlots
        let modules = pendingScheduledModules
        closeAutomationSheetTapped()
        beginPlaybackSubmission(slots: slots)
        onCreateScheduled?(slots, modules, targetDate)
    }

    @objc private func scheduledSettingsTapped() { showScheduledSettings() }
    @objc private func automationLogTapped() { showAutomationLog() }

    @objc private func closeAutomationSheetTapped() {
        automationSheetMode = nil
        automationSheet.isHidden = true
        pendingScheduledSlots = []
        pendingScheduledModules = []
        endEditing(true)
    }

    @objc private func stopAllTapped() { onStopAll?() }
    @objc private func startAllTapped() { onLauncher?("start-missing", OPLINKSlots.all) }

    @objc private func startSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("start", slots)
    }

    @objc private func closeSelectedTapped() {
        guard let slots = requireSelectedSlots() else { return }
        onLauncher?("stop", slots)
    }

    @objc private func closeAllTapped() { onLauncher?("stop", OPLINKSlots.all) }

    @objc private func arrangeTapped() {
        let slots = runningSlots.sorted()
        guard !slots.isEmpty else {
            setStatus("目前沒有運行中的遊戲視窗。", good: false)
            return
        }
        onArrange?(slots)
    }

    private func beginPlaybackSubmission(slots: [Int], feedbackButton: UIButton? = nil) {
        pendingSubmissionSlots.formUnion(slots)
        let expiresAt = Date().addingTimeInterval(30)
        for slot in slots { pendingSubmissionExpiry[slot] = expiresAt }
        selectedSlots.subtract(slots)
        if let autoSelectedSlot, slots.contains(autoSelectedSlot) {
            self.autoSelectedSlot = nil
        }
        setStatus("正在送出 Slot \(slots.map(String.init).joined(separator: ","))...", good: true)
        refreshSlotButtons()
        guard let button = feedbackButton ?? playActionButton else { return }
        UIView.animate(withDuration: 0.1, animations: {
            button.transform = CGAffineTransform(scaleX: 0.86, y: 0.86)
            button.alpha = 0.65
        }) { _ in
            UIView.animate(withDuration: 0.18) {
                button.transform = .identity
                button.alpha = 1
            }
        }
    }
}

private extension Collection where Element == String {
    func sortedLocalized() -> [String] {
        sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }
}
