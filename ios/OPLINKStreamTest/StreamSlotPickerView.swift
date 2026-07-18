import UIKit

final class StreamSlotPickerView: UIVisualEffectView {
    var onSelectSlot: ((Int) -> Void)?

    private let scrollView = UIScrollView()
    private let stack = UIStackView()
    private var buttons: [UIButton] = []
    private var selectedSlot = 1
    private var availableSlots = Set(1...15)

    override init(effect: UIVisualEffect?) {
        super.init(effect: effect ?? UIBlurEffect(style: .systemThinMaterialDark))
        buildLayout()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func apply(selectedSlot: Int, availableSlots: Set<Int>) {
        self.selectedSlot = selectedSlot
        self.availableSlots = availableSlots
        refreshButtons()
    }

    private func buildLayout() {
        alpha = 0.82
        layer.cornerRadius = 14
        layer.masksToBounds = true

        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.showsVerticalScrollIndicator = false
        scrollView.alwaysBounceVertical = true
        contentView.addSubview(scrollView)

        stack.axis = .vertical
        stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false
        scrollView.addSubview(stack)

        NSLayoutConstraint.activate([
            scrollView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            scrollView.topAnchor.constraint(equalTo: contentView.topAnchor),
            scrollView.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor, constant: 10),
            stack.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor, constant: -10),
            stack.widthAnchor.constraint(equalTo: scrollView.frameLayoutGuide.widthAnchor, constant: -20)
        ])

        for slot in 1...15 {
            let button = UIButton(type: .system)
            button.tag = slot
            button.setTitle(String(format: "%02d", slot), for: .normal)
            button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 15, weight: .bold)
            button.tintColor = .white
            button.layer.cornerRadius = 6
            button.layer.masksToBounds = true
            button.accessibilityLabel = "Game slot \(slot)"
            button.addTarget(self, action: #selector(slotTapped(_:)), for: .touchUpInside)
            button.heightAnchor.constraint(equalToConstant: 34).isActive = true
            buttons.append(button)
            stack.addArrangedSubview(button)
        }
        refreshButtons()
    }

    private func refreshButtons() {
        for button in buttons {
            let slot = button.tag
            let available = availableSlots.contains(slot)
            button.isEnabled = available
            button.backgroundColor = selectedSlot == slot
                ? UIColor.systemGreen.withAlphaComponent(0.45)
                : UIColor.white.withAlphaComponent(available ? 0.12 : 0.05)
            button.setTitleColor(
                available ? .white : UIColor.white.withAlphaComponent(0.35),
                for: .normal
            )
        }
    }

    @objc private func slotTapped(_ sender: UIButton) {
        guard availableSlots.contains(sender.tag) else { return }
        selectedSlot = sender.tag
        refreshButtons()
        onSelectSlot?(sender.tag)
    }
}
