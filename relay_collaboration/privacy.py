"""Private runtime ACL verification."""
import os, stat, ctypes
from pathlib import Path
class ServiceError(ValueError): pass

def require_private_state(root: Path):
    """Validate existing ACL; never change the owner's permissions or config."""
    if os.name != "nt":
        info = root.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ServiceError("state_root_permissions_not_private")
        return
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                           ptr, ptr, ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [ptr, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.GetAclInformation.argtypes = [ptr, ptr, wintypes.DWORD, ctypes.c_int]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [ptr, wintypes.DWORD, ctypes.POINTER(ptr)]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ptr, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ptr]
    kernel.LocalFree.restype = ptr
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def sid_text(sid):
        value = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ServiceError("state_acl_sid_unreadable")
        try:
            return value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ptr))

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ServiceError("state_acl_current_user_unreadable")
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ServiceError("state_acl_current_user_unreadable")
        current_sid = sid_text(ctypes.cast(buffer, ctypes.POINTER(ptr)).contents)
    finally:
        kernel.CloseHandle(token)
    descriptor, dacl = ptr(), ptr()
    code = advapi.GetNamedSecurityInfoW(str(root), 1, 4, None, None, ctypes.byref(dacl), None,
                                      ctypes.byref(descriptor))
    if code or not descriptor or not dacl:
        if descriptor:
            kernel.LocalFree(descriptor)
        raise ServiceError("state_acl_missing_or_unreadable")
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)) or not control.value & 0x1000:
            raise ServiceError("state_acl_must_disable_inheritance")
        info = (wintypes.DWORD * 3)()
        if not advapi.GetAclInformation(dacl, info, ctypes.sizeof(info), 2):
            raise ServiceError("state_acl_unreadable")
        allowed = {current_sid, "S-1-5-18", "S-1-5-32-544"}  # user, SYSTEM, Administrators
        for index in range(info[0]):
            ace = ptr()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise ServiceError("state_acl_unreadable")
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            if ace_type == 0 and sid_text(ptr(ace.value + 8)) not in allowed:
                raise ServiceError("state_acl_grants_another_principal")
            if ace_type not in (0, 1):
                raise ServiceError("state_acl_requires_manual_review")
    finally:
        kernel.LocalFree(descriptor)

