import UIKit

final class AssetInventoryPanelView: UIView {
    var onClose: (() -> Void)?
    var onRefresh: (() -> Void)?

    private let card = UIView()
    private let titleLabel = UILabel()
    private let hintLabel = UILabel()
    private let statusLabel = UILabel()
    private let refreshButton = UIButton(type: .system)
    private let closeButton = UIButton(type: .system)
    private let scrollView = UIScrollView()
    private let slotStack = UIStackView()
    private var slotViews: [Int: AssetSlotAccordionView] = [:]
    private var latestItems: [GUIAssetInventoryItem] = []
    private var expandedSlot: Int?

    override init(frame: CGRect) {
        super.init(frame: frame)
        buildLayout()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        buildLayout()
    }

    func prepareForPresentation(streamSlot: Int) {
        expandedSlot = OPLINKSlots.range.contains(streamSlot) ? streamSlot : OPLINKSlots.minimum
        rebuildSlots()
        setLoading()
    }

    func setLoading() {
        statusLabel.text = "讀取最新盤點資料..."
        statusLabel.textColor = UIColor.white.withAlphaComponent(0.68)
        refreshButton.isEnabled = false
    }

    func apply(_ response: GUIAssetInventoryResponse) {
        latestItems = response.items
        refreshButton.isEnabled = true
        statusLabel.text = response.databaseReady
            ? "只顯示各 Slot 最近一次修裝盤點；部分資料仍會計算成功欄位總和。"
            : "盤點資料庫尚未建立。"
        statusLabel.textColor = response.databaseReady
            ? UIColor.white.withAlphaComponent(0.68)
            : UIColor(red: 1, green: 0.47, blue: 0.42, alpha: 1)
        rebuildSlots()
    }

    func setError(_ message: String) {
        refreshButton.isEnabled = true
        statusLabel.text = message
        statusLabel.textColor = UIColor(red: 1, green: 0.47, blue: 0.42, alpha: 1)
    }

    private func buildLayout() {
        backgroundColor = UIColor.black.withAlphaComponent(0.78)

        card.translatesAutoresizingMaskIntoConstraints = false
        card.backgroundColor = UIColor(red: 0.045, green: 0.085, blue: 0.072, alpha: 0.98)
        card.layer.cornerRadius = 16
        card.layer.borderWidth = 1
        card.layer.borderColor = UIColor.white.withAlphaComponent(0.16).cgColor
        addSubview(card)

        titleLabel.font = .systemFont(ofSize: 18, weight: .heavy)
        titleLabel.textColor = .white
        titleLabel.text = "最新角色資產"

        hintLabel.font = .systemFont(ofSize: 10, weight: .semibold)
        hintLabel.textColor = UIColor.white.withAlphaComponent(0.62)
        hintLabel.text = "綠燈：角色 2–5 的左側武器格均有裝備；其餘狀態均為紅燈。"
        hintLabel.numberOfLines = 2

        statusLabel.font = .systemFont(ofSize: 10, weight: .medium)
        statusLabel.textColor = UIColor.white.withAlphaComponent(0.68)
        statusLabel.numberOfLines = 2

        refreshButton.setImage(UIImage(systemName: "arrow.clockwise"), for: .normal)
        refreshButton.tintColor = .white
        refreshButton.backgroundColor = UIColor.white.withAlphaComponent(0.1)
        refreshButton.layer.cornerRadius = 9
        refreshButton.accessibilityLabel = "重新讀取資產盤點"
        refreshButton.addTarget(self, action: #selector(refreshTapped), for: .touchUpInside)

        closeButton.setImage(UIImage(systemName: "xmark"), for: .normal)
        closeButton.tintColor = .white
        closeButton.backgroundColor = UIColor.white.withAlphaComponent(0.1)
        closeButton.layer.cornerRadius = 9
        closeButton.accessibilityLabel = "關閉資產盤點"
        closeButton.addTarget(self, action: #selector(closeTapped), for: .touchUpInside)

        let textStack = UIStackView(arrangedSubviews: [titleLabel, hintLabel, statusLabel])
        textStack.axis = .vertical
        textStack.spacing = 3

        let actions = UIStackView(arrangedSubviews: [refreshButton, closeButton])
        actions.axis = .horizontal
        actions.spacing = 6
        actions.alignment = .top
        refreshButton.widthAnchor.constraint(equalToConstant: 40).isActive = true
        refreshButton.heightAnchor.constraint(equalToConstant: 36).isActive = true
        closeButton.widthAnchor.constraint(equalToConstant: 40).isActive = true
        closeButton.heightAnchor.constraint(equalToConstant: 36).isActive = true

        let header = UIStackView(arrangedSubviews: [textStack, actions])
        header.axis = .horizontal
        header.alignment = .top
        header.spacing = 10
        header.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(header)

        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.alwaysBounceVertical = true
        scrollView.showsVerticalScrollIndicator = true
        card.addSubview(scrollView)

        slotStack.translatesAutoresizingMaskIntoConstraints = false
        slotStack.axis = .vertical
        slotStack.spacing = 7
        scrollView.addSubview(slotStack)

        NSLayoutConstraint.activate([
            card.leadingAnchor.constraint(equalTo: safeAreaLayoutGuide.leadingAnchor, constant: 12),
            card.trailingAnchor.constraint(equalTo: safeAreaLayoutGuide.trailingAnchor, constant: -12),
            card.topAnchor.constraint(equalTo: safeAreaLayoutGuide.topAnchor, constant: 10),
            card.bottomAnchor.constraint(equalTo: safeAreaLayoutGuide.bottomAnchor, constant: -10),

            header.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 13),
            header.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -13),
            header.topAnchor.constraint(equalTo: card.topAnchor, constant: 11),

            scrollView.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 10),
            scrollView.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -10),
            scrollView.topAnchor.constraint(equalTo: header.bottomAnchor, constant: 9),
            scrollView.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -10),

            slotStack.leadingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.leadingAnchor),
            slotStack.trailingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.trailingAnchor),
            slotStack.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor),
            slotStack.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor),
            slotStack.widthAnchor.constraint(equalTo: scrollView.frameLayoutGuide.widthAnchor)
        ])
    }

    private func rebuildSlots() {
        slotStack.arrangedSubviews.forEach { view in
            slotStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        slotViews.removeAll()

        let grouped = Dictionary(grouping: latestItems, by: \GUIAssetInventoryItem.slot)
        for slot in OPLINKSlots.range {
            var readings: [Int: GUIAssetInventoryItem] = [:]
            for item in grouped[slot] ?? [] {
                readings[item.characterIndex] = item
            }
            let slotView = AssetSlotAccordionView(slot: slot)
            slotView.apply(readings: readings, expanded: expandedSlot == slot)
            slotView.onToggle = { [weak self] selectedSlot in
                guard let self else { return }
                self.expandedSlot = self.expandedSlot == selectedSlot ? nil : selectedSlot
                self.slotViews.forEach { slot, view in
                    view.setExpanded(self.expandedSlot == slot)
                }
            }
            slotViews[slot] = slotView
            slotStack.addArrangedSubview(slotView)
        }
    }

    @objc private func refreshTapped() {
        setLoading()
        onRefresh?()
    }

    @objc private func closeTapped() {
        onClose?()
    }
}

private final class AssetSlotAccordionView: UIView {
    var onToggle: ((Int) -> Void)?

    private let slot: Int
    private let contentStack = UIStackView()
    private let headerButton = UIButton(type: .system)
    private let indicator = UIView()
    private let slotLabel = UILabel()
    private let summaryLabel = UILabel()
    private let updatedLabel = UILabel()
    private let chevron = UIImageView()
    private let details = UIStackView()
    private var readings: [Int: GUIAssetInventoryItem] = [:]

    init(slot: Int) {
        self.slot = slot
        super.init(frame: .zero)
        buildLayout()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func apply(readings: [Int: GUIAssetInventoryItem], expanded: Bool) {
        self.readings = readings
        let equipmentReady = (2...5).allSatisfy { readings[$0]?.weaponEmpty == false }
        indicator.backgroundColor = equipmentReady
            ? UIColor(red: 0.19, green: 0.83, blue: 0.5, alpha: 1)
            : UIColor(red: 0.89, green: 0.27, blue: 0.23, alpha: 1)
        indicator.layer.shadowColor = indicator.backgroundColor?.cgColor
        indicator.layer.shadowOpacity = 0.85
        indicator.layer.shadowRadius = 5

        slotLabel.text = String(format: "Slot %02d", slot)
        summaryLabel.text = [
            summaryText("魔幣", keyPath: \GUIAssetInventoryItem.coins, truncateThousands: true),
            summaryText("綁晶", keyPath: \GUIAssetInventoryItem.boundCrystals),
            summaryText("非綁晶", keyPath: \GUIAssetInventoryItem.unboundCrystals)
        ].joined(separator: "   ")
        updatedLabel.text = newestTimestamp().map(Self.displayTimestamp) ?? "尚未盤點"
        headerButton.accessibilityLabel = [
            slotLabel.text ?? "",
            equipmentReady ? "裝備完整" : "裝備欠缺或資料不完整",
            summaryLabel.text ?? "",
            updatedLabel.text ?? ""
        ].joined(separator: "，")
        rebuildDetails()
        setExpanded(expanded)
    }

    func setExpanded(_ expanded: Bool) {
        details.isHidden = !expanded
        chevron.image = UIImage(systemName: expanded ? "chevron.down" : "chevron.right")
        headerButton.accessibilityValue = expanded ? "已展開" : "已摺疊"
    }

    private func buildLayout() {
        backgroundColor = UIColor(red: 0.08, green: 0.13, blue: 0.11, alpha: 1)
        layer.cornerRadius = 11
        layer.borderWidth = 1
        layer.borderColor = UIColor.white.withAlphaComponent(0.13).cgColor
        clipsToBounds = true

        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentStack.axis = .vertical
        contentStack.spacing = 0
        addSubview(contentStack)

        headerButton.backgroundColor = UIColor.white.withAlphaComponent(0.055)
        headerButton.addTarget(self, action: #selector(toggleTapped), for: .touchUpInside)
        contentStack.addArrangedSubview(headerButton)

        indicator.translatesAutoresizingMaskIntoConstraints = false
        indicator.layer.cornerRadius = 5
        headerButton.addSubview(indicator)

        slotLabel.translatesAutoresizingMaskIntoConstraints = false
        slotLabel.font = .monospacedSystemFont(ofSize: 13, weight: .bold)
        slotLabel.textColor = .white
        headerButton.addSubview(slotLabel)

        summaryLabel.translatesAutoresizingMaskIntoConstraints = false
        summaryLabel.font = .monospacedDigitSystemFont(ofSize: 11, weight: .semibold)
        summaryLabel.textColor = UIColor.white.withAlphaComponent(0.87)
        summaryLabel.numberOfLines = 2
        summaryLabel.adjustsFontSizeToFitWidth = true
        summaryLabel.minimumScaleFactor = 0.7
        headerButton.addSubview(summaryLabel)

        updatedLabel.translatesAutoresizingMaskIntoConstraints = false
        updatedLabel.font = .monospacedDigitSystemFont(ofSize: 9, weight: .medium)
        updatedLabel.textColor = UIColor.white.withAlphaComponent(0.58)
        updatedLabel.textAlignment = .right
        updatedLabel.numberOfLines = 2
        headerButton.addSubview(updatedLabel)

        chevron.translatesAutoresizingMaskIntoConstraints = false
        chevron.tintColor = UIColor.white.withAlphaComponent(0.62)
        chevron.contentMode = .scaleAspectFit
        headerButton.addSubview(chevron)

        details.axis = .vertical
        details.spacing = 0
        contentStack.addArrangedSubview(details)

        NSLayoutConstraint.activate([
            contentStack.leadingAnchor.constraint(equalTo: leadingAnchor),
            contentStack.trailingAnchor.constraint(equalTo: trailingAnchor),
            contentStack.topAnchor.constraint(equalTo: topAnchor),
            contentStack.bottomAnchor.constraint(equalTo: bottomAnchor),
            headerButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 52),

            indicator.leadingAnchor.constraint(equalTo: headerButton.leadingAnchor, constant: 11),
            indicator.centerYAnchor.constraint(equalTo: headerButton.centerYAnchor),
            indicator.widthAnchor.constraint(equalToConstant: 10),
            indicator.heightAnchor.constraint(equalToConstant: 10),

            slotLabel.leadingAnchor.constraint(equalTo: indicator.trailingAnchor, constant: 8),
            slotLabel.centerYAnchor.constraint(equalTo: headerButton.centerYAnchor),
            slotLabel.widthAnchor.constraint(equalToConstant: 62),

            summaryLabel.leadingAnchor.constraint(equalTo: slotLabel.trailingAnchor, constant: 8),
            summaryLabel.centerYAnchor.constraint(equalTo: headerButton.centerYAnchor),

            updatedLabel.leadingAnchor.constraint(greaterThanOrEqualTo: summaryLabel.trailingAnchor, constant: 6),
            updatedLabel.centerYAnchor.constraint(equalTo: headerButton.centerYAnchor),
            updatedLabel.widthAnchor.constraint(equalToConstant: 92),

            chevron.leadingAnchor.constraint(equalTo: updatedLabel.trailingAnchor, constant: 5),
            chevron.trailingAnchor.constraint(equalTo: headerButton.trailingAnchor, constant: -10),
            chevron.centerYAnchor.constraint(equalTo: headerButton.centerYAnchor),
            chevron.widthAnchor.constraint(equalToConstant: 13),
            chevron.heightAnchor.constraint(equalToConstant: 16)
        ])
    }

    private func rebuildDetails() {
        details.arrangedSubviews.forEach { view in
            details.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        details.addArrangedSubview(tableRow(["角色", "魔幣", "綁晶", "非綁晶", "武器"], header: true))
        for character in 1...5 {
            let item = readings[character]
            let weapon: String
            if character == 1 {
                weapon = "不檢測"
            } else if item?.weaponEmpty == false {
                weapon = "有裝備"
            } else if item?.weaponEmpty == true {
                weapon = "空缺"
            } else {
                weapon = "未知"
            }
            details.addArrangedSubview(
                tableRow(
                    [
                        String(character),
                        Self.amount(item?.coins, truncateThousands: true),
                        Self.amount(item?.boundCrystals),
                        Self.amount(item?.unboundCrystals),
                        weapon
                    ],
                    character: character,
                    item: item
                )
            )
        }
    }

    private func tableRow(
        _ values: [String],
        header: Bool = false,
        character: Int? = nil,
        item: GUIAssetInventoryItem? = nil
    ) -> UIView {
        let labels = values.enumerated().map { index, value -> UILabel in
            let label = UILabel()
            label.text = value
            label.textAlignment = .center
            label.font = header
                ? .systemFont(ofSize: 9, weight: .bold)
                : .monospacedDigitSystemFont(ofSize: 10, weight: .semibold)
            label.textColor = header ? UIColor.white.withAlphaComponent(0.58) : .white
            if !header, value == "--" || value == "未知" {
                label.textColor = UIColor(red: 1, green: 0.42, blue: 0.38, alpha: 1)
            }
            if !header, index == 4, character != 1 {
                label.textColor = item?.weaponEmpty == false
                    ? UIColor(red: 0.35, green: 0.94, blue: 0.63, alpha: 1)
                    : UIColor(red: 1, green: 0.42, blue: 0.38, alpha: 1)
            }
            if !header, index == 4, character == 1 {
                label.textColor = UIColor.white.withAlphaComponent(0.52)
            }
            label.heightAnchor.constraint(equalToConstant: header ? 25 : 29).isActive = true
            return label
        }
        let row = UIStackView(arrangedSubviews: labels)
        row.axis = .horizontal
        row.distribution = .fillEqually
        row.backgroundColor = header
            ? UIColor.white.withAlphaComponent(0.07)
            : UIColor.white.withAlphaComponent(0.025)
        return row
    }

    private func summaryText(
        _ label: String,
        keyPath: KeyPath<GUIAssetInventoryItem, Int64?>,
        truncateThousands: Bool = false
    ) -> String {
        var total: Int64 = 0
        var count = 0
        for character in 1...5 {
            guard let value = readings[character]?[keyPath: keyPath] else { continue }
            let displayedValue = truncateThousands ? (value / 1_000) * 1_000 : value
            let result = total.addingReportingOverflow(displayedValue)
            guard !result.overflow else { return "\(label) --" }
            total = result.partialValue
            count += 1
        }
        guard count > 0 else { return "\(label) --（0/5）" }
        let coverage = count == 5 ? "" : "（\(count)/5）"
        return "\(label) \(Self.amount(total))\(coverage)"
    }

    private func newestTimestamp() -> String? {
        readings.values.compactMap { $0.scanCapturedAt ?? $0.capturedAt }.max()
    }

    private static func amount(_ value: Int64?, truncateThousands: Bool = false) -> String {
        guard let value else { return "--" }
        let displayedValue = truncateThousands ? (value / 1_000) * 1_000 : value
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 0
        return formatter.string(from: NSNumber(value: displayedValue)) ?? String(displayedValue)
    }

    private static func displayTimestamp(_ value: String) -> String {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let standard = ISO8601DateFormatter()
        guard let date = fractional.date(from: value) ?? standard.date(from: value) else {
            return value
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_Hant_TW")
        formatter.timeZone = TimeZone(identifier: "Asia/Taipei")
        formatter.dateFormat = "MM/dd HH:mm"
        return formatter.string(from: date)
    }

    @objc private func toggleTapped() {
        onToggle?(slot)
    }
}
