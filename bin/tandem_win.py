"""Windows backing for the kernel calls tandem needs: process identity, file locks, Claude's named-pipe inbox.

Imported only when os.name == 'nt'. Everything here is ctypes and the standard library, like the rest of tandem."""
import ctypes
from ctypes import wintypes
import msvcrt
import os
import subprocess
import time

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll')

QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION: enough for name, times and parent, even across elevation
STILL_ACTIVE = 259
PIPE_BUSY = 231

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
ntdll.NtQueryInformationProcess.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                            ctypes.POINTER(wintypes.ULONG))


class Handle:
    def __init__(self, pid):
        self.handle = kernel32.OpenProcess(QUERY_LIMITED, False, int(pid))
        if not self.handle:
            raise OSError(ctypes.get_last_error(), 'cannot open process %s' % pid)

    def __enter__(self):
        return self.handle

    def __exit__(self, *args):
        kernel32.CloseHandle(self.handle)


def process(pid):
    """Same shape as the /proc reader: pid, parent, start (creation FILETIME), name (image name without .exe)."""
    with Handle(pid) as handle:
        code = wintypes.DWORD()
        # An ended process stays openable while anyone holds a handle to it.
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            raise OSError('process %s has ended' % pid)
        basic = (ctypes.c_size_t * 6)()  # PROCESS_BASIC_INFORMATION; [5] is the parent PID
        if ntdll.NtQueryInformationProcess(handle, 0, basic, ctypes.sizeof(basic), None):
            raise OSError('cannot query process %s' % pid)
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, exited, kernel, user))):
            raise OSError(ctypes.get_last_error(), 'cannot read process times')
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), 'cannot read process image name')
    name = os.path.basename(image.value).lower()
    return {'pid': int(pid), 'parent': int(basic[5]), 'start': created.dwHighDateTime << 32 | created.dwLowDateTime,
            'name': name[:-4] if name.endswith('.exe') else name}


def command_line(pid):
    """The process's full command line, as `ps -o args=` gives it elsewhere."""
    with Handle(pid) as handle:
        size = wintypes.ULONG()
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(size))  # ProcessCommandLineInformation
        if not size.value:
            raise OSError('cannot size command line of %s' % pid)
        data = ctypes.create_string_buffer(size.value)
        if ntdll.NtQueryInformationProcess(handle, 60, data, size, ctypes.byref(size)):
            raise OSError('cannot read command line of %s' % pid)

        class UnicodeString(ctypes.Structure):
            _fields_ = [('length', wintypes.USHORT), ('maximum', wintypes.USHORT), ('buffer', ctypes.c_void_p)]
        text = UnicodeString.from_buffer(data)
        return ctypes.wstring_at(text.buffer, text.length // 2) if text.buffer else ''


def flock(stream):
    """Exclusive, non-blocking, released when the stream closes: flock(LOCK_EX | LOCK_NB) on one byte."""
    fd = stream.fileno()
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        raise BlockingIOError('lock held') from None


def pipe_send(path, data, timeout=3):
    """Write data to a named pipe such as \\\\.\\pipe\\LOCAL\\cc-msg-…; False when nothing was written.

    Claude Code on Windows publishes its inbox as a named pipe where other systems have a Unix socket. Bytes
    written before the handle closes stay readable to the server, so closing is the half-close."""
    end = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_RDWR | os.O_BINARY)
            break
        except OSError as error:
            # Every pipe instance is busy with another client: a moment later one is free again.
            if getattr(error, 'winerror', None) != PIPE_BUSY or time.monotonic() >= end:
                return False
            time.sleep(.02)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)
    return True


class JobLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION, of which only LimitFlags is set."""
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD),
                ('IoInfo', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t)]


def end_with_me(child):
    """End the child (a subprocess.Popen) when this process ends, however it ends. Closing a console window
    kills its programs without running their cleanup, and there is no SIGHUP to catch; a job object that
    closes with its last handle does the same work. Returns the handle to keep alive, or None."""
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    limits = JobLimits(LimitFlags=0x2000)  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) \
            or not kernel32.AssignProcessToJobObject(job, int(child._handle)):
        return None
    return job


def spawn_console(args, cwd, env):
    """An engine in a console window of its own, where its user can watch and type: what tmux gives elsewhere."""
    return subprocess.Popen(args, cwd=cwd, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)


def spawn_detached(args):
    """A background process with no console window that outlives the hook or CLI that started it."""
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Leave the caller's job object when it allows that, so ending the caller does not end the worker.
        return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **quiet)
    except OSError:
        return subprocess.Popen(args, creationflags=flags, **quiet)
