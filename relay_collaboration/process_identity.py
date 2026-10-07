"""Read OS process identity; a saved PID or status file is never liveness proof."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path

class IdentityUnavailable(RuntimeError):
    pass

def _windows(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise IdentityUnavailable("process_identity_unreadable")
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise IdentityUnavailable("process_state_unreadable")
        if exit_code.value != 259:
            return None
        created, exited, kern, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kern), ctypes.byref(user)):
            raise IdentityUnavailable("process_creation_unreadable")
        name = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            raise IdentityUnavailable("process_executable_unreadable")
        ntdll = ctypes.WinDLL("ntdll")
        query = ntdll.NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        query.restype = ctypes.c_long
        needed = wintypes.ULONG()
        query(handle, 60, None, 0, ctypes.byref(needed))
        if not 1 <= needed.value <= 1024 * 1024:
            raise IdentityUnavailable("process_commandline_unreadable")
        buffer = ctypes.create_string_buffer(needed.value)
        if query(handle, 60, buffer, len(buffer), ctypes.byref(needed)) < 0:
            raise IdentityUnavailable("process_commandline_unreadable")
        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]
        command = UnicodeString.from_buffer(buffer)
        begin, end = ctypes.addressof(buffer), ctypes.addressof(buffer) + len(buffer)
        if not command.Buffer or command.Length % 2 or not begin <= command.Buffer <= command.Buffer + command.Length <= end:
            raise IdentityUnavailable("process_commandline_invalid")
        command_line = ctypes.wstring_at(command.Buffer, command.Length // 2)
        return {"pid": pid, "creation_time": str((created.dwHighDateTime << 32) | created.dwLowDateTime),
                "executable": os.path.normcase(os.path.realpath(name.value)), "command_line": command_line}
    finally:
        kernel.CloseHandle(handle)

def _linux(pid):
    folder = Path('/proc') / str(pid)
    try:
        details = (folder / 'stat').read_text(encoding='utf-8').rpartition(')')[2].split()
        if details[0] == 'Z':
            return None
        created = details[19]
        executable = os.path.realpath(os.readlink(folder / 'exe'))
        command_line = (folder / 'cmdline').read_bytes().decode('utf-8').rstrip('\0').split('\0')
        if (folder / 'stat').read_text(encoding='utf-8').rpartition(')')[2].split()[19] != created:
            raise IdentityUnavailable('process_changed_during_read')
        return {'pid': pid, 'creation_time': created, 'executable': executable, 'command_line': command_line}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, IndexError, UnicodeError):
        raise IdentityUnavailable('process_identity_unreadable') from None

def inspect_process(pid):
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise IdentityUnavailable('invalid_process_id')
    if os.name == 'nt':
        return _windows(pid)
    if Path('/proc').is_dir():
        return _linux(pid)
    raise IdentityUnavailable('process_identity_platform_unavailable')

def matches_process(expected, actual):
    return isinstance(expected, dict) and isinstance(actual, dict) and all(
        key in expected and expected[key] == actual.get(key)
        for key in ('pid', 'creation_time', 'executable', 'command_line'))
