"""One local inference at a time across Ano processes, with memory admission.

The operating-system lock, not a PID file or elapsed time, owns the lease. No
process killing, model unloading, paid fallback or approval changes.
"""
from pathlib import Path
import os


class InferenceBusy(RuntimeError):pass


def memory_pressure():
    try:
        import psutil
        ram=psutil.virtual_memory()
        return ram.percent,ram.available
    except ImportError:
        if os.name!='nt':return None
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                      *[(k,ctypes.c_ulonglong) for k in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]]
        info=Memory();info.length=ctypes.sizeof(info)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)):return None
        return info.load,info.available


def acquire(path=None,sample=memory_pressure,*,activity=True):
    pressure=sample()
    if pressure and (pressure[0]>=92 or pressure[1]<1024**3):
        raise InferenceBusy('Local AI was not started: memory pressure is too high. Built-in commands still work. Free some memory and try again; no apps were closed or paid fallback used.')
    if path is None:
        from .paths import data_directory
        path=data_directory()/'local-inference.lock'
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    file=path.open('a+b')
    try:
        if os.fstat(file.fileno()).st_size==0:file.write(b'0');file.flush()
        file.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        file.close()
        raise InferenceBusy('Another Ano local-model request is still running. No duplicate inference was started. Built-in commands still work.') from None
    if activity:
        try:os.utime(path,None)
        except OSError:pass
    return Lease(file,path if activity else None)


class Lease:
    def __init__(self,file,activity_path=None):self.file,self.activity_path=file,activity_path
    def close(self):
        if self.file is None:return
        file,self.file=self.file,None
        try:
            if self.activity_path is not None:
                try:os.utime(self.activity_path,None)
                except OSError:pass
            file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(file,fcntl.LOCK_UN)
        finally:file.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
