"""Design tokens and global stylesheet for Aria."""

BG = "#07080C"
BG_SOFT = "#0B0D13"
PANEL = "rgba(255,255,255,0.045)"
PANEL_HOVER = "rgba(255,255,255,0.075)"
BORDER = "rgba(255,255,255,0.085)"
TEXT = "#E9EAF2"
TEXT_MUTED = "#8A8FA3"
TEXT_DIM = "#5C6172"
ACCENT = "#6E8BFF"
ACCENT_2 = "#A071FF"
DANGER = "#FF6B81"
OK = "#5AD8A6"

FONT_STACK = '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif'

QSS = f"""
* {{
    font-family: {FONT_STACK};
    outline: none;
}}
QWidget#Root {{
    background: {BG};
}}
QWidget {{
    color: {TEXT};
}}

/* ---------- title bar ---------- */
QFrame#TitleBar {{
    background: rgba(255,255,255,0.018);
    border-bottom: 1px solid rgba(255,255,255,0.05);
}}
QLabel#Brand {{
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #FFFFFF;
}}
QLabel#BrandSub {{
    font-size: 11px;
    color: {TEXT_DIM};
    letter-spacing: 1px;
}}
QPushButton#WinBtn {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px;
}}
QPushButton#WinBtn:hover {{
    background: rgba(255,255,255,0.08);
}}
QPushButton#WinBtnClose:hover {{
    background: #E81123;
}}
QLineEdit#TopSearch {{
    background: rgba(255,255,255,0.055);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 17px;
    padding: 7px 16px 7px 38px;
    font-size: 13px;
    color: {TEXT};
    selection-background-color: rgba(110,139,255,0.35);
}}
QLineEdit#TopSearch:focus {{
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(110,139,255,0.55);
}}

/* ---------- sidebar ---------- */
QFrame#Sidebar {{
    background: rgba(255,255,255,0.022);
    border-right: 1px solid rgba(255,255,255,0.05);
}}
QPushButton#NavBtn {{
    background: transparent;
    border: none;
    border-radius: 12px;
    padding: 11px 14px;
    text-align: left;
    font-size: 14px;
    color: {TEXT_MUTED};
}}
QPushButton#NavBtn:hover {{
    background: rgba(255,255,255,0.05);
    color: {TEXT};
}}
QPushButton#NavBtn:checked {{
    background: rgba(110,139,255,0.13);
    color: #FFFFFF;
}}
QLabel#NavHint {{
    color: {TEXT_DIM};
    font-size: 11px;
}}
QFrame#SourceMeter {{
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
}}
QLabel#MeterValue {{
    font-size: 20px;
    font-weight: 700;
    color: #FFFFFF;
}}
QLabel#MeterLabel {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QPushButton#SideAction {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 9px;
    color: {TEXT};
    font-size: 12px;
    text-align: left;
    padding: 7px 10px;
}}
QPushButton#SideAction:hover {{
    background: rgba(110,139,255,0.18);
    border-color: rgba(110,139,255,0.45);
}}

/* ---------- generic ---------- */
QLabel#PageTitle {{
    font-size: 26px;
    font-weight: 700;
    color: #FFFFFF;
}}
QLabel#PageSub {{
    font-size: 13px;
    color: {TEXT_MUTED};
}}
QLabel#SectionTitle {{
    font-size: 15px;
    font-weight: 600;
    color: #FFFFFF;
}}
QLabel#Muted {{
    color: {TEXT_MUTED};
}}
QFrame#Card {{
    background: {PANEL};
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 18px;
}}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5F86FF, stop:1 #9B63FF);
    border: none;
    border-radius: 18px;
    padding: 9px 20px;
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7295FF, stop:1 #AC79FF);
}}
QPushButton#Ghost {{
    background: rgba(255,255,255,0.055);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 18px;
    padding: 9px 18px;
    color: {TEXT};
    font-size: 13px;
}}
QPushButton#Ghost:hover {{
    background: rgba(255,255,255,0.09);
    border: 1px solid rgba(110,139,255,0.4);
}}
QPushButton#Ghost:disabled {{
    color: {TEXT_DIM};
}}
QPushButton#LinkBtn {{
    background: transparent;
    border: none;
    color: {TEXT_MUTED};
    font-size: 12px;
    padding: 4px 6px;
}}
QPushButton#LinkBtn:hover {{
    color: #FFFFFF;
}}
QPushButton#Chip {{
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px;
    padding: 6px 14px;
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QPushButton#Chip:hover {{
    color: {TEXT};
    background: rgba(255,255,255,0.085);
}}
QPushButton#Chip:checked {{
    background: rgba(110,139,255,0.16);
    border: 1px solid rgba(110,139,255,0.55);
    color: #FFFFFF;
}}
QPushButton#IconBtn {{
    background: transparent;
    border: none;
    border-radius: 10px;
}}
QPushButton#IconBtn:hover {{
    background: rgba(255,255,255,0.08);
}}
QPushButton#FavBtn {{
    background: rgba(255,255,255,0.90);
    border: none;
    border-radius: 13px;
}}
QPushButton#FavBtn:hover {{
    background: #FFFFFF;
}}
QPushButton#FavBtn[fav="1"] {{
    background: rgba(255,77,109,0.16);
}}
QPushButton#FavBtn[fav="1"]:hover {{
    background: rgba(255,77,109,0.26);
}}
QPushButton#PlayCircle {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5F86FF, stop:1 #9B63FF);
    border: none;
    border-radius: 25px;
}}
QPushButton#PlayCircle:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7A9AFF, stop:1 #B181FF);
}}

/* ---------- search ---------- */
QLineEdit#BigSearch {{
    background: rgba(255,255,255,0.055);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 22px;
    padding: 13px 20px 13px 46px;
    font-size: 15px;
    color: {TEXT};
    selection-background-color: rgba(110,139,255,0.35);
}}
QLineEdit#BigSearch:focus {{
    border: 1px solid rgba(110,139,255,0.6);
    background: rgba(255,255,255,0.08);
}}
QFrame#ResultRow {{
    background: transparent;
    border: none;
    border-radius: 14px;
}}
QFrame#ResultRow:hover {{
    background: rgba(255,255,255,0.05);
}}
QFrame#PlaylistCard {{
    background: transparent;
    border: none;
    border-radius: 12px;
}}
QFrame#PlaylistCard:hover {{
    background: rgba(255,255,255,0.05);
}}
QFrame#PlaylistCard[selected="1"] {{
    background: rgba(110,139,255,0.16);
    border: 1px solid rgba(110,139,255,0.40);
}}
QFrame#PlaylistTile {{
    background: rgba(255,255,255,0.045);
    border: 1px solid rgba(255,255,255,0.085);
    border-radius: 18px;
}}
QFrame#PlaylistTile:hover {{
    background: rgba(255,255,255,0.085);
    border: 1px solid rgba(110,139,255,0.42);
}}
QLabel#TrackTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#TrackMeta {{
    font-size: 12px;
    color: {TEXT_MUTED};
}}
QLabel#SourceBadge {{
    background: rgba(110,139,255,0.14);
    border-radius: 9px;
    padding: 3px 9px;
    color: #A9BCFF;
    font-size: 11px;
}}
QLabel#IndexBadge {{
    color: {TEXT_DIM};
    font-size: 12px;
}}

/* ---------- player ---------- */
QFrame#PlayerBar {{
    background: rgba(255,255,255,0.035);
    border-top: 1px solid rgba(255,255,255,0.07);
}}
QLabel#NowTitle {{
    font-size: 14px;
    font-weight: 600;
    color: #FFFFFF;
}}
QLabel#NowMeta {{
    font-size: 12px;
    color: {TEXT_MUTED};
}}
QSlider#Seek::groove:horizontal {{
    height: 4px;
    background: rgba(255,255,255,0.1);
    border-radius: 2px;
}}
QSlider#Seek::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5F86FF, stop:1 #A071FF);
    border-radius: 2px;
}}
QSlider#Seek::handle:horizontal {{
    width: 11px;
    height: 11px;
    margin: -4px 0;
    border-radius: 5px;
    background: #FFFFFF;
}}
QSlider#Volume::groove:horizontal {{
    height: 3px;
    background: rgba(255,255,255,0.12);
    border-radius: 2px;
}}
QSlider#Volume::sub-page:horizontal {{
    background: rgba(255,255,255,0.6);
    border-radius: 2px;
}}
QSlider#Volume::handle:horizontal {{
    width: 9px; height: 9px; margin: -3px 0;
    border-radius: 4px; background: #FFFFFF;
}}
QLabel#TimeLabel {{
    color: {TEXT_DIM};
    font-size: 11px;
}}

/* ---------- scroll ---------- */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.14);
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(255,255,255,0.26);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    height: 0px;
}}

/* ---------- dialogs ---------- */
QFrame#QueuePopup {{
    background: {PANEL};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 16px;
}}
QMenu {{
    background: #171923;
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 18px;
    border-radius: 7px;
}}
QMenu::item:selected {{
    background: rgba(110,139,255,0.20);
    color: #FFFFFF;
}}
QMenu::separator {{
    height: 1px;
    background: rgba(255,255,255,0.07);
    margin: 5px 8px;
}}
QDialog {{
    background: {BG_SOFT};
}}
QPlainTextEdit#BatchInput {{
    background: rgba(255,255,255,0.045);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 10px;
    color: {TEXT};
    font-family: "Consolas", "Microsoft YaHei UI";
    font-size: 12px;
}}
QTableWidget {{
    background-color: #0E1016;
    border: none;
    gridline-color: rgba(255,255,255,0.05);
    color: {TEXT};
    selection-background-color: rgba(110,139,255,0.16);
}}
QTableView {{
    background-color: #0E1016;
    alternate-background-color: #12141B;
}}
QTableWidget::item {{
    background: transparent;
    padding: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}}
QTableWidget::item:selected {{
    background: rgba(110,139,255,0.16);
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    background: #0E1016;
    top: -1px;
}}
QTabBar::tab {{
    background: rgba(255,255,255,0.05);
    color: {TEXT_MUTED};
    padding: 7px 18px;
    margin-right: 6px;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    background: rgba(110,139,255,0.22);
    color: #FFFFFF;
}}
QTabBar::tab:hover {{
    color: #FFFFFF;
}}
QHeaderView::section {{
    background: transparent;
    color: {TEXT_MUTED};
    border: none;
    padding: 6px;
    font-size: 12px;
}}
QProgressBar {{
    background: rgba(255,255,255,0.06);
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5F86FF, stop:1 #A071FF);
    border-radius: 3px;
}}
QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 10px;
    padding: 7px 12px;
    color: {TEXT};
    selection-background-color: rgba(110,139,255,0.35);
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid rgba(120,145,255,0.8);
    background: rgba(255,255,255,0.07);
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {TEXT_MUTED};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_MUTED};
}}
QComboBox {{
    background: rgba(255,255,255,0.055);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 10px;
    padding: 7px 12px;
    color: {TEXT};
}}
QComboBox QAbstractItemView {{
    background: #12141C;
    border: 1px solid rgba(255,255,255,0.1);
    selection-background-color: rgba(110,139,255,0.25);
}}
QCheckBox {{
    color: {TEXT_MUTED};
    font-size: 12px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 5px;
    border: 1px solid rgba(255,255,255,0.18);
    background: rgba(255,255,255,0.04);
}}
QCheckBox::indicator:checked {{
    background: #6E8BFF;
    border: 1px solid #6E8BFF;
}}
QToolTip {{
    background: #14161F;
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.12);
    padding: 6px 8px;
}}
"""
