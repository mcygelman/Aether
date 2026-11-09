"""Own one command's descendants until exit or timeout; never load agent context, credentials or interpret task instructions."""
import json
import os
from pathlib import Path
import subprocess
import sys

def own_windows_job():
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
            ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',w.DWORD),
            ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
            ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.GetCurrentProcess.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    job=kernel.CreateJobObjectW(None,None);info=Extended();info.basic.flags=0x2000
    if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        raise RuntimeError('Cannot establish command process ownership.')
    # Intentionally held until this worker exits. The OS then kills every job member.
    return kernel,job

def main():
    request=json.loads(sys.stdin.read());receipt=Path(request['receipt'])
    owner=own_windows_job() if os.name=='nt' else None
    try:
        proc=subprocess.Popen(request['argv'],stdin=subprocess.DEVNULL)
        try:
            code=proc.wait(timeout=request['timeout'])
            outcome=dict(status='finished',exit_code=code)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait();outcome=dict(status='timed_out')
    except FileNotFoundError:
        outcome=dict(status='not_found')
    receipt.write_text(json.dumps(outcome),encoding='utf-8')
    return 0

if __name__=='__main__':raise SystemExit(main())
