import AppKit
import SwiftUI

struct PetView: View {
    @EnvironmentObject private var settings: PetSettings
    @EnvironmentObject private var runtime: PetRuntime
    @EnvironmentObject private var systemMetrics: SystemMetricsMonitor
    @State private var message: String?
    @State private var tapCount = 0
    @State private var hostWindow: NSWindow?
    @State private var dragStartOrigin: CGPoint?
    @State private var dragStartMouseLocation: CGPoint?
    @State private var lastDragMouseLocation: CGPoint?
    @State private var dragDirection = DragDirection.none
    @State private var isDragging = false
    @State private var playStartedAt: TimeInterval?

    var body: some View {
        TimelineView(.animation(minimumInterval: animationFrameInterval)) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
                .truncatingRemainder(dividingBy: 240)
            let phase = time * settings.animationSpeed
            let playPhase = max(
                0,
                (timeline.date.timeIntervalSinceReferenceDate - (playStartedAt ?? timeline.date.timeIntervalSinceReferenceDate))
                    * settings.animationSpeed
            )

            ZStack(alignment: .top) {
                if settings.shieldEnabled {
                    ShieldView(style: settings.shieldStyle, phase: phase, color: settings.theme.color)
                        .frame(width: settings.size + 58, height: settings.size + 58)
                        .offset(y: 26)
                        .transition(.opacity)
                }

                GlassLightPool(phase: phase, brightness: settings.baseBrightness, color: settings.theme.color)
                    .frame(width: settings.size * 0.72, height: 46)
                    .offset(y: settings.size + 42)

                if isDragging && runtime.action != .play {
                    DragTrail(direction: dragDirection, phase: phase, color: actionAccent)
                        .frame(width: settings.size * 0.72, height: settings.size * 0.72)
                        .offset(y: 48)
                        .transition(.opacity)
                }

                if runtime.action == .hover && !isDragging {
                    FlightTrail(phase: phase, color: actionAccent)
                        .frame(width: settings.size * 0.82, height: settings.size * 0.72)
                        .offset(y: 52)
                        .transition(.opacity)
                }

                if runtime.action == .play && !isDragging {
                    PlaySparkles(phase: phase, color: actionAccent)
                        .frame(width: settings.size * 0.80, height: settings.size * 0.80)
                        .offset(y: 43)
                        .transition(.identity)
                        .animation(nil, value: runtime.action == .play)
                }

                if runtime.action == .sleep && !isDragging {
                    SleepIndicator(phase: phase, color: actionAccent)
                        .offset(x: settings.size * 0.43 + 42, y: 42)
                        .transition(.opacity)
                }

                robot(phase: phase, playPhase: playPhase)

                if let message {
                    Text(message)
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .padding(.horizontal, 13)
                        .padding(.vertical, 8)
                        .background(.ultraThinMaterial, in: Capsule())
                        .background(Color.black.opacity(0.20), in: Capsule())
                        .foregroundStyle(.white)
                        .overlay(Capsule().stroke(.white.opacity(0.38)))
                        .shadow(color: settings.theme.color.opacity(0.25), radius: 12)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                        .offset(y: settings.showSystemMonitor ? settings.size + 8 : 2)
                }
            }
            .offset(y: settings.showSystemMonitor && settings.metricsPosition == .top ? PetLayoutSpec.topMetricsPetOffset : 0)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .overlay {
                if settings.showSystemMonitor {
                    GeometryReader { geometry in
                        MetricsHUD(snapshot: systemMetrics.snapshot)
                            .fixedSize()
                            .opacity(settings.metricsContentOpacity)
                            .position(
                                PetLayoutSpec.metricsCenter(
                                    for: settings.metricsPosition,
                                    petSize: settings.size,
                                    containerSize: geometry.size
                                )
                            )
                            .transition(.opacity.combined(with: .scale(scale: 0.96)))
                            .animation(.easeInOut(duration: 0.55), value: settings.metricsPosition)
                    }
                }
            }
        }
        .frame(
            width: settings.size + PetLayoutSpec.panelExtraWidth,
            height: settings.size + PetLayoutSpec.panelExtraHeight
        )
        .background(WindowReader { hostWindow = $0 })
        .task(id: "\(settings.autoMood)-\(settings.moodInterval.rawValue)") { await moodLoop() }
        .task(id: "\(settings.showSystemMonitor)-\(settings.metricsRefreshInterval.rawValue)") {
            guard settings.showSystemMonitor else {
                systemMetrics.reset()
                return
            }
            await systemMetrics.run(every: settings.metricsRefreshInterval.rawValue)
        }
        .onChange(of: runtime.action) { newAction in
            playStartedAt = newAction == .play ? Date().timeIntervalSinceReferenceDate : nil
            show(newAction == .sleep ? "晚安，我会安静陪着你" : "慢慢进入\(newAction.title)状态")
        }
        .onChange(of: settings.mood) { newMood in
            show("现在是：\(newMood.title)")
        }
        .animation(.easeInOut(duration: 2.0), value: settings.shieldEnabled)
        .animation(.easeInOut(duration: 1.8), value: runtime.action)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("桌面伙伴伊娃，当前动作：\(runtime.action.title)，情绪：\(settings.mood.title)")
        .accessibilityAddTraits(.isButton)
    }

    private var animationFrameInterval: TimeInterval {
        // Keep calm states economical while reserving 30 FPS for visible movement
        // and direct manipulation, where additional frames are perceptible.
        if isDragging || runtime.action == .hover || runtime.action == .cheer || runtime.action == .play {
            return 1.0 / 30.0
        }
        return 1.0 / 20.0
    }

    private func robot(phase: Double, playPhase: Double) -> some View {
        let isRocketMode = runtime.action == .play
        let motion = isRocketMode
            ? motionValues(for: .play, phase: phase)
            : (isDragging ? dragMotionValues(phase: phase) : motionValues(for: runtime.action, phase: phase))
        return ZStack {
            // Keep a real (barely visible) hit-test surface behind every visual state.
            // A fully transparent SwiftUI subtree can be ignored by AppKit, which made
            // the rocket visible while mouse-down events passed through the panel.
            Rectangle()
                .fill(Color.black.opacity(PetInteractionSpec.hitLayerOpacity))
                .contentShape(Rectangle())

            if isRocketMode {
                PlayRocket(
                    phase: playPhase,
                    color: actionAccent,
                    isDragging: isDragging,
                    dragDirection: dragDirection
                )
                .frame(width: settings.size * 0.88, height: settings.size * 0.80)
                .transition(.identity)
            } else {
                ZStack {
                    Image(nsImage: RobotAsset.image(named: spriteName))
                        .resizable()
                        .scaledToFit()

                    Image(nsImage: RobotAsset.image(named: spriteName))
                        .resizable()
                        .scaledToFit()
                        .scaleEffect(x: -1, y: 1)
                        .mask(
                            Capsule()
                                .frame(width: settings.size * 0.095, height: settings.size * 0.20)
                                .offset(x: settings.size * 0.060, y: settings.size * 0.045)
                                .blur(radius: 1.5)
                        )

                    ActionEyes(action: isDragging ? .hover : runtime.action, color: eyeAccent)
                        .frame(width: settings.size * 0.36, height: settings.size * 0.10)
                        .offset(x: settings.size * 0.035, y: -settings.size * 0.238)
                        .animation(.easeInOut(duration: 1.35), value: runtime.action)

                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [.white.opacity(0.94), actionAccent.opacity(0.72), actionAccent.opacity(0.34)],
                                center: .center,
                                startRadius: 0,
                                endRadius: settings.size * 0.032
                            )
                        )
                        .frame(width: settings.size * 0.058, height: settings.size * 0.058)
                        .overlay(Circle().stroke(.white.opacity(0.72), lineWidth: 0.8))
                        .shadow(color: actionAccent.opacity(0.38), radius: isDragging ? 12 : 9)
                        .scaleEffect(1 + sin(phase * (isDragging ? 1.4 : 0.22)) * 0.065)
                        .offset(
                            x: settings.size * PetMotionSpec.chestCoreNormalizedX,
                            y: settings.size * PetMotionSpec.chestCoreNormalizedY
                        )
                        .animation(.easeInOut(duration: 1.8), value: runtime.action)
                }
                .transition(.identity)
            }
        }
            // Rocket and robot are mutually exclusive. Disabling the implicit
            // cross-fade prevents the baked source sprite flashing at both ends
            // of the play sequence.
            .animation(nil, value: isRocketMode)
            .frame(width: settings.size, height: settings.size)
            .contentShape(Rectangle())
            .opacity(settings.opacity)
            .scaleEffect(motion.scale)
            .rotationEffect(.degrees(motion.rotation))
            .offset(x: motion.x, y: motion.y + 34)
            .gesture(dragGesture)
            .onTapGesture { interact() }
            .contextMenu {
                ForEach(PetAction.allCases) { item in
                    Button { runtime.action = item } label: {
                        Label(item.title, systemImage: item.symbol)
                    }
                }
            }
            .help("点击和伊娃互动；拖动时会根据方向反馈动作与尾迹")
            .animation(.easeInOut(duration: 1.8), value: runtime.action)
            .animation(.spring(response: 0.42, dampingFraction: 0.72), value: isDragging)
    }

    private var spriteName: String {
        "eva-glass-v11"
    }

    private var actionAccent: Color {
        if isDragging { return Color(red: 0.46, green: 0.82, blue: 0.86) }
        switch runtime.action {
        case .idle: return Color(red: 0.44, green: 0.76, blue: 0.92)
        case .hover: return Color(red: 0.52, green: 0.66, blue: 0.91)
        case .cheer: return Color(red: 0.52, green: 0.86, blue: 0.70)
        case .play: return Color(red: 0.34, green: 0.78, blue: 0.98)
        case .sleep: return Color(red: 0.66, green: 0.62, blue: 0.86)
        }
    }

    private var eyeAccent: Color {
        // Eyes stay calm and recognizable across actions. Motion and shape carry
        // expression; only the chest core uses the per-action accent palette.
        Color(red: 0.30, green: 0.82, blue: 1.0)
    }

    private var dragGesture: some Gesture {
        DragGesture(minimumDistance: 1, coordinateSpace: .global)
            .onChanged { _ in
                guard let hostWindow else { return }
                if dragStartOrigin == nil {
                    dragStartOrigin = hostWindow.frame.origin
                    dragStartMouseLocation = NSEvent.mouseLocation
                    lastDragMouseLocation = NSEvent.mouseLocation
                    isDragging = true
                }
                guard let dragStartOrigin, let dragStartMouseLocation else { return }
                let mouseLocation = NSEvent.mouseLocation
                let delta = CGSize(
                    width: mouseLocation.x - dragStartMouseLocation.x,
                    height: mouseLocation.y - dragStartMouseLocation.y
                )

                // Keep the AppKit window locked to the pointer. The old smoothing
                // loop followed only a fraction of every delta and caused visible lag.
                hostWindow.setFrameOrigin(NSPoint(
                    x: dragStartOrigin.x + delta.width,
                    y: dragStartOrigin.y + delta.height
                ))
                let previousMouseLocation = lastDragMouseLocation ?? mouseLocation
                let nextDirection = DragDirection(translation: CGSize(
                    width: mouseLocation.x - previousMouseLocation.x,
                    height: previousMouseLocation.y - mouseLocation.y
                ))
                if nextDirection != .none && nextDirection != dragDirection {
                    dragDirection = nextDirection
                }
                lastDragMouseLocation = mouseLocation
            }
            .onEnded { _ in
                dragStartOrigin = nil
                dragStartMouseLocation = nil
                lastDragMouseLocation = nil
                withAnimation(.spring(response: 0.48, dampingFraction: 0.68)) {
                    isDragging = false
                }
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 520_000_000)
                    guard !isDragging else { return }
                    withAnimation(.easeOut(duration: 0.3)) { dragDirection = .none }
                }
            }
    }

    private func dragMotionValues(phase: Double) -> MotionValues {
        let tilt: Double
        switch dragDirection {
        case .left: tilt = -8
        case .right: tilt = 8
        case .up: tilt = -3
        case .down: tilt = 3
        case .none: tilt = 0
        }
        return MotionValues(
            x: sin(phase * 2.0) * 4.5,
            y: -10 - abs(sin(phase * 2.0)) * 8,
            rotation: tilt + sin(phase * 2.0) * 2.4,
            scale: 1.035 + abs(sin(phase * 2.0)) * 0.028
        )
    }

    private func motionValues(for action: PetAction, phase: Double) -> MotionValues {
        switch action {
        case .idle:
            return MotionValues(
                x: sin(phase * 0.13) * PetMotionSpec.idleHorizontalTravel,
                y: sin(phase * 0.20) * 8,
                rotation: sin(phase * 0.11) * 2.2,
                scale: 1 + sin(phase * 0.16) * 0.018
            )
        case .hover:
            return MotionValues(
                x: sin(phase * 0.16) * PetMotionSpec.hoverHorizontalTravel,
                y: -12 + sin(phase * 0.24) * 14,
                rotation: cos(phase * 0.16) * 6.5,
                scale: 1.025 + sin(phase * 0.18) * 0.022
            )
        case .cheer:
            return MotionValues(
                x: sin(phase * 0.34) * 8,
                y: -8 - abs(sin(phase * 0.43)) * 19,
                rotation: sin(phase * 0.34) * 7,
                scale: 1.035 + abs(sin(phase * 0.43)) * 0.032
            )
        case .play:
            return MotionValues(
                x: 0,
                y: -8,
                rotation: 0,
                scale: 1
            )
        case .sleep:
            return MotionValues(
                x: 0,
                y: 9 + sin(phase * 0.10) * 4,
                rotation: -3 + sin(phase * 0.08) * 1.2,
                scale: 0.97 + sin(phase * 0.09) * 0.012
            )
        }
    }

    @MainActor
    private func moodLoop() async {
        while settings.autoMood && !Task.isCancelled {
            let seconds = UInt64(settings.moodInterval.rawValue)
            try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
            guard settings.autoMood, !Task.isCancelled else { return }
            withAnimation(.easeInOut(duration: 1.25)) { settings.advanceMood() }
        }
    }

    private func interact() {
        tapCount += 1
        let selectedAction = PetAction.interactionCandidates(excluding: runtime.action).randomElement() ?? .cheer
        runtime.action = selectedAction
        let messages = messagesForCurrentMood
        show(messages[tapCount % messages.count])
        Task { @MainActor in
            let duration: UInt64 = selectedAction == .sleep ? 16 : 12
            try? await Task.sleep(nanoseconds: duration * 1_000_000_000)
            if runtime.action == selectedAction { runtime.action = .idle }
        }
    }

    private var messagesForCurrentMood: [String] {
        switch settings.mood {
        case .cheerful: ["今天也很棒呀", "你的好心情，我收到啦"]
        case .calm: ["慢一点也没关系", "陪你安静待一会儿"]
        case .tired: ["累了就伸个懒腰吧", "先喝口水，再继续"]
        case .frustrated: ["工作可以烦，别为难自己", "深呼吸，我陪着你"]
        case .blue: ["今天不开心也没关系", "不用马上振作，我在这里"]
        case .focused: ["专注模式，一起加油", "一步一步来就好"]
        }
    }

    private func show(_ text: String) {
        withAnimation(.easeInOut(duration: 0.65)) { message = text }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            if message == text {
                withAnimation(.easeOut(duration: 0.55)) { message = nil }
            }
        }
    }
}

private struct MetricsHUD: View {
    let snapshot: SystemMetricsSnapshot

    @EnvironmentObject private var settings: PetSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if settings.showCPUUsage { metric("CPU", value: percentage(snapshot.cpuUsage), symbol: "cpu") }
            if settings.showCPUTemperature {
                metric("CPU 温度", value: temperature(snapshot.cpuTemperature, fallback: snapshot.thermalState), symbol: "thermometer.medium")
            }
            if settings.showGPUUsage { metric("GPU", value: percentage(snapshot.gpuUsage), symbol: "display") }
            if settings.showGPUTemperature {
                metric("GPU 温度", value: temperature(snapshot.gpuTemperature, fallback: "系统限制"), symbol: "thermometer.medium")
            }
        }
        .font(.system(size: 10, weight: .semibold, design: settings.metricFontStyle.design))
        .monospacedDigit()
        .padding(.horizontal, 9)
        .padding(.vertical, 8)
        .background {
            if settings.metricsBackgroundOpacity > 0.001 {
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .fill(.ultraThinMaterial)
                    .opacity(min(1, settings.metricsBackgroundOpacity * 1.8))
                    .overlay(
                        RoundedRectangle(cornerRadius: 13, style: .continuous)
                            .fill(Color.black.opacity(settings.metricsBackgroundOpacity * 0.72))
                    )
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke(LinearGradient(colors: [.white.opacity(0.58), settings.theme.color.opacity(0.30)], startPoint: .topLeading, endPoint: .bottomTrailing))
                .opacity(settings.metricsBackgroundOpacity > 0.001 ? 1 : 0)
        )
        .shadow(color: settings.theme.color.opacity(settings.metricsBackgroundOpacity > 0.001 ? 0.08 : 0), radius: 4, y: 2)
        .foregroundStyle(settings.metricTextColor.color.opacity(0.94))
        .allowsHitTesting(false)
    }

    private func metric(_ label: String, value: String, symbol: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: symbol).frame(width: 11)
            Text(label)
            Spacer(minLength: 5)
            Text(value).fontWeight(.bold)
        }
        .frame(width: 112)
    }

    private func percentage(_ value: Double?) -> String {
        value.map { String(format: "%.0f%%", $0) } ?? "读取中"
    }

    private func temperature(_ value: Double?, fallback: String) -> String {
        value.map { String(format: "%.0f°C", $0) } ?? fallback
    }
}

private struct ActionEyes: View {
    let action: PetAction
    let color: Color

    var body: some View {
        ZStack {
            // The eye canvas is intentionally wider than the backdrop. This lets
            // the expression match the hero artwork while the flat source-eye
            // cover remains safely inset inside the original visor.
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .fill(Color(red: 0.006, green: 0.012, blue: 0.018).opacity(0.985))
                .scaleEffect(x: 0.95, y: 0.86)

            expression
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
        }
    }

    @ViewBuilder
    private var expression: some View {
        switch action {
        case .idle:
            ArcEyes(lift: 0.24, color: color, opacity: 1.0, lineWidth: 4.2)
        case .hover:
            FocusedEyes(color: color)
        case .cheer:
            ArcEyes(lift: 0.42, color: color, opacity: 1.0, lineWidth: 4.5)
        case .play:
            RoundPlayEyes(color: color)
        case .sleep:
            ArcEyes(lift: -0.08, color: color, opacity: 0.86, lineWidth: 3.2)
        }
    }
}

private struct ArcEyes: View {
    let lift: CGFloat
    let color: Color
    let opacity: Double
    let lineWidth: CGFloat

    var body: some View {
        EyeArcPair(lift: lift)
            .stroke(color.opacity(opacity), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
            .shadow(color: color.opacity(0.52), radius: 4)
    }
}

private struct EyeArcPair: Shape {
    let lift: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let baseline = rect.height * 0.64
        let eyeWidth = rect.width * 0.28
        let halfWidth = eyeWidth / 2
        let centers = [rect.width * 0.28, rect.width * 0.72]
        for centerX in centers {
            path.move(to: CGPoint(x: centerX - halfWidth, y: baseline))
            path.addQuadCurve(
                to: CGPoint(x: centerX + halfWidth, y: baseline),
                control: CGPoint(x: centerX, y: baseline - rect.height * lift)
            )
        }
        return path
    }
}

private struct FocusedEyes: View {
    let color: Color

    var body: some View {
        FocusedEyePair()
            .stroke(color, style: StrokeStyle(lineWidth: 4.2, lineCap: .round))
            .shadow(color: color.opacity(0.48), radius: 4)
    }
}

private struct FocusedEyePair: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.width * 0.14, y: rect.height * 0.52))
        path.addLine(to: CGPoint(x: rect.width * 0.42, y: rect.height * 0.43))
        path.move(to: CGPoint(x: rect.width * 0.58, y: rect.height * 0.43))
        path.addLine(to: CGPoint(x: rect.width * 0.86, y: rect.height * 0.52))
        return path
    }
}

private struct RoundPlayEyes: View {
    let color: Color

    var body: some View {
        HStack(spacing: 18) {
            eye
            eye
        }
    }

    private var eye: some View {
        Circle()
            .fill(
                RadialGradient(
                    colors: [.white, color.opacity(0.94), color.opacity(0.48)],
                    center: .topLeading,
                    startRadius: 0,
                    endRadius: 11
                )
            )
            .frame(width: 16, height: 20)
            .shadow(color: color.opacity(0.38), radius: 3.5)
    }
}

private struct MotionValues {
    let x: CGFloat
    let y: CGFloat
    let rotation: Double
    let scale: CGFloat
}

enum DragDirection: Equatable {
    case none, left, right, up, down

    init(translation: CGSize) {
        guard hypot(translation.width, translation.height) > 4 else {
            self = .none
            return
        }
        if abs(translation.width) > abs(translation.height) {
            self = translation.width < 0 ? .left : .right
        } else {
            self = translation.height < 0 ? .up : .down
        }
    }

    var unitVector: CGVector {
        switch self {
        case .none, .up: CGVector(dx: 0, dy: -1)
        case .down: CGVector(dx: 0, dy: 1)
        case .left: CGVector(dx: -1, dy: 0)
        case .right: CGVector(dx: 1, dy: 0)
        }
    }

    var rocketRotation: Double {
        switch self {
        case .none, .up: 0
        case .right: 90
        case .down: 180
        case .left: -90
        }
    }
}

private struct WindowReader: NSViewRepresentable {
    let onResolve: (NSWindow?) -> Void

    final class Coordinator {
        weak var window: NSWindow?
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            context.coordinator.window = view.window
            onResolve(view.window)
        }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        DispatchQueue.main.async {
            guard context.coordinator.window !== view.window else { return }
            context.coordinator.window = view.window
            onResolve(view.window)
        }
    }
}

private struct DragTrail: View {
    let direction: DragDirection
    let phase: Double
    let color: Color

    var body: some View {
        ZStack {
            ForEach(0..<3, id: \.self) { index in
                Capsule()
                    .fill(LinearGradient(colors: [.clear, color.opacity(0.55 - Double(index) * 0.12)], startPoint: .leading, endPoint: .trailing))
                    .frame(width: 54 - CGFloat(index) * 10, height: 2)
                    .offset(x: trailOffset * CGFloat(index + 1), y: CGFloat(index - 1) * 13)
                    .rotationEffect(trailRotation)
                    .opacity(0.55 + sin(phase * 3 + Double(index)) * 0.18)
            }
        }
        .allowsHitTesting(false)
    }

    private var trailOffset: CGFloat {
        switch direction {
        case .left: return 48
        case .right: return -48
        case .up, .down, .none: return -34
        }
    }

    private var trailRotation: Angle {
        switch direction {
        case .up: return .degrees(90)
        case .down: return .degrees(-90)
        default: return .zero
        }
    }
}

private struct FlightTrail: View {
    let phase: Double
    let color: Color

    var body: some View {
        let movingRight = cos(phase * 0.16) >= 0
        ZStack {
            ForEach(0..<3, id: \.self) { index in
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [color.opacity(0.05), color.opacity(0.32 - Double(index) * 0.07), .clear],
                            startPoint: movingRight ? .trailing : .leading,
                            endPoint: movingRight ? .leading : .trailing
                        )
                    )
                    .frame(width: 46 - CGFloat(index) * 8, height: 1.5)
                    .offset(
                        x: (movingRight ? -1 : 1) * (72 + CGFloat(index) * 18),
                        y: CGFloat(index - 1) * 16
                    )
                    .opacity(0.62 + sin(phase * 0.7 + Double(index)) * 0.16)
            }
        }
        .allowsHitTesting(false)
    }
}

private struct PlayRocket: View {
    let phase: Double
    let color: Color
    let isDragging: Bool
    let dragDirection: DragDirection

    var body: some View {
        GeometryReader { geometry in
            let size = min(geometry.size.width, geometry.size.height)
            let introduction = min(max(phase / 0.8, 0), 1)
            let easedIntroduction = introduction * introduction * (3 - 2 * introduction)
            let flightPhase = max(phase - 0.45, 0) * 0.72
            let horizontalTravel = isDragging
                ? CGFloat.zero
                : CGFloat(sin(flightPhase)) * geometry.size.width * 0.28 * easedIntroduction
            let verticalTravel = isDragging
                ? -geometry.size.height * 0.03
                : CGFloat(sin(flightPhase * 2)) * geometry.size.height * 0.17 * easedIntroduction
                    - geometry.size.height * 0.06 * easedIntroduction
            let horizontalVelocity = cos(flightPhase) * Double(geometry.size.width * 0.28)
            let verticalVelocity = cos(flightPhase * 2) * Double(geometry.size.height * 0.34)
            let automaticAngle = (atan2(verticalVelocity, horizontalVelocity) * 180 / .pi + 90) * easedIntroduction
            let flightAngle = isDragging ? dragDirection.rocketRotation : automaticAngle
            let direction = dragDirection.unitVector

            ZStack {
                if isDragging {
                    RocketDragWake(
                        phase: phase,
                        color: color,
                        direction: direction,
                        rotation: flightAngle,
                        rocketSize: size
                    )
                }

                RocketBody(phase: phase, color: color, isBoosted: isDragging)
                    .frame(width: size * 0.30, height: size * 0.54)
                    .rotationEffect(.degrees(flightAngle))
                    .shadow(color: color.opacity(isDragging ? 0.42 : 0.24), radius: isDragging ? 16 : 12)
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
            .offset(x: horizontalTravel, y: verticalTravel)
            .animation(.easeOut(duration: 0.16), value: dragDirection)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

private struct RocketDragWake: View {
    let phase: Double
    let color: Color
    let direction: CGVector
    let rotation: Double
    let rocketSize: CGFloat

    var body: some View {
        let perpendicular = CGVector(dx: -direction.dy, dy: direction.dx)

        ZStack {
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [.white.opacity(0.18), color.opacity(0.10), .clear],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: rocketSize * 0.19, height: rocketSize * 0.40)
                .rotationEffect(.degrees(rotation))
                .offset(
                    x: -direction.dx * rocketSize * 0.22,
                    y: -direction.dy * rocketSize * 0.22
                )
                .blur(radius: 8)

            ForEach(0..<3, id: \.self) { index in
                let distance = rocketSize * (0.28 + CGFloat(index) * 0.10)
                let lateral = (CGFloat(index) - 1) * rocketSize * 0.09
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [color.opacity(0.58 - Double(index) * 0.11), .clear],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .frame(
                        width: max(1.5, rocketSize * 0.014),
                        height: rocketSize * (0.19 + CGFloat(index) * 0.035)
                    )
                    .rotationEffect(.degrees(rotation))
                    .offset(
                        x: -direction.dx * distance + perpendicular.dx * lateral,
                        y: -direction.dy * distance + perpendicular.dy * lateral
                    )
                    .opacity(0.70 + sin(phase * 5 + Double(index)) * 0.16)
            }
        }
        .allowsHitTesting(false)
    }
}

private struct RocketBody: View {
    let phase: Double
    let color: Color
    let isBoosted: Bool

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height

            ZStack {
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [.white, .white.opacity(0.78), .white],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: width * 0.58, height: height * 0.78)
                    .overlay(
                        Capsule()
                            .stroke(.white.opacity(0.92), lineWidth: 1)
                    )

                RocketFin()
                    .fill(.white.opacity(0.92))
                    .frame(width: width * 0.29, height: height * 0.27)
                    .offset(x: -width * 0.34, y: height * 0.22)

                RocketFin()
                    .fill(.white.opacity(0.92))
                    .frame(width: width * 0.29, height: height * 0.27)
                    .scaleEffect(x: -1, y: 1)
                    .offset(x: width * 0.34, y: height * 0.22)

                Capsule()
                    .fill(.black)
                    .frame(width: width * 0.43, height: height * 0.20)
                    .overlay {
                        HStack(spacing: width * 0.08) {
                            Capsule().fill(color).frame(width: width * 0.09, height: height * 0.035)
                            Capsule().fill(color).frame(width: width * 0.09, height: height * 0.035)
                        }
                        .shadow(color: color.opacity(0.72), radius: 3)
                    }
                    .offset(y: -height * 0.14)

                Capsule()
                    .fill(color.opacity(0.82))
                    .frame(width: width * 0.48, height: max(2, height * 0.026))
                    .offset(y: height * 0.18)
                    .shadow(color: color.opacity(0.55), radius: 4)

                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [color.opacity(0.78), color.opacity(0.28), .clear],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .frame(
                        width: width * ((isBoosted ? 0.23 : 0.18) + abs(sin(phase * 5)) * 0.05),
                        height: height * ((isBoosted ? 0.42 : 0.25) + abs(sin(phase * 4)) * 0.05)
                    )
                    .offset(y: height * (isBoosted ? 0.56 : 0.48))
                    .blur(radius: isBoosted ? 2.2 : 1.4)
            }
            .frame(width: width, height: height)
        }
    }
}

private struct RocketFin: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addQuadCurve(
            to: CGPoint(x: rect.minX, y: rect.maxY),
            control: CGPoint(x: rect.minX, y: rect.midY)
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY * 0.80))
        path.closeSubpath()
        return path
    }
}

private struct PlaySparkles: View {
    let phase: Double
    let color: Color

    var body: some View {
        ZStack {
            ForEach(0..<4, id: \.self) { index in
                PlaySparkleParticle(index: index, phase: phase, color: color)
            }
        }
        .allowsHitTesting(false)
    }
}

private struct PlaySparkleParticle: View {
    let index: Int
    let phase: Double
    let color: Color

    var body: some View {
        Image(systemName: symbolName)
            .font(.system(size: symbolSize, weight: .medium))
            .foregroundStyle(symbolColor)
            .offset(x: horizontalOffset, y: verticalOffset)
            .scaleEffect(scale)
    }

    private var isSparkle: Bool { index.isMultiple(of: 2) }
    private var angle: Double { phase * 0.42 + Double(index) * .pi / 2 }
    private var symbolName: String { isSparkle ? "sparkle" : "circle.fill" }
    private var symbolSize: CGFloat { isSparkle ? 12 : 5 }
    private var symbolColor: Color { isSparkle ? .white.opacity(0.72) : color.opacity(0.62) }
    private var horizontalOffset: CGFloat {
        CGFloat(cos(angle)) * (76 + CGFloat(index % 2) * 10)
    }
    private var verticalOffset: CGFloat {
        CGFloat(sin(angle)) * (66 + CGFloat(index % 2) * 8)
    }
    private var scale: CGFloat {
        CGFloat(0.78 + sin(phase * 0.8 + Double(index)) * 0.18)
    }
}

private struct SleepIndicator: View {
    let phase: Double
    let color: Color

    var body: some View {
        HStack(alignment: .center, spacing: 7) {
            Image(systemName: "moon.zzz.fill")
                .font(.system(size: 23, weight: .semibold))

            HStack(alignment: .bottom, spacing: 1) {
                Text("z").font(.system(size: 13, weight: .bold, design: .rounded))
                Text("z").font(.system(size: 18, weight: .bold, design: .rounded))
                Text("z").font(.system(size: 25, weight: .bold, design: .rounded))
            }
        }
        .foregroundStyle(
            LinearGradient(
                colors: [.white.opacity(0.94), color.opacity(0.88)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .padding(.horizontal, 11)
        .padding(.vertical, 7)
        .background(.ultraThinMaterial, in: Capsule())
        .background(Color.black.opacity(0.20), in: Capsule())
        .overlay(Capsule().stroke(.white.opacity(0.52), lineWidth: 1))
        .shadow(color: color.opacity(0.48), radius: 12)
        .offset(y: sin(phase * 0.14) * 6)
        .scaleEffect(1 + sin(phase * 0.18) * 0.045)
        .opacity(0.82 + sin(phase * 0.18) * 0.12)
        .allowsHitTesting(false)
    }
}

private struct GlassLightPool: View {
    let phase: Double
    let brightness: Double
    let color: Color

    var body: some View {
        ZStack {
            Ellipse()
                .fill(
                    RadialGradient(
                        colors: [.white.opacity(brightness * 0.55), color.opacity(brightness * 0.48), color.opacity(brightness * 0.08), .clear],
                        center: .center,
                        startRadius: 0,
                        endRadius: 70
                    )
                )
                .blur(radius: 8)
                .scaleEffect(0.98 + sin(phase * 0.15) * 0.020)
            Ellipse()
                .fill(
                    LinearGradient(
                        colors: [.clear, .white.opacity(brightness * 0.20), color.opacity(brightness * 0.22), .clear],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(height: 5)
                .blur(radius: 3)
        }
        .opacity(0.18 + brightness * 0.42)
        .allowsHitTesting(false)
    }
}

private struct ShieldView: View {
    let style: ShieldStyle
    let phase: Double
    let color: Color

    var body: some View {
        switch style {
        case .halo:
            ZStack {
                Circle().stroke(color.opacity(0.10), lineWidth: 8).blur(radius: 5)
                Circle()
                    .trim(from: 0.03, to: 0.29)
                    .stroke(color.opacity(0.72), style: StrokeStyle(lineWidth: 1.6, lineCap: .round))
                    .rotationEffect(.degrees(phase * 4.2))
                Circle()
                    .trim(from: 0.54, to: 0.86)
                    .stroke(.white.opacity(0.42), style: StrokeStyle(lineWidth: 0.8, dash: [3, 8]))
                    .rotationEffect(.degrees(-phase * 2.6))
            }
            .scaleEffect(0.965 + sin(phase * 0.20) * 0.010)
        case .bubble:
            ZStack {
                Circle()
                    .stroke(AngularGradient(colors: [color.opacity(0.08), .white.opacity(0.62), color.opacity(0.45), .clear, color.opacity(0.12)], center: .center), lineWidth: 1.2)
                Circle()
                    .stroke(color.opacity(0.48), style: StrokeStyle(lineWidth: 1, dash: [1, 8]))
                    .padding(5)
                    .rotationEffect(.degrees(phase * 0.8))
                Circle()
                    .trim(from: 0.73, to: 0.79)
                    .stroke(.white.opacity(0.72), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .padding(5)
                    .rotationEffect(.degrees(phase * 2.2))
            }
                .scaleEffect(0.97 + sin(phase * 0.16) * 0.010)
        case .orbit:
            ZStack {
                Circle()
                    .trim(from: 0.02, to: 0.36)
                    .stroke(color.opacity(0.62), style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                    .rotationEffect(.degrees(phase * 5.0))
                Circle()
                    .trim(from: 0.49, to: 0.79)
                    .stroke(.white.opacity(0.34), style: StrokeStyle(lineWidth: 0.8, lineCap: .round, dash: [7, 5]))
                    .rotationEffect(.degrees(-phase * 3.1))
                Circle()
                    .trim(from: 0.88, to: 0.94)
                    .stroke(color.opacity(0.92), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(phase * 7.2))
            }
        }
    }
}
