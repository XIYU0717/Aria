"""Vector icon factory + artwork helpers, drawn with QPainter (no external assets)."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)


def _pen(p, color, width):
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def draw_icon(p: QPainter, kind: str, color: str, s: float):
    """Draw a line icon inside a s x s box (logical coordinates)."""
    c = QColor(color)
    w = s * 0.085
    _pen(p, color, w)

    if kind in ("search",):
        p.drawEllipse(QPointF(s * 0.43, s * 0.43), s * 0.28, s * 0.28)
        p.drawLine(QPointF(s * 0.65, s * 0.65), QPointF(s * 0.86, s * 0.86))
    elif kind == "home":
        path = QPainterPath()
        path.moveTo(s * 0.16, s * 0.46)
        path.lineTo(s * 0.5, s * 0.16)
        path.lineTo(s * 0.84, s * 0.46)
        p.drawPath(path)
        p.drawPolyline([QPointF(s * 0.26, s * 0.42), QPointF(s * 0.26, s * 0.84),
                        QPointF(s * 0.74, s * 0.84), QPointF(s * 0.74, s * 0.42)])
    elif kind == "globe":
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.34, s * 0.34)
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.15, s * 0.34)
        p.drawLine(QPointF(s * 0.16, s * 0.5), QPointF(s * 0.84, s * 0.5))
        p.drawLine(QPointF(s * 0.22, s * 0.32), QPointF(s * 0.78, s * 0.32))
        p.drawLine(QPointF(s * 0.22, s * 0.68), QPointF(s * 0.78, s * 0.68))
    elif kind == "list":
        for i, y in enumerate((0.3, 0.5, 0.7)):
            p.drawEllipse(QPointF(s * 0.22, s * y), s * 0.045, s * 0.045)
            p.drawLine(QPointF(s * 0.36, s * y), QPointF(s * 0.82, s * y))
    elif kind == "gear":
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.13, s * 0.13)
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.3, s * 0.3)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(
                QPointF(s * 0.5 + s * 0.3 * math.cos(a), s * 0.5 + s * 0.3 * math.sin(a)),
                QPointF(s * 0.5 + s * 0.4 * math.cos(a), s * 0.5 + s * 0.4 * math.sin(a)),
            )
    elif kind == "play":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(s * 0.34, s * 0.22)
        path.lineTo(s * 0.8, s * 0.5)
        path.lineTo(s * 0.34, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "pause":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(s * 0.3, s * 0.24, s * 0.14, s * 0.52), s * 0.05, s * 0.05)
        p.drawRoundedRect(QRectF(s * 0.56, s * 0.24, s * 0.14, s * 0.52), s * 0.05, s * 0.05)
    elif kind in ("prev", "next"):
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        flip = 1 if kind == "next" else -1
        path = QPainterPath()
        if flip > 0:
            path.moveTo(s * 0.28, s * 0.24)
            path.lineTo(s * 0.66, s * 0.5)
            path.lineTo(s * 0.28, s * 0.76)
        else:
            path.moveTo(s * 0.72, s * 0.24)
            path.lineTo(s * 0.34, s * 0.5)
            path.lineTo(s * 0.72, s * 0.76)
        path.closeSubpath()
        p.drawPath(path)
        x = s * 0.76 if flip > 0 else s * 0.2
        p.setBrush(c)
        p.drawRoundedRect(QRectF(x, s * 0.24, s * 0.06, s * 0.52), s * 0.02, s * 0.02)
    elif kind in ("volume", "mute"):
        p.setBrush(c)
        path = QPainterPath()
        path.moveTo(s * 0.2, s * 0.4)
        path.lineTo(s * 0.32, s * 0.4)
        path.lineTo(s * 0.48, s * 0.26)
        path.lineTo(s * 0.48, s * 0.74)
        path.lineTo(s * 0.32, s * 0.6)
        path.lineTo(s * 0.2, s * 0.6)
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        _pen(p, color, w * 0.8)
        if kind == "volume":
            p.drawArc(QRectF(s * 0.36, s * 0.3, s * 0.34, s * 0.4), -60 * 16, 120 * 16)
            p.drawArc(QRectF(s * 0.36, s * 0.18, s * 0.5, s * 0.64), -55 * 16, 110 * 16)
        else:
            p.drawLine(QPointF(s * 0.66, s * 0.36), QPointF(s * 0.86, s * 0.64))
            p.drawLine(QPointF(s * 0.86, s * 0.36), QPointF(s * 0.66, s * 0.64))
    elif kind in ("close", "minimize", "maximize", "restore"):
        if kind == "close":
            p.drawLine(QPointF(s * 0.32, s * 0.32), QPointF(s * 0.68, s * 0.68))
            p.drawLine(QPointF(s * 0.68, s * 0.32), QPointF(s * 0.32, s * 0.68))
        elif kind == "minimize":
            p.drawLine(QPointF(s * 0.3, s * 0.58), QPointF(s * 0.7, s * 0.58))
        elif kind == "maximize":
            p.drawRoundedRect(QRectF(s * 0.3, s * 0.3, s * 0.4, s * 0.4), s * 0.03, s * 0.03)
        else:
            p.drawRoundedRect(QRectF(s * 0.24, s * 0.36, s * 0.4, s * 0.4), s * 0.03, s * 0.03)
            p.drawPolyline([QPointF(s * 0.4, s * 0.28), QPointF(s * 0.74, s * 0.28),
                            QPointF(s * 0.74, s * 0.62)])
    elif kind in ("chevron_left", "chevron_right", "chevron_down", "chevron_up"):
        if kind == "chevron_left":
            p.drawPolyline([QPointF(s * 0.58, s * 0.28), QPointF(s * 0.36, s * 0.5),
                            QPointF(s * 0.58, s * 0.72)])
        elif kind == "chevron_right":
            p.drawPolyline([QPointF(s * 0.42, s * 0.28), QPointF(s * 0.64, s * 0.5),
                            QPointF(s * 0.42, s * 0.72)])
        elif kind == "chevron_down":
            p.drawPolyline([QPointF(s * 0.28, s * 0.42), QPointF(s * 0.5, s * 0.64),
                            QPointF(s * 0.72, s * 0.42)])
        else:
            p.drawPolyline([QPointF(s * 0.28, s * 0.58), QPointF(s * 0.5, s * 0.36),
                            QPointF(s * 0.72, s * 0.58)])
    elif kind == "plus":
        p.drawLine(QPointF(s * 0.5, s * 0.22), QPointF(s * 0.5, s * 0.78))
        p.drawLine(QPointF(s * 0.22, s * 0.5), QPointF(s * 0.78, s * 0.5))
    elif kind == "share":
        # Three connected nodes: a compact share glyph that matches the line set.
        p.drawEllipse(QPointF(s * 0.24, s * 0.5), s * 0.08, s * 0.08)
        p.drawEllipse(QPointF(s * 0.76, s * 0.24), s * 0.08, s * 0.08)
        p.drawEllipse(QPointF(s * 0.76, s * 0.76), s * 0.08, s * 0.08)
        p.drawLine(QPointF(s * 0.32, s * 0.47), QPointF(s * 0.68, s * 0.29))
        p.drawLine(QPointF(s * 0.32, s * 0.53), QPointF(s * 0.68, s * 0.71))
    elif kind == "trash":
        p.drawPolyline([QPointF(s * 0.24, s * 0.3), QPointF(s * 0.76, s * 0.3)])
        p.drawPolyline([QPointF(s * 0.4, s * 0.3), QPointF(s * 0.4, s * 0.2),
                        QPointF(s * 0.6, s * 0.2), QPointF(s * 0.6, s * 0.3)])
        p.drawPolyline([QPointF(s * 0.32, s * 0.3), QPointF(s * 0.36, s * 0.8),
                        QPointF(s * 0.64, s * 0.8), QPointF(s * 0.68, s * 0.3)])
    elif kind == "refresh":
        p.drawArc(QRectF(s * 0.2, s * 0.2, s * 0.6, s * 0.6), 40 * 16, 280 * 16)
        p.drawPolyline([QPointF(s * 0.6, s * 0.14), QPointF(s * 0.8, s * 0.24),
                        QPointF(s * 0.66, s * 0.4)])
    elif kind in ("heart", "heart_filled"):
        path = QPainterPath()
        path.moveTo(s * 0.5, s * 0.78)
        path.cubicTo(s * 0.1, s * 0.55, s * 0.16, s * 0.2, s * 0.5, s * 0.36)
        path.cubicTo(s * 0.84, s * 0.2, s * 0.9, s * 0.55, s * 0.5, s * 0.78)
        if kind == "heart_filled":
            p.setBrush(c)
            p.setPen(Qt.NoPen)
        p.drawPath(path)
    elif kind == "note":
        p.drawLine(QPointF(s * 0.42, s * 0.7), QPointF(s * 0.42, s * 0.24))
        p.drawLine(QPointF(s * 0.42, s * 0.24), QPointF(s * 0.74, s * 0.32))
        p.drawLine(QPointF(s * 0.74, s * 0.32), QPointF(s * 0.74, s * 0.6))
        p.setBrush(c)
        p.drawEllipse(QPointF(s * 0.35, s * 0.72), s * 0.09, s * 0.07)
        p.drawEllipse(QPointF(s * 0.67, s * 0.62), s * 0.09, s * 0.07)
        p.setBrush(Qt.NoBrush)
    elif kind == "expand":
        p.drawPolyline([QPointF(s * 0.3, s * 0.44), QPointF(s * 0.3, s * 0.3), QPointF(s * 0.44, s * 0.3)])
        p.drawPolyline([QPointF(s * 0.56, s * 0.3), QPointF(s * 0.7, s * 0.3), QPointF(s * 0.7, s * 0.44)])
        p.drawPolyline([QPointF(s * 0.7, s * 0.56), QPointF(s * 0.7, s * 0.7), QPointF(s * 0.56, s * 0.7)])
        p.drawPolyline([QPointF(s * 0.44, s * 0.7), QPointF(s * 0.3, s * 0.7), QPointF(s * 0.3, s * 0.56)])
    elif kind == "collapse":
        p.drawPolyline([QPointF(s * 0.44, s * 0.3), QPointF(s * 0.44, s * 0.44), QPointF(s * 0.3, s * 0.44)])
        p.drawPolyline([QPointF(s * 0.56, s * 0.44), QPointF(s * 0.7, s * 0.44), QPointF(s * 0.7, s * 0.3)])
        p.drawPolyline([QPointF(s * 0.56, s * 0.7), QPointF(s * 0.56, s * 0.56), QPointF(s * 0.7, s * 0.56)])
        p.drawPolyline([QPointF(s * 0.44, s * 0.56), QPointF(s * 0.3, s * 0.56), QPointF(s * 0.3, s * 0.7)])
    elif kind == "check":
        p.drawPolyline([QPointF(s * 0.26, s * 0.52), QPointF(s * 0.44, s * 0.7),
                        QPointF(s * 0.76, s * 0.3)])
    elif kind == "sparkle":
        p.drawLine(QPointF(s * 0.5, s * 0.18), QPointF(s * 0.5, s * 0.82))
        p.drawLine(QPointF(s * 0.18, s * 0.5), QPointF(s * 0.82, s * 0.5))
        p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
        p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))
    elif kind == "edit":
        p.drawLine(QPointF(s * 0.26, s * 0.74), QPointF(s * 0.36, s * 0.46))
        p.drawPolyline([QPointF(s * 0.36, s * 0.46), QPointF(s * 0.66, s * 0.16),
                        QPointF(s * 0.84, s * 0.34), QPointF(s * 0.54, s * 0.64),
                        QPointF(s * 0.26, s * 0.74)])
    elif kind == "shuffle":
        p.drawPolyline([QPointF(s * 0.2, s * 0.3), QPointF(s * 0.4, s * 0.3),
                        QPointF(s * 0.74, s * 0.7), QPointF(s * 0.86, s * 0.7)])
        p.drawPolyline([QPointF(s * 0.2, s * 0.7), QPointF(s * 0.4, s * 0.7),
                        QPointF(s * 0.74, s * 0.3), QPointF(s * 0.86, s * 0.3)])
        p.drawPolyline([QPointF(s * 0.76, s * 0.22), QPointF(s * 0.88, s * 0.3), QPointF(s * 0.76, s * 0.38)])
    elif kind in ("repeat", "repeat_one"):
        p.drawPolyline([QPointF(s * 0.2, s * 0.42), QPointF(s * 0.2, s * 0.3),
                        QPointF(s * 0.7, s * 0.3), QPointF(s * 0.8, s * 0.4)])
        p.drawPolyline([QPointF(s * 0.8, s * 0.58), QPointF(s * 0.8, s * 0.7),
                        QPointF(s * 0.3, s * 0.7), QPointF(s * 0.2, s * 0.6)])
        if kind == "repeat_one":
            p.setFont(QFont("Segoe UI", max(1, int(s * 0.32)), QFont.Bold))
            p.drawText(QRectF(s * 0.38, s * 0.28, s * 0.24, s * 0.42),
                       Qt.AlignCenter, "1")
    elif kind == "dice":
        p.drawRoundedRect(QRectF(s * 0.2, s * 0.2, s * 0.6, s * 0.6), s * 0.1, s * 0.1)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        for x, y in ((0.35, 0.35), (0.65, 0.35), (0.5, 0.5), (0.35, 0.65), (0.65, 0.65)):
            p.drawEllipse(QPointF(s * x, s * y), s * 0.045, s * 0.045)
    elif kind == "music":
        p.drawLine(QPointF(s * 0.36, s * 0.7), QPointF(s * 0.36, s * 0.22))
        p.drawLine(QPointF(s * 0.36, s * 0.22), QPointF(s * 0.72, s * 0.3))
        p.setBrush(c)
        p.drawEllipse(QPointF(s * 0.28, s * 0.72), s * 0.1, s * 0.08)
        p.setBrush(Qt.NoBrush)
    else:  # dot fallback
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.3, s * 0.3)


def make_icon(kind: str, color: str = "#E9EAF2", size: int = 20) -> QIcon:
    icon = QIcon()
    for scale in (1, 2, 3):
        pm = QPixmap(int(size * scale), int(size * scale))
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        draw_icon(p, kind, color, float(size))
        p.end()
        icon.addPixmap(pm, QIcon.Normal)
    return icon


def two_state_icon(kind: str, normal: str, active: str, size: int = 20) -> QIcon:
    icon = QIcon()
    for state_color, mode, state in ((normal, QIcon.Normal, QIcon.Off), (active, QIcon.Normal, QIcon.On)):
        for scale in (1, 2, 3):
            pm = QPixmap(int(size * scale), int(size * scale))
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            draw_icon(p, kind, state_color, float(size))
            p.end()
            icon.addPixmap(pm, mode, state)
    return icon


def rounded(pix: QPixmap, size: int, radius: int) -> QPixmap:
    if pix.isNull():
        return pix
    scaled = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    p.drawPixmap(x, y, scaled)
    p.end()
    return out


def placeholder_art(w: int, h: int, seed: str, accent: str = "#6E8BFF") -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    g = QLinearGradient(0, 0, w, h)
    base = QColor(accent)
    g.setColorAt(0, QColor(base.red() // 3, base.green() // 3, base.blue() // 3 + 14))
    g.setColorAt(0.55, QColor(24, 26, 38))
    g.setColorAt(1, QColor(12, 13, 20))
    p.fillRect(0, 0, w, h, g)
    rg = QRadialGradient(w * 0.72, h * 0.22, max(w, h) * 0.8)
    rg.setColorAt(0, QColor(base.red(), base.green(), base.blue(), 85))
    rg.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(0, 0, w, h, rg)
    p.setPen(Qt.NoPen)
    for i in range(3):
        ring = QColor(255, 255, 255, 12 + i * 4)
        p.setBrush(ring)
        p.drawEllipse(QPointF(w * 0.18, h * 0.78), w * (0.3 + i * 0.16), w * (0.3 + i * 0.16))
    p.setPen(QColor(255, 255, 255, 46))
    font = QFont("Segoe UI", int(h * 0.42), QFont.Bold)
    p.setFont(font)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, seed[:1].upper())
    p.end()
    return pm

