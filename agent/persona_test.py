"""persona_test — display names come from the roster by id, and a broken roster never silences anyone.

    python3 agent/persona_test.py

Pins both halves of agent/lib/persona.py (and its shell twin persona.sh): a persona renamed in the
roster is shown by its new name, and an unknown id, a missing roster or a corrupt one gives the id
capitalized rather than raising or returning nothing.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
spec = importlib.util.spec_from_file_location("persona", LIB / "persona.py")
persona = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persona)

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


def use(*paths: Path) -> None:
    """Point the lookup at exactly these files, nothing on this machine."""
    persona._candidates = lambda: list(paths)
    persona._CACHE = None


ROSTER = {"version": 1, "personas": [
    {"id": "moses", "name": "Moses", "emoji": ":scroll:", "title": "Manager"},
    {"id": "birdeye", "name": "Hawk", "emoji": ":owl:", "title": "Watch"},
]}

tmp = Path(tempfile.mkdtemp(prefix="persona_test."))
good = tmp / "roster.json"
good.write_text(json.dumps(ROSTER), encoding="utf-8")
corrupt = tmp / "corrupt.json"
corrupt.write_text('{"personas": [ {"id": "bigpipe", "name": ', encoding="utf-8")
missing = tmp / "nope.json"

real_candidates = persona._candidates

# ── Renamed persona ──────────────────────────────────────────────────────────
use(good)
check("RENAME the roster's name is shown, not the literal", persona.name_of("birdeye") == "Hawk",
      persona.name_of("birdeye"))
check("RENAME the roster's emoji", persona.emoji_of("birdeye") == ":owl:", persona.emoji_of("birdeye"))
check("RENAME the roster's title", persona.title_of("birdeye") == "Watch", persona.title_of("birdeye"))

# ── Unknown id ───────────────────────────────────────────────────────────────
check("UNKNOWN an id not in the roster gives the id capitalized", persona.name_of("nobody") == "Nobody",
      persona.name_of("nobody"))
check("UNKNOWN emoji is neutral, never empty", persona.emoji_of("nobody") == ":robot_face:",
      persona.emoji_of("nobody"))
check("UNKNOWN a caller's fallback is honored", persona.name_of("nobody", "Someone") == "Someone")

# ── Missing roster ───────────────────────────────────────────────────────────
use(missing)
check("MISSING no roster gives the id capitalized", persona.name_of("bigpipe") == "Bigpipe",
      persona.name_of("bigpipe"))
check("MISSING nothing to search at all still answers", (use(), persona.name_of("tagilla"))[1] == "Tagilla")

# ── Corrupt roster ───────────────────────────────────────────────────────────
use(corrupt)
try:
    got = persona.name_of("bigpipe")
    check("CORRUPT a malformed roster gives the id capitalized", got == "Bigpipe", got)
except Exception as e:  # noqa: BLE001
    check("CORRUPT a malformed roster does not raise", False, f"{type(e).__name__}: {e}")

# ── A rename is picked up without a restart ──────────────────────────────────
use(good)
persona.name_of("birdeye")
edited = json.loads(json.dumps(ROSTER))
edited["personas"][1]["name"] = "Falcon"
good.write_text(json.dumps(edited), encoding="utf-8")
st = good.stat()
os.utime(good, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
check("RELOAD an edited roster is read again", persona.name_of("birdeye") == "Falcon",
      persona.name_of("birdeye"))
good.write_text(json.dumps(ROSTER), encoding="utf-8")
os.utime(good, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

# ── The real search order: $MOSES_ROSTER wins ────────────────────────────────
persona._candidates = real_candidates
persona._CACHE = None
os.environ["MOSES_ROSTER"] = str(good)
check("ORDER $MOSES_ROSTER is searched first", persona.name_of("birdeye") == "Hawk",
      persona.name_of("birdeye"))
del os.environ["MOSES_ROSTER"]


# ── The shell twin ───────────────────────────────────────────────────────────
def sh(roster: Path, fn: str, pid: str) -> str:
    env = {**os.environ, "MOSES_ROSTER": str(roster)}
    r = subprocess.run(["bash", "-c", f'. "{LIB}/persona.sh"; {fn} {pid}'],
                       capture_output=True, text=True, env=env, timeout=20)
    return r.stdout


check("SHELL the roster's name is shown", sh(good, "persona_name", "birdeye") == "Hawk",
      sh(good, "persona_name", "birdeye"))
check("SHELL the roster's emoji", sh(good, "persona_emoji", "birdeye") == ":owl:")
check("SHELL an unknown id gives the id capitalized", sh(good, "persona_name", "nobody") == "Nobody",
      sh(good, "persona_name", "nobody"))
check("SHELL a corrupt roster gives the id capitalized", sh(corrupt, "persona_name", "bigpipe") == "Bigpipe",
      sh(corrupt, "persona_name", "bigpipe"))

failed = results.count(False)
print(f"\n  {len(results) - failed} passed, {failed} failed")
sys.exit(1 if failed else 0)
