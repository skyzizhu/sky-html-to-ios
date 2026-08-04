"""Canonical page-visible UIKit control catalog used by coverage auditing."""

SYSTEM_CONTROLS = [
    {"uikit": "UIButton", "swiftUI": "Button", "semantic": "button", "discovery": "html-or-aria"},
    {"uikit": "UITextField", "swiftUI": "TextField", "semantic": "text-input", "discovery": "html-or-aria"},
    {"uikit": "UITextView", "swiftUI": "TextEditor", "semantic": "text-area", "discovery": "html-or-aria"},
    {"uikit": "UISearchTextField", "swiftUI": "TextField", "semantic": "search-input", "discovery": "html-or-aria"},
    {"uikit": "UISearchBar", "swiftUI": "UIViewRepresentable", "semantic": "search-bar", "discovery": "explicit-or-structural"},
    {"uikit": "UISwitch", "swiftUI": "Toggle", "semantic": "switch", "discovery": "aria-or-explicit"},
    {"uikit": "UISlider", "swiftUI": "Slider", "semantic": "slider", "discovery": "html-or-aria"},
    {"uikit": "UIStepper", "swiftUI": "Stepper", "semantic": "stepper", "discovery": "explicit-or-composite"},
    {"uikit": "UISegmentedControl", "swiftUI": "Picker", "semantic": "segmented-control", "discovery": "explicit-or-structural"},
    {"uikit": "UIDatePicker", "swiftUI": "DatePicker", "semantic": "date-input", "discovery": "html-or-explicit"},
    {"uikit": "UIPickerView", "swiftUI": "Picker", "semantic": "wheel-picker", "discovery": "explicit-or-expanded-select"},
    {"uikit": "UIColorWell", "swiftUI": "ColorPicker", "semantic": "color-picker", "discovery": "html-or-explicit"},
    {"uikit": "UIPageControl", "swiftUI": "UIViewRepresentable", "semantic": "page-control", "discovery": "explicit-or-structural"},
    {"uikit": "UIProgressView", "swiftUI": "ProgressView", "semantic": "progress", "discovery": "html-or-aria"},
    {"uikit": "UIActivityIndicatorView", "swiftUI": "ProgressView", "semantic": "activity-indicator", "discovery": "aria-or-structural"},
    {"uikit": "UIPasteControl", "swiftUI": "PasteButton", "semantic": "paste-control", "discovery": "explicit"},
    {"uikit": "UIRefreshControl", "swiftUI": ".refreshable", "semantic": "refresh-control", "discovery": "explicit-or-runtime-behavior"},
    {"uikit": "UICalendarView", "swiftUI": "UIViewRepresentable", "semantic": "calendar-view", "discovery": "explicit"},
]

