"""Build the one-page résumé PDF from the website.

Reads the Experience, Education and Stack sections of ../index.html, combines
them with the photo and the short intro below, renders resume_template.html,
and prints it to PDF with a local Microsoft Edge or Google Chrome (headless).

Usage (from anywhere):
    python tools/build_resume.py            # writes assets/resume/Morere Philippe - resume.pdf
    python tools/build_resume.py --html     # also keeps the intermediate HTML for inspection

Only the Python standard library is needed (pypdf is used, if installed, to
check the result fits on one page).
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # the website folder
SITE = ROOT / "index.html"
TEMPLATE = Path(__file__).resolve().parent / "resume_template.html"
PHOTO = ROOT / "assets" / "img" / "philippe.jpg"
OUT = ROOT / "assets" / "resume" / "Morere Philippe - resume.pdf"

# ---------------------------------------------------------------- settings
NAME = "Philippe Morere"
TITLE = "Senior Machine Learning Engineer · AI agents &amp; automation"
CONTACT = [
    ("philippe.morere@gmail.com", "mailto:philippe.morere@gmail.com"),
    ("linkedin.com/in/philippemorere", "https://www.linkedin.com/in/philippemorere"),
    ("github.com/PhilippeMorere", "https://github.com/PhilippeMorere"),
    ("philippemorere.github.io", "https://philippemorere.github.io"),
]
# Short version of the LinkedIn "About" section.
INTRO = (
    "Senior Machine Learning Engineer at <b>Canva</b>, building and deploying an AI agent that edits Canva "
    "documents, from the agent harness to its evaluation and production. Previously <b>Chief Data Scientist "
    "and Tech Lead</b> for over four years, leading teams and delivering ML projects end to end for clients. "
    "My focus is using <b>AI agents, LLMs and machine learning to automate manual work</b>, reliably and "
    "measurably, in production. <b>PhD in Machine Learning</b> from the University of Sydney."
)
LANGUAGES = "French (native) · English (bilingual)"


# ------------------------------------------------------- tiny HTML tree
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs or {}), parent, []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def walk(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.walk()

    def find_all(self, tag=None, cls=None, id=None):
        return [n for n in self.walk()
                if (tag is None or n.tag == tag) and (cls is None or cls in n.classes)
                and (id is None or n.attrs.get("id") == id)]

    def find(self, *a, **k):
        r = self.find_all(*a, **k)
        return r[0] if r else None

    def kids(self, tag=None):
        return [c for c in self.children if isinstance(c, Node) and (tag is None or c.tag == tag)]

    def text(self):
        out = "".join(c if isinstance(c, str) else c.text() for c in self.children)
        return re.sub(r"\s+", " ", out).strip()

    def inline_html(self, keep=("b", "strong", "em", "i")):
        """Inner HTML keeping only simple emphasis tags; links become plain text."""
        parts = []
        for c in self.children:
            if isinstance(c, str):
                parts.append(html.escape(c, quote=False))
            elif c.tag in keep:
                parts.append(f"<{c.tag}>{c.inline_html(keep)}</{c.tag}>")
            elif c.tag != "a":
                parts.append(c.inline_html(keep))
        return re.sub(r"\s+", " ", "".join(parts)).strip()


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#root")

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(path):
    b = TreeBuilder()
    b.feed(path.read_text(encoding="utf-8"))
    return b.root


# ------------------------------------------------------- extraction
def extract(root):
    exp = []
    section = root.find("section", id="experience")
    for li in section.find("ol", cls="timeline").kids("li"):
        meta, card = li.find("div", cls="meta"), li.find("div", cls="card")
        date = meta.find("span", cls="date")
        place = meta.find("span", cls="place")
        org = card.find("p", cls="org")
        paras = [p.inline_html() for p in card.kids("p") if "org" not in p.classes]
        lst = card.find("ul")
        exp.append({
            "date": date.text() if date else "",
            "place": place.text() if place else "",
            "title": card.find("h3").text(),
            "org": org.text() if org else "",
            "paras": paras,
            "bullets": [li2.inline_html() for li2 in lst.kids("li")] if lst else [],
        })

    edu, stack = [], []
    for card in root.find("section", cls="edu-strip").find_all("div", cls="edu"):
        chips = card.find("div", cls="chips")
        if chips:
            stack = [s.text() for s in chips.kids("span")]
            continue
        org = card.find("p", cls="org")
        desc = [p.inline_html() for p in card.kids("p") if "org" not in p.classes]
        edu.append({
            "years": card.find("span", cls="years").text(),
            "title": card.find("h3").text(),
            "org": org.text() if org else "",
            "desc": re.sub(r"\s*Publications\s*→\s*$", "", " ".join(desc)).strip(),
        })

    expertise = [h.text() for h in root.find("section", id="expertise").find_all("h3")]
    return exp, edu, stack, expertise


# ------------------------------------------------------- rendering
def render(exp, edu, stack, expertise):
    def row(left_main, left_sub, title, org, body):
        sub = f'<div class="sub">{left_sub}</div>' if left_sub else ""
        orgh = f' <span class="org">· {html.escape(org)}</span>' if org else ""
        return (f'<div class="row"><div class="when"><div class="date">{html.escape(left_main)}</div>{sub}</div>'
                f'<div class="what"><h3>{html.escape(title)}{orgh}</h3>{body}</div></div>')

    exp_html = []
    for e in exp:
        body = "".join(f"<p>{p}</p>" for p in e["paras"])
        if e["bullets"]:
            body += "<ul>" + "".join(f"<li>{b}</li>" for b in e["bullets"]) + "</ul>"
        exp_html.append(row(e["date"], html.escape(e["place"]), e["title"], e["org"], body))

    edu_html = [row(d["years"], "", d["title"], d["org"], f"<p>{d['desc']}</p>" if d["desc"] else "") for d in edu]

    contact = " <span class='sep'>·</span> ".join(f'<a href="{u}">{html.escape(t)}</a>' for t, u in CONTACT)
    values = {
        "NAME": NAME, "TITLE": TITLE, "CONTACT": contact, "INTRO": INTRO,
        "PHOTO": PHOTO.as_uri(),
        "EXPERIENCE": "\n".join(exp_html), "EDUCATION": "\n".join(edu_html),
        "EXPERTISE": " · ".join(html.escape(x) for x in expertise),
        "STACK": " · ".join(html.escape(s) for s in stack),
        "LANGUAGES": LANGUAGES,
    }
    out = TEMPLATE.read_text(encoding="utf-8")
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def find_browser():
    candidates = [
        os.environ.get("RESUME_BROWSER"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("msedge"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    sys.exit("No Edge/Chrome found. Set RESUME_BROWSER to the browser executable.")


def print_pdf(html_path, pdf_path):
    browser = find_browser()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_pdf = Path(tmp) / "resume.pdf"
        subprocess.run([
            browser, "--headless=new", "--disable-gpu", "--no-first-run",
            f"--user-data-dir={Path(tmp) / 'profile'}", "--no-pdf-header-footer",
            "--virtual-time-budget=8000",           # let web fonts load
            f"--print-to-pdf={tmp_pdf}", html_path.as_uri(),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if not tmp_pdf.exists() or tmp_pdf.stat().st_size == 0:
            sys.exit("The browser did not produce a PDF.")
        try:
            shutil.copyfile(tmp_pdf, pdf_path)
        except PermissionError:
            sys.exit(f"Could not overwrite {pdf_path}: close it in any PDF viewer and run again.")


def main():
    exp, edu, stack, expertise = extract(parse(SITE))
    page = render(exp, edu, stack, expertise)
    build = Path(__file__).resolve().parent / ".build"
    build.mkdir(exist_ok=True)
    html_path = build / "resume.html"
    html_path.write_text(page, encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print_pdf(html_path, OUT)
    msg = f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)"
    try:
        from pypdf import PdfReader
        n = len(PdfReader(OUT).pages)
        msg += f", {n} page{'s' * (n != 1)}"
        if n > 1:
            msg += "  <- WARNING: more than one page"
    except ImportError:
        pass
    print(msg)
    if "--html" in sys.argv:
        print(f"HTML kept at {html_path}")
    else:
        html_path.unlink()


if __name__ == "__main__":
    main()
