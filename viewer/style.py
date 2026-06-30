"""Design tokens and the app-wide stylesheet — one source of truth.

Brutalist / minimal: one monospace face, hairline rules (no fills, shadows, or
rounded corners), monochrome chrome, and a single amber accent for interactive
cues. The layout geometry is the only saturated color in the window.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

# -- tokens (0-255 RGB) ------------------------------------------------------
CANVAS = (10, 10, 12)       # #0a0a0c   GL canvas + window background
INK = (232, 232, 234)       # #e8e8ea   primary text
MUTED = (110, 110, 118)     # secondary text
DIM = (70, 70, 78)          # disabled / hidden
HAIR = (42, 42, 48)         # 1px hairline rules
ACCENT = (255, 176, 0)      # #ffb000   amber — the single interactive accent

# Use Menlo (always present on macOS). Avoid naming "SF Mono" — Qt can't resolve it
# by family name there, which logs a warning and pays an alias-populate cost.
MONO = "Menlo, monospace"
MONO_FAMILY = "Menlo"       # concrete family for QFont

# GL clear color (0-1 floats), kept in sync with the canvas token.
CANVAS_GL = tuple(c / 255.0 for c in CANVAS)


def qcolor(rgb, a: int = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], a)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % rgb


def stylesheet() -> str:
    ink, muted, dim, hair, accent, canvas = (
        _hex(INK), _hex(MUTED), _hex(DIM), _hex(HAIR), _hex(ACCENT), _hex(CANVAS))
    return f"""
    QMainWindow, QWidget#panel, QMenuBar, QStatusBar {{
        background: {canvas};
    }}
    QWidget#panel {{ color: {ink}; font-family: {MONO}; font-size: 12px; }}
    QWidget#panel QLabel {{ color: {ink}; }}

    QListWidget {{
        background: {canvas}; border: none; outline: 0;
        font-family: {MONO}; font-size: 12px;
    }}
    QListWidget::item {{ padding: 5px 2px; border: none; }}
    QListWidget::item:selected {{ background: rgba(255,255,255,0.06); color: {ink}; }}

    QPushButton {{
        background: transparent; border: none; color: {muted};
        font-family: {MONO}; font-size: 12px; padding: 3px 4px; text-align: left;
    }}
    QPushButton:hover {{ color: {accent}; }}

    QCheckBox {{ color: {ink}; spacing: 11px; font-family: {MONO}; font-size: 15px; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border: 1px solid {muted}; background: transparent;
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {accent}; }}
    QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}

    QMenuBar {{ color: {muted}; font-family: {MONO}; }}
    QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
    QMenuBar::item:selected {{ background: rgba(255,255,255,0.08); color: {ink}; }}
    QMenu {{ background: {canvas}; color: {ink}; border: 1px solid {hair};
             font-family: {MONO}; font-size: 12px; }}
    QMenu::item:selected {{ background: rgba(255,255,255,0.08); }}

    QStatusBar {{ color: {muted}; border-top: 1px solid {hair};
                  font-family: {MONO}; font-size: 11px; }}
    QStatusBar::item {{ border: none; }}

    QSplitter::handle {{ background: {hair}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}

    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {hair}; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
