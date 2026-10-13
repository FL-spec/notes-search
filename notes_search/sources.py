"""Document sources: folders of text and Markdown files, JSON files, and Apple Notes."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".org", ".rst"}


@dataclass
class Document:
    """A searchable item, with the same fields Notes queries."""

    id: str
    title: str
    body: str = ""
    authors: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)  # e.g. image scene labels
    folder: str = ""
    modified: datetime | None = None
    created: datetime | None = None
    attachments: list[str] = field(default_factory=list)  # attachment display names
    # Indexed flags used by scope tokens: attachments, checklists, drawings, scans, tags,
    # locked, shared (Notes' _ICItemHas*/_ICItemIs* attributes).
    flags: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        for k in ("modified", "created"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Document":
        d = dict(d)
        for k in ("modified", "created"):
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


_CHECKLIST = re.compile(r"^\s*[-*] \[[ xX]\]", re.MULTILINE)
_TAG = re.compile(r"(?<![\w#])#\w+")
_MD_FILE = re.compile(r"!?\[[^\]]*\]\((?!https?:)([^)]+)\)")


def text_flags(text: str, attachments: list[str]) -> list[str]:
    flags = []
    if attachments:
        flags.append("attachments")
    if _CHECKLIST.search(text):
        flags.append("checklists")
    if _TAG.search(text):
        flags.append("tags")
    return flags


def _title_and_body(text: str, fallback: str) -> tuple[str, str]:
    # Like Notes, the first non-empty line is the title.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped, "\n".join(lines[i + 1 :])
    return fallback, ""


def from_folder(root: str | os.PathLike) -> Iterator[Document]:
    root = Path(root).expanduser()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title, body = _title_and_body(text, path.stem)
        st = path.stat()
        attachments = [Path(m).name for m in _MD_FILE.findall(text)]
        yield Document(
            id=str(path),
            title=title,
            body=body,
            folder=str(path.parent.relative_to(root)) if path.parent != root else "",
            attachments=attachments,
            flags=text_flags(text, attachments),
            modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            created=datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime), tz=timezone.utc),
        )


def from_json(path: str | os.PathLike) -> Iterator[Document]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    items = data["documents"] if isinstance(data, dict) else data
    for i, item in enumerate(items):
        item.setdefault("id", str(i))
        item.setdefault("title", "")
        yield Document.from_json(item)


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "div", "p", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    return p.text()


_JXA_EXPORT = r"""
function run() {
  const Notes = Application("Notes");
  const all = Notes.notes;
  const get = (f, fallback) => { try { return f(); } catch (e) { return fallback; } };
  const n = all.id().length;
  const ids = all.id(), names = all.name();
  const mods = get(() => all.modificationDate(), []), cres = get(() => all.creationDate(), []);
  const locked = get(() => all.passwordProtected(), []);
  const shared = get(() => all.shared(), []);
  // Bulk container lookups fail silently, so map note ids per account and folder.
  const folderOf = {}, accountOf = {};
  get(() => Notes.accounts().forEach(a => {
    const an = a.name();
    a.folders().forEach(f => {
      const fn = f.name();
      get(() => f.notes.id(), []).forEach(id => { folderOf[id] = fn; accountOf[id] = an; });
    });
  }), null);
  const atts = get(() => all.attachments.name(), []);
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({id: ids[i], title: names[i],
      folder: folderOf[ids[i]] || "", account: accountOf[ids[i]] || "",
      modified: mods[i] ? mods[i].toISOString() : null,
      created: cres[i] ? cres[i].toISOString() : null,
      attachments: atts[i] || [], locked: !!locked[i], shared: !!shared[i]});
  }
  // Bodies are fetched one by one so a locked note cannot fail the whole batch.
  for (let i = 0; i < n; i++) {
    out[i].html = out[i].locked ? "" : get(() => all[i].body(), "");
  }
  return JSON.stringify(out);
}
"""


def from_apple_notes(timeout: int = 1800) -> Iterator[Document]:
    """Export notes through JavaScript for Automation (bulk property reads).

    macOS asks once for permission for your terminal to control Notes. Locked notes are
    returned without their contents, as Notes does for Spotlight.
    """
    proc = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA_EXPORT],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"Apple Notes export failed: {proc.stderr.strip()}")
    for item in json.loads(proc.stdout):
        body_html = item.pop("html", "")
        body = html_to_text(body_html)
        name = item["title"]
        if body.startswith(name):
            body = body[len(name):].lstrip()
        attachments = [a for a in item.get("attachments") or [] if a]
        flags = text_flags(body, attachments)
        if item.get("locked"):
            flags.append("locked")
        if item.get("shared"):
            flags.append("shared")
        if 'type="checkbox"' in body_html or "checklist" in body_html:
            flags.append("checklists")
        yield Document(
            id=item["id"], title=name, body=body, folder=item.get("folder") or "",
            modified=_iso(item.get("modified")), created=_iso(item.get("created")),
            attachments=attachments, flags=sorted(set(flags)),
        )


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def load(sources: Iterable[str]) -> list[Document]:
    docs: list[Document] = []
    for src in sources:
        if src == "apple-notes":
            docs.extend(from_apple_notes())
        elif src.endswith(".json"):
            docs.extend(from_json(src))
        else:
            docs.extend(from_folder(src))
    return docs
