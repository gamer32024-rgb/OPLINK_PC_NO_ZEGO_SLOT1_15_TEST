import UIKit

final class StreamSlotPickerView: UIView {
    var onSelectSlot: ((Int) -> Void)?
    var onClose: (() -> Void)?

    private let card = UIView()
    private var buttons: [UIButton] = []
    private var selectedSlot = 1
    private var availableSlots = Set(1...15)

    override init(frame: CGRect) {
        super.init(frame: frame)
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
        backgroundColor = UIColor.black.withAlphaComponent(0.62)

        card.translatesAutoresizingMaskIntoConstraints = false
        card.backgroundColor = UIColor(red: 0.045, green: 0.075, blue: 0.085, alpha: 0.98)
        card.layer.cornerRadius = 20
        card.layer.borderWidth = 1
        card.layer.borderColor = UIColor.white.withAlphaComponent(0.15).cgColor
        addSubview(card)

        let title = UILabel()
        title.text = "選擇遊戲串流"
        title.textColor = .white
        title.font = .systemFont(ofSize: 17, weight: .bold)

        let close = UIButton(type: .system)
        close.setImage(UIImage(systemName: "xmark"), for: .normal)
        close.tintColor = .white
        close.backgroundColor = UIColor.white.withAlphaComponent(0.1)
        close.layer.cornerRadius = 15
        close.addTarget(self, action: #selector(closeTapped), for: .touchUpInside)
        close.widthAnchor.constraint(equalToConstant: 30).isActive = true
        close.heightAnchor.constraint(equalToConstant: 30).isActive = true

        let header = UIStackView(arrangedSubviews: [title, UIView(), close])
        header.axis = .horizontal
        header.alignment = .center

        let grid = UIStackView()
        grid.axis = .vertical
        grid.spacing = 8
        grid.distribution = .fillEqually
        for rowIndex in 0..<3 {
            let row = UIStackView()
            row.axis = .horizontal
            row.spacing = 8
            row.distribution = .fillEqually
            for columnIndex in 0..<5 {
                let slot = rowIndex * 5 + columnIndex + 1
                let button = UIButton(type: .system)
                button.tag = slot
                button.setTitle(String(format: "%02d", slot), for: .normal)
                button.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 17, weight: .bold)
                button.layer.cornerRadius = 10
                button.addTarget(self, action: #selector(slotTapped(_:)), for: .touchUpInside)
                buttons.append(button)
                row.addArrangedSubview(button)
            }
            grid.addArrangedSubview(row)
        }

        let stack = UIStackView(arrangedSubviews: [header, grid])
        stack.axis = .vertical
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(stack)

        NSLayoutConstraint.activate([
            card.centerXAnchor.constraint(equalTo: centerXAnchor),
            card.centerYAnchor.constraint(equalTo: centerYAnchor),
            card.widthAnchor.constraint(equalToConstant: 410),
            card.heightAnchor.constraint(equalToConstant: 235),
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            stack.topAnchor.constraint(equalTo: card.topAnchor, constant: 14),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -16)
        ])
        refreshButtons()
    }

    private func refreshButtons() {
        for button in buttons {
            let slot = button.tag
            let available = availableSlots.contains(slot)
            let selected = selectedSlot == slot
            button.isEnabled = available
            button.backgroundColor = selected
                ? UIColor(red: 0.43, green: 0.88, blue: 0.5, alpha: 1)
                : UIColor(white: available ? 0.28 : 0.13, alpha: 0.95)
            button.setTitleColor(selected ? UIColor(red: 0.02, green: 0.13, blue: 0.06, alpha: 1) : .white, for: .normal)
            button.alpha = available ? 1 : 0.42
        }
    }

    @objc private func slotTapped(_ sender: UIButton) {
        guard availableSlots.contains(sender.tag) else { return }
        onSelectSlot?(sender.tag)
    }

    @objc private func closeTapped() {
        onClose?()
    }
}
