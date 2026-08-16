# Controlus — RGB lighting control

Controls RGB on **Gigabyte AORUS keyboards** and **Logitech G Pro Wireless
mice** by talking to the hardware directly over HID, without vendor software.
Started on Linux; Windows port (PySide6 + hidapi) landed 2026-06-23. Mouse
support uses the HID++ protocol over the Lightspeed receiver.

Has **its own git repo** — run git from this folder. See `README.md` for
features and install steps.

## Layout

| path | what |
|---|---|
| `app/` | standalone Python GUI |
| `extension/` | GNOME Shell extension (Quick Settings integration) |
| `windows/` | Windows port |
| `install.sh` | Linux installer |
| `99-controlus.rules` | udev rules for non-root HID access |
| `research/` | reverse-engineering input, see below |

## research/ — where the protocol came from

The AORUS keyboard protocol was recovered by tearing apart Gigabyte Control
Center rather than from documentation.

- `research/GCC_26.03.31.01.zip` — the original 897 MB vendor installer
  (`GIGABYTE Control Center_2026_Mar_release_All_Setup_26.03.31.01.exe`).
- `research/gcc-extracted/` — its unpacked internals: `kbd/`,
  `KeyboardDomainLogic2023/`, `KeyboardModel2023/`, `core15/`, plus the
  analysis scripts used to mine them (`dbdump.py`, `find.py`, `classes.txt`).

**`research/` is ~3 GB and untracked by git.** It shows up as `?? research/`
in `git status`. Add it to `.gitignore` if that noise gets annoying — the repo
currently has no `.gitignore` at all.
