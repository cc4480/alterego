"""Windows provider: the current implementation, relocated verbatim.

Native Windows mechanics live here (ctypes, win32gui, USERPROFILE,
PowerShell, native approval dialogs). Nothing outside this package
may import platform specifics.
"""
