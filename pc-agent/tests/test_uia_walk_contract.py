"""Slice B — UIA enumeration contracts on the Windows helper.

History (two live findings, 2026-10-29):
1. The original diagnosis assumed the CONTROL view hid text
   surfaces; on the deployment box (Win10 classic Notepad) the
   editor child is a control-visible Pane in BOTH views and its
   text channel is the hwnd fallback (El-HwndText) — view choice
   was never the Notepad problem.
2. A hand-rolled RawViewWalker DFS was then tried and lost live
   on Hermes: Chromium's UIA tree is lazy, and walker navigation
   intermittently enumerated an empty/partial tree while a direct
   FindAll on the same window found every element.

The settled contract: enumeration is the provider-backed
$win.FindAll(Descendants, TrueCondition); RawViewWalker remains
only for the focused op's ANCESTOR walk (parents of a realized
element are always available).

The PowerShell helper cannot run on this host, so — following
test_browser_uia_wave2.py's pattern for the same script — these are
static contracts on the _UIA_PS string, extracted by importing the
windows read module directly (it imports fine on Linux; its Windows
dependencies are all lazy).
"""
import re

from seams.providers.windows import read as win_read

PS = win_read._UIA_PS


def test_helper_script_is_pure_ascii():
    # Written out as strict ASCII for PowerShell 5.1 by _uia_invoke.
    PS.encode("ascii")


def test_enumeration_is_provider_backed_findall():
    # The settled enumeration (see module docstring): FindAll over
    # the target window's descendants, TrueCondition.
    assert ("$all = $win.FindAll("
            "[System.Windows.Automation.TreeScope]::Descendants, "
            "[System.Windows.Automation.Condition]::TrueCondition)"
            ) in PS
    # The hand-rolled walker DFS is gone: no enumeration driven by
    # TreeWalker child/sibling navigation, and no visited-node cap
    # machinery remains for it.
    assert "$walker.GetFirstChild($win)" not in PS
    assert "$walker.GetNextSibling" not in PS
    assert "$UIA_MAX_VISITED_NODES" not in PS
    # The COM TreeWalker names (GetFirstChildElement etc.) throw on
    # the managed API (proven live 2026-10-29); pin their absence.
    assert "GetFirstChildElement" not in PS
    assert "GetNextSiblingElement" not in PS
    assert "GetParentElement" not in PS
    # RawViewWalker survives ONLY for the focused op's ancestor
    # walk (GetParent on a realized element).
    assert "[System.Windows.Automation.TreeWalker]::RawViewWalker" in PS
    assert "GetParent" in PS


def test_enumeration_feeds_the_shared_collection_in_document_order():
    # The FindAll enumeration sits between the window lookup and the
    # same downstream foreach, so the filters / ordering / caps
    # below it are the pre-change ones.
    assert PS.index("Find-Window $req.window") < PS.index("$all = $win.FindAll(")
    assert PS.index("$all = $win.FindAll(") < PS.index("foreach ($el in $all)")
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


def test_pattern_probes_are_deferred_until_selection():
    # Collection stores cheap fields only; the expensive pattern
    # probes run at collection time ONLY for a contains search.
    assert "$val = $null; $txt = $null" in PS
    assert "if ($contains -ne '') { $val = El-Value $el; $txt = El-Text $el }" in PS
    # The returned find set is probed before projection...
    assert ("foreach ($w in $winSet) { $w.value = El-Value $w.el; "
            "$w.has_keyboard_focus = El-Focus $w.el }") in PS
    # ...and so is the single resolved element before op dispatch.
    assert ("$t.value = El-Value $t.el; $t.text = El-Text $t.el; "
            "$t.has_keyboard_focus = El-Focus $t.el; "
            "$t.patterns = @(El-Patterns $t.el)") in PS


def test_no_visited_node_cap_machinery_remains():
    # The cap belonged to the retired hand-rolled DFS; the
    # provider-backed FindAll has no client-side walk to bound.
    assert "$UIA_MAX_VISITED_NODES" not in PS
    assert "$visited" not in PS


def test_collected_element_fields_are_exactly_the_pre_change_set():
    # The collected pscustomobject in the enumeration loop: 'el' is
    # the internal element handle (downstream ops act through it);
    # the data fields are exactly the pre-change set, in order.
    # (The patterns tail is the deferred-probe form: patterns are
    # probed at collection only for a contains search.)
    start = PS.index("[pscustomobject]@{el=$el;")
    tail = "else { @() })}"
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
