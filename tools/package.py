"""Builds the release archive, dist/TownfallCompanion-<version>.zip: the TownfallCompanion folder and the players'
documents as the last commit holds them, with tf_native.dll built from that commit's code.

    py tools/package.py

Needs a working tree without uncommitted changes (the archive is what the commit holds) and what native/build.py
needs (requirements-dev.txt). The version is VERSION in TownfallCompanion/companion/config.py.
"""
import hashlib
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DLL = "TownfallCompanion/Scripts/tf_native.dll"
# Besides the mod's folder, players get the notices and the guides the README links to (not its pictures: those
# are for the repository's and the mod page's README).
DOCUMENTS = ("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "docs/PHONE_SETUP.md",
             "docs/TROUBLESHOOTING.md", "docs/NATIVE_DLL.md")
REQUIRED = ("TownfallCompanion/enabled.txt", "TownfallCompanion/Start Companion.py",
            "TownfallCompanion/Scripts/main.lua", DLL, "LICENSE", "THIRD_PARTY_NOTICES.md")
# Never in a release: what each PC makes for itself, 1.x's tools and cache, and anything of the game's.
FORBIDDEN_NAMES = ("companion.ini", "tf_native.profile", "tf_native.tmp")
FORBIDDEN_FOLDERS = ("cache", "tools")
FORBIDDEN_SUFFIXES = (".pyc", ".exe", ".pdb", ".pak", ".ucas", ".utoc", ".uasset", ".uexp", ".bk2", ".bank", ".wem")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def chosen(commit="HEAD"):
    """The names in `commit` that go into the archive."""
    names = git("ls-tree", "-r", "--name-only", "-z", commit).decode("utf-8").split("\0")
    return [name for name in names if name.startswith("TownfallCompanion/") or name in DOCUMENTS]


def problems(names):
    """What is wrong with an archive of `names`: each a line; none, and it can go out."""
    found = [f"missing: {name}" for name in REQUIRED if name not in names]
    for name in sorted(names):
        parts = name.split("/")
        if (parts[-1] in FORBIDDEN_NAMES or name.lower().endswith(FORBIDDEN_SUFFIXES) or "__pycache__" in parts or
                (parts[0] == "TownfallCompanion" and len(parts) > 2 and parts[1] in FORBIDDEN_FOLDERS)):
            found.append(f"not for a release: {name}")
    return found


def main():
    sys.path.insert(0, str(ROOT / "TownfallCompanion" / "companion"))
    import config
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("Commit the changes first: the archive holds what the last commit does.")
    subprocess.run([sys.executable, str(ROOT / "native" / "build.py")], cwd=ROOT, check=True)
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("native/build.py changed committed files (native/profile.h): commit them first.")
    files = {name: git("show", f"HEAD:{name}") for name in chosen()}
    files[DLL] = (ROOT / DLL).read_bytes()
    found = problems(files)
    if found:
        sys.exit("Not packaged:\n  " + "\n  ".join(found))
    DIST.mkdir(exist_ok=True)
    archive = DIST / f"TownfallCompanion-{config.VERSION}.zip"
    stamp = time.gmtime(int(git("log", "-1", "--format=%ct")))[:6]  # the commit's time: the same commit, the same zip
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zipped.writestr(info, files[name])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"{archive.relative_to(ROOT)}: {len(files)} files, {archive.stat().st_size // 1024} KB\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
