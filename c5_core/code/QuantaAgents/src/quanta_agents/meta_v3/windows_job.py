"""Windows job accounting from process birth through all descendant exits.

IO_COUNTERS are OS process IO, not physical disk transfers. The supervisor
is outside the measured experiment. No breakaway flags are enabled.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
import math
import os
import subprocess
import time

from .ledger import need


class BasicAccounting(C.Structure):
    _fields_=[(n,C.c_longlong) for n in ('TotalUserTime','TotalKernelTime','ThisPeriodTotalUserTime','ThisPeriodTotalKernelTime')]+[(n,W.DWORD) for n in ('TotalPageFaultCount','TotalProcesses','ActiveProcesses','TotalTerminatedProcesses')]


class IoCounters(C.Structure):
    _fields_=[(n,C.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]


class Accounting(C.Structure):
    _fields_=[('BasicInfo',BasicAccounting),('IoInfo',IoCounters)]


class BasicLimits(C.Structure):
    _fields_=[('PerProcessUserTimeLimit',C.c_longlong),('PerJobUserTimeLimit',C.c_longlong),('LimitFlags',W.DWORD),
        ('MinimumWorkingSetSize',C.c_size_t),('MaximumWorkingSetSize',C.c_size_t),('ActiveProcessLimit',W.DWORD),
        ('Affinity',C.c_size_t),('PriorityClass',W.DWORD),('SchedulingClass',W.DWORD)]


class ExtendedLimits(C.Structure):
    _fields_=[('BasicLimitInformation',BasicLimits),('IoInfo',IoCounters)]+[(n,C.c_size_t) for n in ('ProcessMemoryLimit','JobMemoryLimit','PeakProcessMemoryUsed','PeakJobMemoryUsed')]


class StartupInfo(C.Structure):
    _fields_=[('cb',W.DWORD),('lpReserved',W.LPWSTR),('lpDesktop',W.LPWSTR),('lpTitle',W.LPWSTR)]+[(n,W.DWORD) for n in ('dwX','dwY','dwXSize','dwYSize','dwXCountChars','dwYCountChars','dwFillAttribute','dwFlags')]+[
        ('wShowWindow',W.WORD),('cbReserved2',W.WORD),('lpReserved2',C.POINTER(W.BYTE)),
        ('hStdInput',W.HANDLE),('hStdOutput',W.HANDLE),('hStdError',W.HANDLE)]


class ProcessInfo(C.Structure):
    _fields_=[('hProcess',W.HANDLE),('hThread',W.HANDLE),('dwProcessId',W.DWORD),('dwThreadId',W.DWORD)]


def api():
    need(os.name=='nt','Windows job accounting unavailable on this platform')
    k=C.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
        'OpenJobObjectW':([W.DWORD,W.BOOL,W.LPCWSTR],W.HANDLE),
        'CloseHandle':([W.HANDLE],W.BOOL),
        'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
        'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
        'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
        'IsProcessInJob':([W.HANDLE,W.HANDLE,C.POINTER(W.BOOL)],W.BOOL),
        'GetCurrentProcess':([],W.HANDLE),
        'CreateProcessW':([W.LPCWSTR,W.LPWSTR,C.c_void_p,C.c_void_p,W.BOOL,W.DWORD,C.c_void_p,W.LPCWSTR,C.POINTER(StartupInfo),C.POINTER(ProcessInfo)],W.BOOL),
        'ResumeThread':([W.HANDLE],W.DWORD),
        'TerminateProcess':([W.HANDLE,W.UINT],W.BOOL),
        'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),
        'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD),
        'GetExitCodeProcess':([W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),
    }
    for n,(args,result) in signatures.items():
        f=getattr(k,n);f.argtypes=args;f.restype=result
    return k


def checked(value):
    if not value: raise C.WinError(C.get_last_error())
    return value


def measure(k,handle):
    value=Accounting();checked(k.QueryInformationJobObject(handle,8,C.byref(value),C.sizeof(value),None))
    b,i=value.BasicInfo,value.IoInfo
    return {'user_cpu_100ns':b.TotalUserTime,'kernel_cpu_100ns':b.TotalKernelTime,
        'cpu_ms':math.ceil((b.TotalUserTime+b.TotalKernelTime)/10000),
        'read_ops':i.ReadOperationCount,'write_ops':i.WriteOperationCount,'other_ops':i.OtherOperationCount,
        'read_bytes':i.ReadTransferCount,'write_bytes':i.WriteTransferCount,'other_bytes':i.OtherTransferCount,
        'io_transfer_bytes':i.ReadTransferCount+i.WriteTransferCount+i.OtherTransferCount,
        'total_processes':b.TotalProcesses,'active_processes':b.ActiveProcesses,
        'processes_terminated_by_limit':b.TotalTerminatedProcesses}


def own_measurement(name):
    k=api();handle=checked(k.OpenJobObjectW(4,False,name))
    try:
        member=W.BOOL();checked(k.IsProcessInJob(k.GetCurrentProcess(),handle,C.byref(member)))
        need(bool(member.value),'current process is outside the registered experiment job')
        return measure(k,handle)
    finally:k.CloseHandle(handle)


class Job:
    def __init__(self,name):
        self.k=api();self.name=name;self.process=None;self.handle=checked(self.k.CreateJobObjectW(None,name))
        try:
            limits=ExtendedLimits();limits.BasicLimitInformation.LimitFlags=0x2000
            checked(self.k.SetInformationJobObject(self.handle,9,C.byref(limits),C.sizeof(limits)))
        except BaseException:
            self.close();raise

    def close(self):
        # Kill-on-close applies only to this newly owned job, never other jobs.
        if self.handle:self.k.CloseHandle(self.handle);self.handle=None
        if self.process:
            self.k.CloseHandle(self.process.hThread);self.k.CloseHandle(self.process.hProcess);self.process=None

    def start(self,argv,*,cwd,env,log_path,on_suspended):
        import msvcrt
        need(self.process is None,'job primary process already created')
        self.started_at=time.time();self.clock=time.perf_counter()
        with open(os.devnull,'rb') as stdin,open(log_path,'xb') as log:
            si=StartupInfo();si.cb=C.sizeof(si);si.dwFlags=0x101;si.wShowWindow=0
            si.hStdInput=msvcrt.get_osfhandle(stdin.fileno())
            si.hStdOutput=si.hStdError=msvcrt.get_osfhandle(log.fileno())
            handles=[si.hStdInput,si.hStdOutput]
            old=[os.get_handle_inheritable(h) for h in handles]
            try:
                for h in handles:os.set_handle_inheritable(h,True)
                command=C.create_unicode_buffer(subprocess.list2cmdline(argv))
                environment=C.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items()))+'\0\0')
                info=ProcessInfo()
                checked(self.k.CreateProcessW(argv[0],command,None,None,True,0x4|0x400|0x08000000,
                    environment,str(cwd),C.byref(si),C.byref(info)))
                self.process=info
            finally:
                for h,inheritable in zip(handles,old):os.set_handle_inheritable(h,inheritable)
        try:
            checked(self.k.AssignProcessToJobObject(self.handle,self.process.hProcess))
            need(self.measurement()['total_processes']==1,'unexpected preexisting job processes')
            on_suspended({'pid':self.process.dwProcessId,'job_name':self.name,'started_at':self.started_at,
                          'assigned_before_resume':True})
            need(self.k.ResumeThread(self.process.hThread)!=0xFFFFFFFF,'cannot resume accounted process')
        except BaseException:
            self.k.TerminateProcess(self.process.hProcess,99)
            raise

    def measurement(self):return measure(self.k,self.handle)

    def poll(self):
        state=self.k.WaitForSingleObject(self.process.hProcess,0)
        if state==258:return None
        need(state==0,'cannot observe primary process exit')
        code=W.DWORD();checked(self.k.GetExitCodeProcess(self.process.hProcess,C.byref(code)))
        return code.value

    def terminate(self):checked(self.k.TerminateJobObject(self.handle,98))


@contextmanager
def job(name):
    value=Job(name)
    try:yield value
    finally:value.close()
