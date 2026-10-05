"""Service Providers: one package per platform.

windows/: the current implementation (ctypes, win32, PowerShell).
linux/ and mock/ arrive in later phases; the registry treats a missing
group module as an honest gap, not an error.
"""
