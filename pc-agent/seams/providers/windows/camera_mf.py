"""Media Foundation camera capture via ctypes — backend #2 for
camera_snapshot (see seams.providers.windows.read.camera_snapshot).

Why this backend exists: mfplat/mfreadwrite/mf ship in every Windows 10
install, so this is the only capture path that satisfies the project's
zero-new-dependencies rule UNCONDITIONALLY (ffmpeg, backend #1, is a
separate install that may or may not be on the box). The cost is
hand-declared COM vtables: every HRESULT is checked and mapped to a
named RuntimeError — this module never returns a frame it did not
actually read, and any mismatch (wrong GUID, missing interface,
device gone) degrades to a loud error, not a fake success.

STATUS: written on a Linux VM; it cannot run here (ctypes.WinDLL does
not exist off Windows). It is import-safe on any platform and fails
loud off-Windows. The GUID constants below are the published SDK
values (mfidl.h / mfreadwrite.h); first live run on a Windows host
must verify enumeration + one real capture before this backend is
trusted (the ffmpeg backend is primary where present).

Only the interfaces used are declared, with their vtable indices:
  IUnknown        0 QueryInterface, 1 AddRef, 2 Release
  IMFAttributes   3 GetItem .. 10 GetGUID, 13 GetAllocatedString,
                  21 SetUINT32, 22 SetUINT64, 24 SetGUID (of 3..32)
  IMFActivate     33 ActivateObject (after all IMFAttributes slots)
  IMFMediaSource  7 CreatePresentationDescriptor .. 11 Shutdown
                  (after IUnknown + IMFMediaEventGenerator 3..6)
  IMFSourceReader 5 GetNativeMediaType, 6 GetCurrentMediaType,
                  7 SetCurrentMediaType, 10 ReadSample (of 3..10)
  IMFSample       11 ConvertToContiguousBuffer (of 3..16)
  IMFMediaBuffer  3 Lock, 4 Unlock (of 3..7)
"""
import ctypes
import struct
import threading
import uuid
import zlib

BACKEND_NAME = "media-foundation"

# --- SDK constants (GUIDs are the published mfidl.h values) ----------
_MF_VERSION = 0x00020070
_MF_SOURCE_READER_FIRST_VIDEO_STREAM = 0xFFFFFFFC  # (DWORD)-4
_MF_E_NO_MORE_TYPES = 0xC00D36B6
_MF_SOURCE_READERF_ENDOFSTREAM = 0x00000002

_ATTR_SOURCE_TYPE = "6970a9c8-f31e-4396-86b9-728d9d9d2f8e"
_ATTR_SOURCE_TYPE_VIDCAP = "8ac3587a-4ae7-42d8-99e0-0a6013eef90f"
_ATTR_FRIENDLY_NAME = "60d0e559-52f8-4fa2-bbce-acdb34a8ec01"
_MT_MAJOR_TYPE = "48eba18e-f8c9-4687-bf11-0a74c9f96a8f"
_MT_SUBTYPE = "f7e34c9a-42e8-4714-b74b-cb29d72ce5e5"
_MT_FRAME_SIZE = "1652c33d-d6b2-4012-b834-72030849a37d"
_MEDIA_TYPE_VIDEO = "73646976-0000-0010-8000-00aa00389b71"  # 'vids'
_VIDEO_FORMAT_RGB32 = "00000016-0000-0010-8000-00aa00389b71"  # D3DFMT 22
_IID_IUNKNOWN = "00000000-0000-0000-c000-000000000046"


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _check(hr, step):
    if hr != 0:
        raise RuntimeError(
            f"media_foundation: {step} failed "
            f"(HRESULT 0x{hr & 0xFFFFFFFF:08X})")


def _fn(ptr, index, restype, *argtypes):
    """Bind vtable slot `index` of a COM interface pointer."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(
        ctypes.c_void_p))).contents
    return ctypes.cast(vtbl[index], ctypes.CFUNCTYPE(
        restype, ctypes.c_void_p, *argtypes))


def _release(ptr):
    if ptr:
        _fn(ptr, 2, ctypes.c_ulong)(ptr)


def _get_guid(ptr, key_guid):
    out = _GUID()
    _check(_fn(ptr, 10, ctypes.c_long, ctypes.POINTER(_GUID),
               ctypes.POINTER(_GUID))(
                   ptr, ctypes.byref(_guid(key_guid)), ctypes.byref(out)),
           "GetGUID")
    return out


def _get_u64(ptr, key_guid):
    out = ctypes.c_ulonglong(0)
    _check(_fn(ptr, 8, ctypes.c_long, ctypes.POINTER(_GUID),
               ctypes.POINTER(ctypes.c_ulonglong))(
                   ptr, ctypes.byref(_guid(key_guid)), ctypes.byref(out)),
           "GetUINT64")
    return out.value


def _set_guid(ptr, key_guid, value_guid):
    _check(_fn(ptr, 24, ctypes.c_long, ctypes.POINTER(_GUID),
               ctypes.POINTER(_GUID))(
                   ptr, ctypes.byref(_guid(key_guid)),
                   ctypes.byref(_guid(value_guid))), "SetGUID")


def _set_u64(ptr, key_guid, value):
    _check(_fn(ptr, 22, ctypes.c_long, ctypes.POINTER(_GUID),
               ctypes.c_ulonglong)(
                   ptr, ctypes.byref(_guid(key_guid)), value), "SetUINT64")


def _friendly_name(activate_ptr, ole32):
    """MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME via GetAllocatedString."""
    get_str = _fn(activate_ptr, 13, ctypes.c_long,
                  ctypes.POINTER(_GUID),
                  ctypes.POINTER(ctypes.c_void_p),
                  ctypes.POINTER(ctypes.c_uint32))
    name_ptr = ctypes.c_void_p()
    length = ctypes.c_uint32(0)
    hr = get_str(activate_ptr, ctypes.byref(_guid(_ATTR_FRIENDLY_NAME)),
                 ctypes.byref(name_ptr), ctypes.byref(length))
    if hr != 0 or not name_ptr.value:
        return ""
    try:
        return ctypes.wstring_at(name_ptr.value)
    finally:
        ole32.CoTaskMemFree(name_ptr)


def _encode_png_rgb(width, height, rgb):
    """Minimal stdlib PNG encoder (filter 0 rows, color type 2)."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride]
                   for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def _luma_stats(rgb):
    """(mean, variance) of luma over sampled pixels of an RGB buffer."""
    n = len(rgb) // 3
    step = max(1, n // 20000)
    total, total_sq, count = 0.0, 0.0, 0
    for i in range(0, n, step):
        r, g, b = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        total += y
        total_sq += y * y
        count += 1
    mean = total / count
    var = max(0.0, total_sq / count - mean * mean)
    return mean, var


def _select_device(devices, names, device):
    """Index of the requested device, or raise naming what exists."""
    if device is None:
        return 0
    if isinstance(device, int):
        if device >= len(devices):
            raise RuntimeError(
                f"camera_snapshot: camera device index {device} not "
                f"found (media_foundation enumerated: {names})")
        return device
    for i, name in enumerate(names):
        if device.lower() in name.lower():
            return i
    raise RuntimeError(
        f"camera_snapshot: camera device {device!r} not found "
        f"(media_foundation enumerated: {names})")


def _capture_inner(device, max_width):
    """Returns (png_bytes, device_name, width, height, mean, variance)."""
    try:
        ole32 = ctypes.WinDLL("ole32")
        mfplat = ctypes.WinDLL("mfplat")
        mf = ctypes.WinDLL("mf")
        mfreadwrite = ctypes.WinDLL("mfreadwrite")
    except (AttributeError, OSError) as e:
        raise RuntimeError(
            f"media_foundation backend unavailable: {e}")
    hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    if hr not in (0, 1, -2147417850):  # S_OK, S_FALSE, RPC_E_CHANGED_MODE
        _check(hr, "CoInitializeEx")
    source = reader = None
    activates, array = [], None
    try:
        _check(mfplat.MFStartup(_MF_VERSION, 0), "MFStartup")
        attrs = ctypes.c_void_p()
        _check(mfplat.MFCreateAttributes(ctypes.byref(attrs), 1),
               "MFCreateAttributes")
        try:
            _set_guid(attrs, _ATTR_SOURCE_TYPE, _ATTR_SOURCE_TYPE_VIDCAP)
            devices = ctypes.POINTER(ctypes.c_void_p)()
            count = ctypes.c_uint32(0)
            # MFEnumDeviceSources is exported by mf.dll, NOT mfplat.dll
            # (symbol-probed on Looko, Win10 19045, 2026-10-26: mfplat
            # has MFStartup/MFCreateAttributes/MFCreateMediaType; the
            # enumeration export lives in mf).
            _check(mf.MFEnumDeviceSources(
                attrs, ctypes.byref(devices), ctypes.byref(count)),
                "MFEnumDeviceSources")
            array = devices
            if count.value == 0:
                raise RuntimeError(
                    "camera_snapshot: no camera device found "
                    "(media_foundation enumerated 0 video capture "
                    f"devices; requested device: {device!r})")
            activates = [devices[i] for i in range(count.value)]
            names = [_friendly_name(p, ole32) or f"camera {i}"
                     for i, p in enumerate(activates)]
            chosen = _select_device(activates, names, device)
            dev_name = names[chosen]
            # ActivateObject with IID_IUnknown: for MF capture sources
            # the IMFMediaSource interface pointer coincides with
            # IUnknown (single-inheritance chain), and this avoids a
            # second hand-declared IID. Verified-or-loud: a mismatch
            # surfaces as an HRESULT failure below, never as a frame.
            src = ctypes.c_void_p()
            _check(_fn(activates[chosen], 33, ctypes.c_long,
                       ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                           activates[chosen],
                           ctypes.byref(_guid(_IID_IUNKNOWN)),
                           ctypes.byref(src)), "ActivateObject")
            source = src.value
        finally:
            _release(attrs.value if hasattr(attrs, "value") else attrs)
        rdr = ctypes.c_void_p()
        _check(mfreadwrite.MFCreateSourceReaderFromMediaSource(
            ctypes.c_void_p(source), None, ctypes.byref(rdr)),
            "MFCreateSourceReaderFromMediaSource")
        reader = rdr.value
        width, height = _negotiate(reader, mfplat, max_width)
        rgb = _read_frame(reader, width, height)
        mean, var = _luma_stats(rgb)
        return (_encode_png_rgb(width, height, rgb), dev_name,
                width, height, mean, var)
    finally:
        if reader:
            _release(reader)
        if source:
            _fn(source, 11, ctypes.c_long)(source)  # IMFMediaSource.Shutdown
            _release(source)
        for p in activates:
            _release(p)
        if array:
            ole32.CoTaskMemFree(array)
        try:
            mfplat.MFShutdown()
        except Exception:
            pass
        ole32.CoUninitialize()


def _negotiate(reader, mfplat, max_width):
    """Pick a native (width, height) and set an RGB32 output type.

    max_width is honored by RESOLUTION SELECTION (backend-native, per
    the no-Pillow rule): the largest native mode at or under the cap,
    else the smallest mode available; if that still exceeds the cap
    the capture fails loud rather than silently returning oversize."""
    stream = _MF_SOURCE_READER_FIRST_VIDEO_STREAM
    sizes = []
    idx = 0
    while True:
        mtype = ctypes.c_void_p()
        hr = _fn(reader, 5, ctypes.c_long, ctypes.c_uint32, ctypes.c_uint32,
                 ctypes.POINTER(ctypes.c_void_p))(
                     reader, stream, idx, ctypes.byref(mtype))
        if hr & 0xFFFFFFFF == _MF_E_NO_MORE_TYPES:
            break
        _check(hr, "GetNativeMediaType")
        try:
            packed = _get_u64(mtype.value, _MT_FRAME_SIZE)
            sizes.append((packed >> 32, packed & 0xFFFFFFFF))
        finally:
            _release(mtype.value)
        idx += 1
        if idx > 64:
            break
    if not sizes:
        raise RuntimeError(
            "media_foundation: camera offered no native media types")
    unique = sorted(set(sizes))
    if max_width is not None:
        under = [s for s in unique if s[0] <= max_width]
        chosen = under[-1] if under else unique[0]
        if chosen[0] > max_width:
            raise RuntimeError(
                f"media_foundation: smallest native camera mode "
                f"{chosen[0]}x{chosen[1]} exceeds max_width={max_width}; "
                "refusing to return an oversize frame")
    else:
        chosen = unique[-1]
    out_type = ctypes.c_void_p()
    _check(mfplat.MFCreateMediaType(ctypes.byref(out_type)),
           "MFCreateMediaType")
    try:
        _set_guid(out_type.value, _MT_MAJOR_TYPE, _MEDIA_TYPE_VIDEO)
        _set_guid(out_type.value, _MT_SUBTYPE, _VIDEO_FORMAT_RGB32)
        _set_u64(out_type.value, _MT_FRAME_SIZE,
                 (chosen[0] << 32) | chosen[1])
        hr = _fn(reader, 7, ctypes.c_long, ctypes.c_uint32,
                 ctypes.c_void_p, ctypes.c_void_p)(
                     reader, stream, None, out_type.value)
        if hr != 0:
            # Retry without pinning the frame size; verify actual below.
            out2 = ctypes.c_void_p()
            _check(mfplat.MFCreateMediaType(ctypes.byref(out2)),
                   "MFCreateMediaType (retry)")
            try:
                _set_guid(out2.value, _MT_MAJOR_TYPE, _MEDIA_TYPE_VIDEO)
                _set_guid(out2.value, _MT_SUBTYPE, _VIDEO_FORMAT_RGB32)
                _check(_fn(reader, 7, ctypes.c_long, ctypes.c_uint32,
                           ctypes.c_void_p, ctypes.c_void_p)(
                               reader, stream, None, out2.value),
                       "SetCurrentMediaType")
            finally:
                _release(out2.value)
            cur = ctypes.c_void_p()
            _check(_fn(reader, 6, ctypes.c_long, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_void_p))(
                           reader, stream, ctypes.byref(cur)),
                   "GetCurrentMediaType")
            try:
                packed = _get_u64(cur.value, _MT_FRAME_SIZE)
                chosen = (packed >> 32, packed & 0xFFFFFFFF)
            finally:
                _release(cur.value)
            if max_width is not None and chosen[0] > max_width:
                raise RuntimeError(
                    f"media_foundation: negotiated mode {chosen[0]}x"
                    f"{chosen[1]} exceeds max_width={max_width}")
    finally:
        _release(out_type.value)
    return chosen


def _read_frame(reader, width, height):
    """ReadSample loop: skip 3 warm-up frames (auto-exposure), convert
    the 4th to a top-down RGB byte string."""
    stream = _MF_SOURCE_READER_FIRST_VIDEO_STREAM
    read_sample = _fn(reader, 10, ctypes.c_long, ctypes.c_uint32,
                      ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                      ctypes.POINTER(ctypes.c_uint32),
                      ctypes.POINTER(ctypes.c_longlong),
                      ctypes.POINTER(ctypes.c_void_p))
    sample = None
    for attempt in range(30):
        actual = ctypes.c_uint32(0)
        flags = ctypes.c_uint32(0)
        ts = ctypes.c_longlong(0)
        samp = ctypes.c_void_p()
        _check(read_sample(reader, stream, 0, ctypes.byref(actual),
                           ctypes.byref(flags), ctypes.byref(ts),
                           ctypes.byref(samp)), "ReadSample")
        if flags.value & _MF_SOURCE_READERF_ENDOFSTREAM:
            raise RuntimeError(
                "media_foundation: end of stream before any frame "
                "(device lost or FrameServer wedged?)")
        if not samp.value:
            continue  # stream tick / format change: keep reading
        if attempt < 3:
            _release(samp.value)  # warm-up frame: discard
            continue
        sample = samp.value
        break
    if sample is None:
        raise RuntimeError(
            "media_foundation: no frame after 30 ReadSample calls")
    buf = ctypes.c_void_p()
    try:
        _check(_fn(sample, 11, ctypes.c_long,
                   ctypes.POINTER(ctypes.c_void_p))(
                       sample, ctypes.byref(buf)),
               "ConvertToContiguousBuffer")
        data_ptr = ctypes.c_void_p()
        max_len = ctypes.c_uint32(0)
        cur_len = ctypes.c_uint32(0)
        _check(_fn(buf.value, 3, ctypes.c_long,
                   ctypes.POINTER(ctypes.c_void_p),
                   ctypes.POINTER(ctypes.c_uint32),
                   ctypes.POINTER(ctypes.c_uint32))(
                       buf.value, ctypes.byref(data_ptr),
                       ctypes.byref(max_len), ctypes.byref(cur_len)),
               "IMFMediaBuffer.Lock")
        try:
            raw = ctypes.string_at(data_ptr.value, cur_len.value)
        finally:
            _fn(buf.value, 4, ctypes.c_long)(buf.value)  # Unlock
    finally:
        _release(buf.value if buf else None)
        _release(sample)
    stride_min = width * 4
    if len(raw) < stride_min * height:
        raise RuntimeError(
            f"media_foundation: short frame buffer ({len(raw)} bytes "
            f"for {width}x{height} RGB32)")
    stride = len(raw) // height if len(raw) % height == 0 else stride_min
    if stride < stride_min:
        stride = stride_min
    rgb = bytearray(width * height * 3)
    # MF RGB32 frames are bottom-up (DIB origin): flip while converting
    # BGRA -> RGB. If a future device delivers top-down, the image is
    # upside down but real — orientation is a verify-on-hardware item,
    # never a silent fake.
    for y in range(height):
        src_row = raw[(height - 1 - y) * stride:(height - 1 - y) * stride
                      + stride_min]
        dst = rgb[y * width * 3:(y + 1) * width * 3]
        dst[0::3] = src_row[2::4]
        dst[1::3] = src_row[1::4]
        dst[2::3] = src_row[0::4]
    return bytes(rgb)


def capture(device=None, max_width=None, timeout_s=25):
    """Capture one frame via Media Foundation.

    Returns (png_bytes, device_name, width, height, mean_luma, variance).
    Runs the COM work on a daemon thread with a hard deadline: a hung
    ReadSample fails loud instead of hanging the agent (a wedged
    Windows Camera Frame Server service is the named suspect)."""
    outcome = {}

    def _work():
        try:
            outcome["result"] = _capture_inner(device, max_width)
        except Exception as e:  # noqa: BLE001 - re-raised on this thread
            outcome["error"] = e

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise RuntimeError(
            f"media_foundation: capture timed out after {timeout_s}s "
            "(possible Windows Camera Frame Server wedge — restart the "
            "'Windows Camera Frame Server' service, not the PC)")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]
