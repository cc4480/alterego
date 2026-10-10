src = open(r"pc-agent\server.py", encoding="utf-8").read()
i = src.find("async def _ws_relay")
print(src[i:i+1600].replace("\r\n","\n"))
