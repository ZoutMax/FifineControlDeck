"""Keep only the Qt libraries our modules actually need.

Walks DT_NEEDED entries (readelf -d) rather than ldd: ldd only reports a path
when the loader can RESOLVE the library, and the bundled Qt libs resolve via an
RPATH that is not in effect when ldd runs from outside the AppImage. Using ldd
here silently produced an EMPTY closure and deleted all 109 Qt libraries.
"""
import os, re, subprocess, sys

SP = sys.argv[1]
PQ = os.path.join(SP, "PyQt6")
QT = os.path.join(PQ, "Qt6")
LIB = os.path.join(QT, "lib")
KEEP_MODS = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtDBus"}
KEEP_PLUGIN_DIRS = {"platforms", "platformthemes", "platforminputcontexts",
                    "imageformats", "iconengines", "xcbglintegrations",
                    "wayland-shell-integration", "wayland-decoration-client",
                    "wayland-graphics-integration-client", "egldeviceintegrations",
                    "generic", "styles", "tls"}

NEEDED = re.compile(r"\(NEEDED\).*\[(.+?)\]")


def needed_of(path):
    try:
        out = subprocess.run(["readelf", "-d", path], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return []
    return NEEDED.findall(out)


present = {f for f in os.listdir(LIB) if os.path.isfile(os.path.join(LIB, f))}


def close_over(roots):
    seen, queue = set(), list(roots)
    while queue:
        p = queue.pop()
        for name in needed_of(p):
            if name in seen or name not in present:
                continue
            seen.add(name)
            queue.append(os.path.join(LIB, name))
    return seen


# 1. drop PyQt6 python extension modules we never import
dropped_mods = []
for f in sorted(os.listdir(PQ)):
    base = f.split(".")[0]
    if base.startswith("Qt") and base not in KEEP_MODS and (
            f.endswith(".abi3.so") or f.endswith(".pyi")):
        os.remove(os.path.join(PQ, f))
        dropped_mods.append(base)

# 2. roots: the extension modules we kept, plus the plugins we ship
roots = [os.path.join(PQ, f) for f in os.listdir(PQ) if f.endswith(".abi3.so")]
plug = os.path.join(QT, "plugins")
for d in sorted(os.listdir(plug)):
    p = os.path.join(plug, d)
    if d not in KEEP_PLUGIN_DIRS:
        subprocess.run(["rm", "-rf", p])
        continue
    for root, _, files in os.walk(p):
        roots += [os.path.join(root, f) for f in files if f.endswith(".so")]

keep = close_over(roots)
if len(keep) < 5:
    sys.exit(f"FATAL: closure found only {len(keep)} Qt libs — refusing to prune")

dropped_libs = []
for f in sorted(present):
    if f not in keep:
        os.remove(os.path.join(LIB, f))
        dropped_libs.append(f)

for extra in ("qml", "translations", "qsci"):
    subprocess.run(["rm", "-rf", os.path.join(QT, extra)])
for extra in ("pip", "setuptools", "pkg_resources"):
    subprocess.run(["rm", "-rf", os.path.join(SP, extra)])

print(f"  dropped {len(dropped_mods)} PyQt6 modules and {len(dropped_libs)} Qt libs")
print(f"  kept {len(keep)}: {', '.join(sorted(keep))}")
