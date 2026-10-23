import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
src = open(r"pc-agent\chat.html", encoding="utf-8").read()
idx = src.lower().find("connect")
print(src[idx-400:idx+1400])
