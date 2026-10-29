"""Slice B — UIA raw-view traversal contracts on the Windows helper.

The live defect: the enumeration used the UIA CONTROL view
($win.FindAll(Descendants, TrueCondition)), which hides real text
surfaces — Win11 Notepad's RichEdit Document control never appears,
and neither does the ControlType.Edit composer in Electron/Chromium
apps (Hermes). The fix walks the RAW view instead.

The PowerShell helper cannot run on this host, so — following
test_browser_uia_wave2.py's pattern for the same script — these are
static contracts on the _UIA_PS string, extracted by importing the
windows read module directly (it imports fine on Linux; its Windows
dependencies are all lazy). The live-PC retest is named in the slice
report.
"""
import re

from seams.providers.windows import read as win_read

PS = win_read._UIA_PS


def test_helper_script_is_pure_ascii():
    # Written out as strict ASCII for PowerShell 5.1 by _uia_invoke.
    PS.encode("ascii")


def test_enumeration_uses_raw_view_walker():
    assert "[System.Windows.Automation.TreeWalker]::RawViewWalker" in PS
    assert "GetFirstChild" in PS
    assert "GetNextSibling" in PS
    # The managed TreeWalker API is GetFirstChild/GetNextSibling/
    # GetParent — the *Element-suffixed names exist only on the COM
    # IUIAutomationTreeWalker and throw on every call here (proven
    # live 2026-10-29: empty enumeration). Pin their absence.
    assert "GetFirstChildElement" not in PS
    assert "GetNextSiblingElement" not in PS
    assert "GetParentElement" not in PS
    # The walk starts at the target window and climbs within its
    # subtree when a branch is exhausted.
    assert "GetFirstChild($win)" in PS
    assert "GetParent" in PS


def test_no_findall_descendants_truecondition_enumeration():
    # The old control-view enumeration is gone. (Find-Window's
    # Children-scope FindAll is a different, untouched lookup.)
    assert "$win.FindAll" not in PS
    assert "TrueCondition" not in PS
    assert "TreeScope]::Descendants" not in PS


def test_walk_feeds_the_shared_enumeration_in_document_order():
    # The raw-view walk replaces the enumeration source only: it sits
    # between the window lookup and the same downstream foreach, so
    # the filters / ordering / caps below it are the pre-change ones.
    # Scope to the MAIN walk via its unique cap constant: the
    # 'focused' op (slice C) also names RawViewWalker for its
    # ancestor/escalation walk and sits earlier in the script.
    assert PS.index("Find-Window $req.window") < PS.index("$UIA_MAX_VISITED_NODES")
    assert PS.index("$UIA_MAX_VISITED_NODES") < PS.index("foreach ($el in $all)")
    # Matched-cap break over the collected list is unchanged.
    assert "if ($elems.Count -ge $limit) { break }" in PS
    # Name matching: slice B alone left the in-loop -notmatch filter
    # as-is; in the INTEGRATED tree slice A's tiered resolution
    # supersedes it (collection is name-unfiltered, selection happens
    # post-loop). This assertion tracks the integrated contract.
    assert "$n -notmatch $req.name_rx" not in PS


def test_legacy_edit_hwnd_text_fallback_present():
    # Hosts whose OS UIA proxy never engages for classic Win32
    # controls expose standard Edits as pattern-less Panes; the
    # helper's text channel for Edit/RichEdit hwnds is window
    # messages (WM_GETTEXT/WM_SETTEXT via SendMessageTimeout),
    # used only when no ValuePattern owns the element.
    assert "function El-HwndText" in PS
    assert "function El-HwndSet" in PS
    assert "SendMessageTimeout" in PS
    assert "0x000D" in PS  # WM_GETTEXT
    assert "0x000C" in PS  # WM_SETTEXT
    assert "'Edit'" in PS and "'RICHEDIT50W'" in PS
    assert "method='hwnd'" in PS


def test_bounding_rect_values_are_clamped_not_cast():
    # Raw-view-only elements can carry out-of-Int32-range rect
    # doubles; a bare [int] cast throws and kills the whole query
    # (proven live on Hermes). Collection must sanitize via Safe-Int.
    assert "function Safe-Int" in PS
    assert "x=[int]$r.X" not in PS
    assert "$bx = Safe-Int $r.X" in PS


def test_visited_node_cap_constant_exists_and_bounds_the_walk():
    m = re.search(r"\$UIA_MAX_VISITED_NODES\s*=\s*(\d+)", PS)
    assert m, "named visited-node cap constant missing"
    assert int(m.group(1)) == 20000
    # The cap actually guards the walk loop and the counter it reads
    # is the one the loop increments.
    assert "$visited -lt $UIA_MAX_VISITED_NODES" in PS
    assert "$visited += 1" in PS


def test_collected_element_fields_are_exactly_the_pre_change_set():
    # The collected pscustomobject in the enumeration loop: 'el' is
    # the internal element handle (downstream ops act through it);
    # the data fields are exactly the pre-change set, in order.
    start = PS.index("[pscustomobject]@{el=$el;")
    tail = "patterns=@(El-Patterns $el)}"
    segment = PS[start:PS.index(tail, start) + len(tail)]
    keys = re.findall(r"(?:^|[{;]\s*)([a-z_]+)=", segment)
    assert keys == ["el", "name", "control_type", "automation_id",
                    "x", "y", "width", "height", "value", "text",
                    "has_keyboard_focus", "runtime_id", "patterns"]
    assert set(keys) - {"el"} == {
        "name", "control_type", "automation_id", "x", "y", "width",
        "height", "value", "text", "has_keyboard_focus", "runtime_id",
        "patterns"}


def test_ops_find_click_get_set_blocks_still_present():
    for op in ("find", "click", "get", "set"):
        assert f"$op -eq '{op}'" in PS, op
