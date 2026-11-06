#!/usr/bin/env python3
"""MF8: brad's drift check and daily self-test read the tracker moses_env names (moses-data since the
cutover), never the stale corpus copy beside the notes. Each runs in a child process with the location
given ONLY in the settings file, as on Reserve. Then each fix is reverted and its check must turn red.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = r'''
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("m", sys.argv[1])
spec = importlib.util.spec_from_loader("m", loader); m = importlib.util.module_from_spec(spec)
loader.exec_module(m); print(m.REGISTRY)
'''


def registry_of(script: Path) -> str:
    d = Path(tempfile.mkdtemp())
    (d / "moses.env").write_text(f"MOSES_REGISTRY={d}/data/projects.json\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOSES_")}
    env.update(MOSES_SETTINGS=str(d / "moses.env"), HOME=str(d))
    try:
        r = subprocess.run([sys.executable, "-c", PROBE, str(script)], capture_output=True, text=True, env=env)
    finally:
        shutil.rmtree(d)
    out = r.stdout.strip().splitlines()
    return (out[-1] if out else "error: " + r.stderr[-300:]), str(d / "data/projects.json")


def check(script: Path) -> bool:
    got, want = registry_of(script)
    return got == want


SCRIPTS = {"moses-drift": "REGISTRY = _env.REGISTRY",
           "moses-selftest": 'REGISTRY = Path(os.environ.get("MOSES_REGISTRY_LIVE") or _env.REGISTRY)'}
OLD = {"moses-drift": 'REGISTRY = Path(os.environ.get("MOSES_REGISTRY", str(Path.home() / ".claude" / "memory" / "projects.json")))',
       "moses-selftest": 'REGISTRY = Path(os.environ.get("MOSES_REGISTRY_LIVE", HOME / ".claude/memory/projects.json"))'}

fails = 0
for name, fixed in SCRIPTS.items():
    src = HERE / name
    ok = check(src)
    print(f"  {'PASS' if ok else 'FAIL'}  {name} reads the tracker the settings file names")
    fails += not ok
    s = src.read_text(); assert fixed in s, name
    with tempfile.TemporaryDirectory() as t:
        m = Path(t) / name; m.write_text(s.replace(fixed, OLD[name]))
        red = not check(m)
    print(f"  {'watched failing' if red else 'FAIL guard not watched failing'}: {name} with the old corpus default")
    fails += not red
sys.exit(1 if fails else 0)
