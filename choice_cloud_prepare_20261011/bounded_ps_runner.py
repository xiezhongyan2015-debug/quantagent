"""Bound diagnostic PowerShell memory, lifetime and output; no account calls."""
import subprocess,sys,threading
import win32job,win32api

timeout=min(60,max(1,int(sys.argv[1])))
job=win32job.CreateJobObject(None,'')
info=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
info['BasicLimitInformation']['LimitFlags']=win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY|win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
info['ProcessMemoryLimit']=256*1024*1024
win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,info)
process=subprocess.Popen(['powershell.exe','-NoLogo','-NoProfile','-EncodedCommand',sys.argv[2]],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
win32job.AssignProcessToJobObject(job,int(process._handle))
buffers=[bytearray(),bytearray()];lock=threading.Lock();overflow=threading.Event()
def read_pipe(pipe,index):
    while True:
        data=pipe.read1(4096)
        if not data:return
        with lock:
            if sum(len(x) for x in buffers)+len(data)>65536:
                overflow.set();process.terminate();return
            buffers[index].extend(data)
threads=[threading.Thread(target=read_pipe,args=(pipe,i),daemon=True) for i,pipe in enumerate([process.stdout,process.stderr])]
for thread in threads:thread.start()
try:
    code=process.wait(timeout=timeout)
except subprocess.TimeoutExpired:
    process.kill();process.wait();code=124
finally:
    win32api.CloseHandle(job)
for thread in threads:thread.join(timeout=2)
if overflow.is_set():
    print('Diagnostic output exceeded 64 KiB; process stopped.',file=sys.stderr);sys.exit(125)
if code==124:
    print('Diagnostic timed out; process stopped.',file=sys.stderr);sys.exit(code)
sys.stdout.buffer.write(buffers[0]);sys.stderr.buffer.write(buffers[1]);sys.exit(code)
