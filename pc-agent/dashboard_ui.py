"""MCP Apps dashboard widget (SEP-1865 / io.modelcontextprotocol/ui).

The HTML below is a static panel: the SDK ships no in-iframe tool-call
bridge, so nothing here pretends to fetch live data on its own. Live
data arrives the honest way — the ``dashboard`` tool's result, which
Apps-capable hosts render beside this panel, and which every other
client (including Brio) reads as plain text. The panel explains
itself, names the tool that feeds it, and stays correct whether or
not the host renders it.
"""

DASHBOARD_URI = "ui://alterego/dashboard.html"

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AlterEgo Bridge</title>
<style>
  body { margin: 0; padding: 16px; background: #101418; color: #e8edf2;
         font-family: system-ui, sans-serif; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  p { font-size: 13px; line-height: 1.5; color: #b8c2cc; margin: 0 0 12px; }
  .row { display: flex; gap: 8px; }
  .card { flex: 1; background: #1a2129; border: 1px solid #2a343f;
          border-radius: 8px; padding: 12px; }
  .card b { display: block; font-size: 12px; color: #8fa1b3;
            text-transform: uppercase; letter-spacing: .06em; }
  .card span { font-size: 20px; }
  code { background: #1a2129; padding: 1px 5px; border-radius: 4px; }
</style>
</head>
<body>
<h1>AlterEgo Bridge</h1>
<p>Live PC state. Call the <code>dashboard</code> tool for fresh numbers:
health summary, window count, and the first window titles. This panel
is the widget shell; the tool result is the data. Resources
<code>pc://bridge/health</code>, <code>pc://desktop/windows</code>, and
<code>pc://desktop/state</code> carry the same state addressably.</p>
<div class="row">
  <div class="card"><b>Health</b><span>see tool result</span></div>
  <div class="card"><b>Windows</b><span>see tool result</span></div>
</div>
</body>
</html>
"""
