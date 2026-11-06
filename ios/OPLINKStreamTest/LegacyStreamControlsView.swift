import UIKit

final class LegacyStreamControlsView: UIVisualEffectView {
    var onPrevious: (() -> Void)?
    var onList: (() -> Void)?
    var onNext: (() -> Void)?
    var onSettings: (() -> Void)?
    var onExpandedChanged: ((Bool) -> Void)?

    private let toggleButton = UIButton(type: .system)
    private let previousButton = UIButton(type: .system)
    private let listButton = UIButton(type: .system)
    private let nextButton = UIButton(type: .system)
    private var widthConstraint: NSLayoutConstraint!
    private var heightConstraint: NSLayoutConstraint!

    private(set) var isExpanded = false

    init() {
        super.init(effect: UIBlurEffect(style: .systemUltraThinMaterialDark))
        buildLayout()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func setExpanded(_ expanded: Bool, animated: Bool) {
        guard expanded != isExpanded else { return }
        isExpanded = expanded
        toggleButton.isHidden = false
        previousButton.isHidden = true
        listButton.isHidden = true
        nextButton.isHidden = true
        widthConstraint.constant = 40
        heightConstraint.constant = 40
        layer.cornerRadius = 17
        onExpandedChanged?(expanded)

        let updates: () -> Void = {
            if let superview = self.superview {
                superview.layoutIfNeeded()
            }
        }
        if animated {
            UIView.animate(
                withDuration: 0.16,
                delay: 0,
                options: [.beginFromCurrentState, .curveEaseOut],
                animations: updates
            )
        } else {
            updates()
        }
    }

    func setCurrentSlot(_ slot: Int) {
        toggleButton.setImage(nil, for: .normal)
        toggleButton.setTitle(String(format: "%02d", slot), for: .normal)
        toggleButton.setTitleColor(UIColor(red: 0.46, green: 1.0, blue: 0.22, alpha: 1), for: .normal)
        toggleButton.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 17, weight: .heavy)
        toggleButton.accessibilityLabel = "Game list, current slot \(slot)"
        listButton.setImage(nil, for: .normal)
        listButton.setTitle(String(format: "%02d", slot), for: .normal)
        listButton.setTitleColor(
            UIColor(red: 0.46, green: 1.0, blue: 0.22, alpha: 1),
            for: .normal
        )
        listButton.titleLabel?.font = .monospacedDigitSystemFont(ofSize: 17, weight: .heavy)
        listButton.titleLabel?.layer.shadowColor = UIColor.systemGreen.cgColor
        listButton.titleLabel?.layer.shadowOpacity = 1
        listButton.titleLabel?.layer.shadowRadius = 3
        listButton.titleLabel?.layer.shadowOffset = .zero
        listButton.accessibilityLabel = "Game list, current slot \(slot)"
    }

    private func buildLayout() {
        translatesAutoresizingMaskIntoConstraints = false
        alpha = 0.78
        layer.cornerRadius = 17
        layer.masksToBounds = true

        configure(toggleButton, icon: "arrow.up.arrow.down", label: "Open game switch controls")
        configure(previousButton, icon: "chevron.up", label: "Previous game")
        configure(listButton, icon: "list.bullet", label: "Game list")
        configure(nextButton, icon: "chevron.down", label: "Next game")
        previousButton.imageView?.transform = CGAffineTransform(scaleX: 1, y: 1.1)
        nextButton.imageView?.transform = CGAffineTransform(scaleX: 1, y: 1.1)
        setCurrentSlot(1)

        toggleButton.addTarget(self, action: #selector(toggleTapped), for: .touchUpInside)
        previousButton.addTarget(self, action: #selector(previousTapped), for: .touchUpInside)
        listButton.addTarget(self, action: #selector(listTapped), for: .touchUpInside)
        nextButton.addTarget(self, action: #selector(nextTapped), for: .touchUpInside)

        let longPress = UILongPressGestureRecognizer(target: self, action: #selector(settingsLongPressed(_:)))
        addGestureRecognizer(longPress)

        let stack = UIStackView(arrangedSubviews: [toggleButton, previousButton, listButton, nextButton])
        stack.axis = .vertical
        stack.spacing = 0
        stack.alignment = .fill
        stack.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(stack)

        widthConstraint = widthAnchor.constraint(equalToConstant: 40)
        heightConstraint = heightAnchor.constraint(equalToConstant: 40)
        let buttonHeights = [
            toggleButton.heightAnchor.constraint(equalToConstant: 40),
            previousButton.heightAnchor.constraint(equalToConstant: 36),
            listButton.heightAnchor.constraint(equalToConstant: 40),
            nextButton.heightAnchor.constraint(equalToConstant: 36)
        ]
        buttonHeights.forEach { $0.priority = .defaultHigh }
        NSLayoutConstraint.activate([
            widthConstraint,
            heightConstraint,
            stack.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            stack.topAnchor.constraint(equalTo: contentView.topAnchor),
            stack.bottomAnchor.constraint(equalTo: contentView.bottomAnchor)
        ] + buttonHeights)
        previousButton.isHidden = true
        listButton.isHidden = true
        nextButton.isHidden = true
    }

    private func configure(_ button: UIButton, icon: String, label: String) {
        button.setImage(UIImage(systemName: icon), for: .normal)
        button.tintColor = .white
        button.backgroundColor = .clear
        button.accessibilityLabel = label
    }

    @objc private func toggleTapped() {
        onList?()
    }

    @objc private func previousTapped() {
        onPrevious?()
    }

    @objc private func listTapped() {
        onList?()
    }

    @objc private func nextTapped() {
        onNext?()
    }

    @objc private func settingsLongPressed(_ gesture: UILongPressGestureRecognizer) {
        guard gesture.state == .began else { return }
        onSettings?()
    }
}

final class FixedRightRailView: UIVisualEffectView {
    var onAssets: (() -> Void)?
    var onKeyboard: (() -> Void)?
    var onControlPanel: (() -> Void)?
    var onPrevious: (() -> Void)?
    var onNext: (() -> Void)?

    init() {
        super.init(effect: UIBlurEffect(style: .systemUltraThinMaterialDark))
        buildLayout()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func buildLayout() {
        translatesAutoresizingMaskIntoConstraints = false
        alpha = 0.72
        layer.cornerRadius = 21
        layer.maskedCorners = [.layerMinXMinYCorner, .layerMinXMaxYCorner]
        layer.masksToBounds = true

        let assets = iconButton("dollarsign.circle.fill", label: "最新角色資產")
        let keyboard = iconButton("keyboard", label: "Keyboard")
        let grid = iconButton("square.grid.2x2.fill", label: "GUI_TEST_PC")
        let previous = iconButton("chevron.up", label: "Previous game")
        let next = iconButton("chevron.down", label: "Next game")
        assets.addTarget(self, action: #selector(assetsTapped), for: .touchUpInside)
        keyboard.addTarget(self, action: #selector(keyboardTapped), for: .touchUpInside)
        grid.addTarget(self, action: #selector(controlPanelTapped), for: .touchUpInside)
        previous.addTarget(self, action: #selector(previousTapped), for: .touchUpInside)
        next.addTarget(self, action: #selector(nextTapped), for: .touchUpInside)

        func row(_ left: UIView, _ right: UIView) -> UIStackView {
            let result = UIStackView(arrangedSubviews: [left, right])
            result.axis = .horizontal
            result.spacing = 2
            result.distribution = .fillEqually
            return result
        }
        let stack = UIStackView(arrangedSubviews: [row(UIView(), assets), row(previous, keyboard), row(next, grid)])
        stack.axis = .vertical
        stack.spacing = 4
        stack.distribution = .fillEqually
        stack.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(stack)
        NSLayoutConstraint.activate([
            widthAnchor.constraint(equalToConstant: 80),
            heightAnchor.constraint(equalToConstant: 140),
            stack.leadingAnchor.constraint(equalTo: contentView.leadingAnchor, constant: 4),
            stack.trailingAnchor.constraint(equalTo: contentView.trailingAnchor, constant: -4),
            stack.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 5),
            stack.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -5)
        ])
    }

    private func iconButton(_ name: String, label: String) -> UIButton {
        let button = UIButton(type: .system)
        button.setImage(UIImage(systemName: name), for: .normal)
        button.tintColor = .white
        button.backgroundColor = UIColor.white.withAlphaComponent(0.08)
        button.layer.cornerRadius = 8
        button.accessibilityLabel = label
        return button
    }

    @objc private func assetsTapped() {
        onAssets?()
    }

    @objc private func keyboardTapped() {
        onKeyboard?()
    }

    @objc private func controlPanelTapped() {
        onControlPanel?()
    }

    @objc private func previousTapped() { onPrevious?() }
    @objc private func nextTapped() { onNext?() }
}

