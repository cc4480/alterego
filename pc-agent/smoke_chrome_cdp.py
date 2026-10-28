"""On-PC smoke for the Chrome CDP hand (chrome_cdp + cdp_* ops).

Run ON the PC (not part of the unit suites):

    cd pc-agent
    python smoke_chrome_cdp.py

Exercises the whole presentation route against a data: URL page —
no external network, no real profile, nothing typed anywhere real:

    launch (throwaway profile under the agent data area)
      -> navigate (load proven, readback returned)
      -> snapshot (input found by its generated selector)
      -> type by selector (value readback equality enforced)
      -> click the button by text (its handler retitles the page;
         the click's landing readback must show the change)
      -> readback (active element / ready_state)
      -> close GRACEFULLY with profile deletion

Prints SMOKE PASS lines per step; exit 0 only if every step passed.
First-light check after deploying to a PC: this script passing is
the CDP hand's live receipt, the same route the 2026-10-28 proof
ran in 3.3–5.3 s by hand.
"""
import os
import sys
import traceback
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chrome_cdp  # noqa: E402

_PAGE = """<!doctype html><title>CDP Smoke</title>
<input id="q" name="q" placeholder="smoke field">
<button id="go" onclick="document.title='Clicked proof';
  document.getElementById('out').textContent =
  'saw:' + document.getElementById('q').value">Go</button>
<div id="out"></div>"""


def _data_url(html):
    return "data:text/html;charset=utf-8," + urllib.parse.quote(html)


def main():
    profile = os.path.expandvars(
        rf"%LocalAppData%\AlterEgo\cdp-profiles\smoke-{os.getpid()}")
    step = "launch"
    try:
        r = chrome_cdp.launch(profile_dir=profile,
                              start_url=_data_url(_PAGE))
        assert r["pid"] > 0 and r["cdp_port"] > 0, r
        print(f"SMOKE PASS launch: pid={r['pid']} port={r['cdp_port']}")

        step = "navigate"
        rb = chrome_cdp.navigate(_data_url(_PAGE + "<p>second</p>"))
        assert rb["title"] == "CDP Smoke", rb
        print(f"SMOKE PASS navigate: {rb['url'][:40]}... "
              f"title={rb['title']!r}")

        step = "snapshot"
        snap = chrome_cdp.snapshot()
        selectors = [e["selector"] for e in snap["elements"]]
        assert "#q" in selectors, selectors
        print(f"SMOKE PASS snapshot: {len(selectors)} elements, "
              f"#q addressed as {selectors[selectors.index('#q')]}")

        step = "type"
        r = chrome_cdp.fill("proof", selector="#q")
        assert r["verified"] and r["value"] == "proof", r
        print(f"SMOKE PASS type: field holds {r['value']!r} (verified)")

        step = "click"
        r = chrome_cdp.click(text="Go")
        assert r["title"] == "Clicked proof", r
        print(f"SMOKE PASS click: landed title={r['title']!r} "
              f"changed={r['changed']}")

        step = "readback"
        rb = chrome_cdp.readback()
        assert rb["ready_state"] == "complete", rb
        print(f"SMOKE PASS readback: ready_state={rb['ready_state']} "
              f"active={rb['active_element']}")

        step = "close"
        r = chrome_cdp.close(delete_profile=True)
        assert r["closed"] == "browser" and not r["forced"], r
        assert r["profile_deleted"], r
        print(f"SMOKE PASS close: graceful, profile deleted "
              f"({r['profile_dir']})")
    except Exception:
        print(f"SMOKE FAIL at step: {step}")
        traceback.print_exc()
        try:
            chrome_cdp.close(delete_profile=True)
        except Exception:
            pass
        return 1
    print("SMOKE: ALL STEPS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
