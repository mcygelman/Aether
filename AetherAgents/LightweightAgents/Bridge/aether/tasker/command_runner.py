"""Run a command with bounded output and owned descendant lifetime; never grant filesystem access or claim OS isolation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

OUTPUT_BYTES=24000

def run(argv,*,cwd,env,timeout):
    with tempfile.TemporaryDirectory(prefix='aether-cmd-') as folder:
        receipt=Path(folder)/'result.json'
        request=dict(argv=argv,timeout=timeout,receipt=str(receipt))
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
        proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('command_worker.py'))],
            cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options)
        buffers=[bytearray(),bytearray()]
        def drain(stream,tail):
            try:
                while chunk:=stream.read(8192):
                    tail.extend(chunk)
                    if len(tail)>OUTPUT_BYTES:del tail[:-OUTPUT_BYTES]
            finally:stream.close()
        threads=[threading.Thread(target=drain,args=(stream,tail),daemon=True) for stream,tail in zip((proc.stdout,proc.stderr),buffers)]
        for thread in threads:thread.start()
        timed_out=False
        try:
            proc.stdin.write(json.dumps(request).encode());proc.stdin.close()
            try:proc.wait(timeout=timeout+5)
            except subprocess.TimeoutExpired:timed_out=True
        finally:
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=5)
            if os.name!='nt':
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            for thread in threads:thread.join(timeout=5)
            if any(thread.is_alive() for thread in threads):raise RuntimeError('Command output streams did not close after process cleanup.')
        if timed_out:raise subprocess.TimeoutExpired(argv,timeout)
        if not receipt.is_file():raise RuntimeError('Command worker ended without an outcome receipt.')
        result=json.loads(receipt.read_text(encoding='utf-8'))
        if result['status']=='timed_out':raise subprocess.TimeoutExpired(argv,timeout)
        if result['status']=='not_found':raise FileNotFoundError(argv[0])
        return subprocess.CompletedProcess(argv,result['exit_code'],bytes(buffers[0]),bytes(buffers[1]))
