"""Text extraction from bundle documents (.docx, .msg, plain text).

All extraction is lossy-tolerant: a file that cannot be read returns ``None``
and the caller flags it — conversion never fails on a single bad document.
"""

from __future__ import annotations

import email
import email.policy
import re

import mammoth


def norm_name(name: str) -> str:
    """Normalise a filename for punctuation-insensitive matching.

    ``'E-mail from Marcus.docx'`` and ``'Manager_e-mail.docx'`` both normalise
    to strings containing ``'email'``.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def docx_text(path: str) -> str | None:
    """Raw text of a .docx, or None if unreadable."""
    try:
        with open(path, "rb") as f:
            return str(mammoth.extract_raw_text(f).value)
    except Exception:
        return None


def msg_text(path: str) -> str | None:
    """Body text of a .msg, or None if unreadable.

    The bundles' .msg files are MIME/RFC-822 messages despite the extension
    (not Outlook OLE2), so the stdlib parses them. Callers re-emit them as
    .txt in references/ because a .msg is not openable by sandbox tooling.
    """
    try:
        with open(path, errors="replace") as fh:
            m = email.message_from_file(fh, policy=email.policy.default)
        body: str | None = None
        if m.is_multipart():
            for part in m.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_content()
                    break
        else:
            body = m.get_content()
        if body is None:
            return None
        head = [f"{k}: {m.get(k)}" for k in ("From", "To", "Subject") if m.get(k)]
        return ("\n".join(head) + "\n\n" + body).strip()
    except Exception:
        return None


def clean_email(text: str) -> str:
    """Strip an email's header block: drop everything through the last header
    label/value found in the first 16 lines. The body proper is never edited.

    Note the "value may sit on the following non-empty line" rule means the
    first non-empty line after the header block — in practice the salutation
    ("Priya,") — is consumed with the headers. This is the behaviour the
    accountable ME-A-01 run shipped with and is relied on by its recorded
    outputs; do not change it without re-baselining every bundle."""
    lines = [line.rstrip() for line in text.splitlines()]
    hdr = re.compile(r"^(From|To|Subject|Cc|Date|Sent)\s*:", re.I)
    cut = 0
    for i, line in enumerate(lines[:16]):
        if hdr.match(line):
            cut = i + 1
            # header value may sit on the following non-empty line
            j = i + 1
            while j < min(len(lines), i + 3) and not lines[j]:
                j += 1
            if j < len(lines) and lines[j] and not hdr.match(lines[j]):
                cut = j + 1
    txt = "\n".join(lines[cut:])
    return re.sub(r"\n{3,}", "\n\n", txt).strip()
