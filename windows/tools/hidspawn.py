"""Elevated: hook HID writes in every GIGABYTE process until stop.flag appears.

Logs HidD_SetFeature / HidD_SetOutputReport / WriteFile / DeviceIoControl
payloads (with the device path behind each handle) to hidhook.log.
"""
import os, sys, time, json
import frida
import psutil

DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DIR, "hidspawn.log")
STOP = os.path.join(DIR, "stop.flag")

JS = r"""
const paths = {};
function hex(p, n) { try { return Array.from(new Uint8Array(p.readByteArray(n))).map(b => b.toString(16).padStart(2,'0')).join(' '); } catch (e) { return '?'; } }
function hook(mod, name, cb) {
  const f = Process.findModuleByName(mod)?.findExportByName(name);
  if (f) Interceptor.attach(f, cb);
}
hook('kernel32.dll', 'CreateFileW', {
  onEnter(a) { this.p = a[0].readUtf16String(); },
  onLeave(r) { if (this.p && /hid|vid_/i.test(this.p)) paths[r.toString()] = this.p; }
});
hook('kernelbase.dll', 'CreateFileW', {
  onEnter(a) { this.p = a[0].readUtf16String(); },
  onLeave(r) { if (this.p && /hid|vid_/i.test(this.p)) paths[r.toString()] = this.p; }
});
function dev(h) { const p = paths[h.toString()] || ''; const m = p.match(/pid_([0-9a-f]{4})/i); return m ? m[1] : (p ? p.slice(-40) : 'h' + h); }
hook('hid.dll', 'HidD_SetFeature', { onEnter(a) { send({f:'SetFeature', d:dev(a[0]), x:hex(a[1], a[2].toInt32())}); } });
hook('hid.dll', 'HidD_GetFeature', { onEnter(a) { this.a=a[1]; this.n=a[2].toInt32(); this.h=a[0]; }, onLeave(r) { send({f:'GetFeature', d:dev(this.h), x:hex(this.a, this.n)}); } });
hook('hid.dll', 'HidD_SetOutputReport', { onEnter(a) { send({f:'SetOutput', d:dev(a[0]), x:hex(a[1], a[2].toInt32())}); } });
hook('kernelbase.dll', 'WriteFile', { onEnter(a) { const h=a[0].toString(); if (paths[h]) send({f:'WriteFile', d:dev(a[0]), x:hex(a[1], a[2].toInt32())}); } });
hook('kernelbase.dll', 'DeviceIoControl', { onEnter(a) { const h=a[0].toString(); const code=a[1].toUInt32(); if (paths[h] && a[3].toInt32() > 0) send({f:'IOCTL '+code.toString(16), d:dev(a[0]), x:hex(a[2], a[3].toInt32())}); } });
send({f:'ready', d:Process.id, x:''});
"""

def main():
    out = open(LOG, "a", encoding="utf-8")
    def on_msg(msg, data, name):
        if msg["type"] == "send":
            p = msg["payload"]
            out.write(f"{time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d} {name} {p['f']} {p['d']} {p['x']}\n")
        else:
            out.write(f"{name} ERR {msg}\n")
        out.flush()
    exe = os.path.join(os.environ["ProgramFiles"], "GIGABYTE", "Control Center", "GCC.exe")
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"].lower() == "gcc.exe":
            proc.kill()
    time.sleep(2)
    pid = frida.spawn(exe, cwd=os.path.dirname(exe))
    s = frida.attach(pid)
    sc = s.create_script(JS)
    sc.on("message", lambda m, d: on_msg(m, d, "GCC:%d" % pid))
    sc.load()
    frida.resume(pid)
    out.write("spawned %d" % pid + chr(10)); out.flush()
    while not os.path.exists(STOP):
        time.sleep(0.3)
    try:
        s.detach()
    except Exception:
        pass
    out.write("stopped" + chr(10))


if __name__ == "__main__":
    main()
