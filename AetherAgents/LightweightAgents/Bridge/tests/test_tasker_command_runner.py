"""Verify command process ownership, output bounds and real outcomes; never access network or resident homes."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from aether.tasker.command_runner import run,OUTPUT_BYTES
from aether.tasker.model import test_environment as environment

def invoke(tmp_path,code,timeout=5):
    return run([sys.executable,'-B','-c',code],cwd=str(tmp_path),env=environment(),timeout=timeout)

def alive(pid):
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x100000,False,pid)
    if not handle:return False
    try:return kernel.WaitForSingleObject(handle,1000)==258
    finally:kernel.CloseHandle(handle)

@pytest.mark.parametrize('timeout',[False,True])
def test_descendant_is_gone_after_command_finishes_or_times_out(tmp_path,timeout):
    code='import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(30)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);Path("child.pid").write_text(str(p.pid));'+('time.sleep(30)' if timeout else '')
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired):invoke(tmp_path,code,1)
    else:assert invoke(tmp_path,code).returncode==0
    assert not alive(int((tmp_path/'child.pid').read_text()))

def test_output_is_bounded_and_tail_preserved(tmp_path):
    result=invoke(tmp_path,'import sys;sys.stdout.write("x"*5000000+"END");sys.stderr.write("y"*1000000+"ERR")')
    assert result.returncode==0 and len(result.stdout)==OUTPUT_BYTES and result.stdout.endswith(b'END')
    assert len(result.stderr)==OUTPUT_BYTES and result.stderr.endswith(b'ERR')

def test_real_cwd_stdin_and_exit_status(tmp_path):
    result=invoke(tmp_path,'import os,sys;print(os.getcwd());print(repr(sys.stdin.read()));raise SystemExit(127)')
    assert result.returncode==127 and str(tmp_path).encode() in result.stdout and b"''" in result.stdout

def test_missing_program_is_distinct_from_program_exit_127(tmp_path):
    with pytest.raises(FileNotFoundError):
        run([str(tmp_path/'absent-program.exe')],cwd=str(tmp_path),env=environment(),timeout=3)
