"""Builds the release archive, dist/TownfallCompanion-<version>.zip, from the last commit, and checks it.

    python tools/build-package.py [--repo-url <url, or "" for none>] [--out <folder>]

The archive holds one folder, TownfallCompanion, as it is unzipped into ...\\Win64\\ue4ss\\Mods\\: the mod and
the companion exactly as committed (uncommitted changes are left out), plus the players' documents: README,
LICENSE, third-party notices, phone setup and troubleshooting. Never in it: what each PC makes (companion.ini,
cache, tools), developer files, files from the game, programs. The archive isn't written if it breaks one of
those rules.

Next to it goes TownfallCompanion-<version>-nexus-description.txt: the same README in BBCode, to paste into the
Nexus page's description, so the two never differ.
"""
import argparse
import io
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "TownfallCompanion" / "companion"))
import config  # noqa: E402  (VERSION)

MOD = "TownfallCompanion"
REPO_URL = "https://github.com/smaugikun/silent-hill-townfall-companion"
# In the repository -> in the archive. The mod folder goes in as it is.
DOCS = {
    "README.md": f"{MOD}/README.md",
    "LICENSE": f"{MOD}/LICENSE",
    "THIRD_PARTY_NOTICES.md": f"{MOD}/THIRD_PARTY_NOTICES.md",
    "docs/PHONE_SETUP.md": f"{MOD}/docs/PHONE_SETUP.md",
    "docs/TROUBLESHOOTING.md": f"{MOD}/docs/TROUBLESHOOTING.md",
}
REQUIRED = ["enabled.txt", "Scripts/main.lua", "companion/bridge.py", "companion/config.py", "companion/run.bat",
            "companion/static/index.html", "companion/static/monster/lunger.webp", "Start Companion.bat",
            "Convert Game Videos.bat", *DOCS.values()]  # lunger: also any monster the phone doesn't know
PROGRAMS = (".exe", ".dll", ".com", ".msi", ".scr", ".ps1", ".vbs", ".jar")
GAME_FILES = (".bk2", ".bik", ".mp4", ".avi", ".wav", ".ogg", ".bank", ".pak", ".utoc", ".ucas", ".uasset", ".usmap")
MADE_ON_THE_PC = (f"{MOD}/companion.ini", f"{MOD}/cache/", f"{MOD}/tools/")
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
OUR_ART = (f"{MOD}/companion/static/crtv/", f"{MOD}/companion/static/monster/")  # the device, the monsters


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def committed_files():
    """{path in the archive: bytes} from HEAD, line endings as .gitattributes says (CRLF for .bat)."""
    tar = git("archive", "--format=tar", "HEAD", "--", MOD, *DOCS)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        for member in archive.getmembers():
            if member.isfile():
                files[DOCS.get(member.name, member.name)] = archive.extractfile(member).read()
    return files


def bbcode_inline(text, repo_url):
    """Markdown's inline marks as BBCode: links (relative ones to the repository), `code`, **bold**, *italic*."""
    def link(match):
        label, target = match.groups()
        if re.match(r"https?://", target):
            return f"[url={target}]{label}[/url]"
        if repo_url:
            return f"[url={repo_url.rstrip('/')}/blob/main/{target}]{label}[/url]"
        return f"{label} ({target.split('#')[0]} in the download)"
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"`([^`]+)`", r"[font=Courier New]\1[/font]", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"[b]\1[/b]", text)
    return re.sub(r"(?<![*\w])\*([^*]+?)\*(?![*\w])", r"[i]\1[/i]", text)


def nexus_description(readme, repo_url):
    """The README as BBCode for the Nexus description field, so the two say the same. Nexus keeps line
    breaks, so a paragraph or list item that the Markdown wraps becomes one line."""
    out, block, kind = [], [], None  # the paragraph ("p") or list ("ul", "ol") being read

    def flush():
        nonlocal block, kind
        if kind == "p":
            out.append(bbcode_inline(" ".join(block), repo_url))
        elif kind:
            items = "\n".join(f"[*]{bbcode_inline(item, repo_url)}" for item in block)
            out.append(f"[list{'=1' if kind == 'ol' else ''}]\n{items}\n[/list]")
        block, kind = [], None

    for line in readme.splitlines():
        item = re.match(r"(- |\d+\. )(.*)", line)
        if not line.strip():
            flush()
        elif line.startswith("#"):
            flush()
            title = bbcode_inline(line.lstrip("#").strip(), repo_url)
            out.append(f"[center][size=6][b]{title}[/b][/size][/center]" if line.startswith("# ")
                       else f"[line]\n[size=5][b]{title}[/b][/size]")
        elif item:
            if kind != ("ul" if item.group(1) == "- " else "ol"):
                flush()
                kind = "ul" if item.group(1) == "- " else "ol"
            block.append(item.group(2))
        elif kind in ("ul", "ol") and line.startswith(" "):
            block[-1] += " " + line.strip()
        else:
            if kind != "p":
                flush()
                kind = "p"
            block.append(line.strip())
    flush()
    return "\n\n".join(out) + "\n"


def problems(files):
    """What makes this archive unfit for release."""
    found = []
    for path, data in files.items():
        low = path.lower()
        if not path.startswith(f"{MOD}/"):
            found.append(f"outside the mod folder: {path}")
        if low.startswith(tuple(p.lower() for p in MADE_ON_THE_PC)) or "__pycache__" in low or low.endswith(".pyc"):
            found.append(f"made on a PC, not shipped: {path}")
        if low.endswith(PROGRAMS):
            found.append(f"a program: {path}")
        if low.endswith(GAME_FILES):
            found.append(f"a game file type: {path}")
        if low.endswith(IMAGES) and not low.startswith(tuple(p.lower() for p in OUR_ART)):
            found.append(f"an image outside the mod's art: {path}")
        if low.endswith(".bat") and b"\n" in data.replace(b"\r\n", b""):
            found.append(f"a .bat without CRLF line endings: {path}")
    found += [f"missing: {MOD}/{name}" for name in REQUIRED if f"{MOD}/{name}" not in files and name not in files]
    return found


def main():
    parser = argparse.ArgumentParser(description="Builds and checks the release archive.")
    parser.add_argument("--repo-url", default=REPO_URL,
                        help="the public repository the Nexus description links the docs to (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="where the archive goes (default: dist)")
    args = parser.parse_args()
    if git("status", "--porcelain").strip():
        print("Note: there are uncommitted changes; the archive is built from the last commit without them.")

    files = committed_files()
    found = problems(files)
    if found:
        raise SystemExit("Not built:\n  " + "\n  ".join(found))

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{MOD}-{config.VERSION}.zip"
    stamp = tuple(int(v) for v in git("log", "-1", "--format=%cd", "--date=format:%Y %m %d %H %M %S").split())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, stamp)  # the commit's time: the same commit makes the same archive
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[path])
    commit = git("rev-parse", "--short", "HEAD").decode().strip()
    print(f"{target} ({target.stat().st_size // 1024} KB, {len(files)} files, commit {commit})")
    description = args.out / f"{MOD}-{config.VERSION}-nexus-description.txt"
    description.write_text(nexus_description(files[f"{MOD}/README.md"].decode("utf-8"), args.repo_url), encoding="utf-8")
    print(f"{description} (the README in BBCode, for the Nexus description field)")


if __name__ == "__main__":
    main()
