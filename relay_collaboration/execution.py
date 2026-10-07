"""Stable installation paths and subprocess behavior for source and frozen builds."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def frozen():
    return bool(getattr(sys, 'frozen', False))


def resource_root():
    return Path(__file__).resolve().parent.parent


def package_root():
    return Path(sys.executable).resolve().parent if frozen() else resource_root()


def command_prefix():
    if frozen():
        return [sys.executable]
    entry = resource_root() / 'relay.py'
    launch = [str(entry)] if entry.is_file() else ['-m', 'relay_collaboration']
    return [sys.executable, '-B', '-X', 'utf8', *launch]


def supervisor_environment():
    env = dict(os.environ)
    if frozen():
        # An independent onefile supervisor must own its extraction directory.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def configure_process():
    if not frozen():
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    if os.name == 'nt':
        # Preload our extension dependencies, then keep external official CLIs
        # from inheriting the bootloader's private DLL search directory.
        import ctypes
        import ssl
        import sqlite3
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.restype = ctypes.c_int
        if not kernel.SetDllDirectoryW(None):
            raise OSError('Unable to restore the Windows DLL search directory')
