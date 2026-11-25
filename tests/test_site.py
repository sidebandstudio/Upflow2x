"""The public site under docs/.

It is served straight from the repo by GitHub Pages, so nothing rebuilds it and
nothing catches it drifting. It described the removed Detect, Sequence, Regroup
and Upscale workflow for as long as that workflow had been gone.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

PAGES = ["index.html", "help.html", "download.html", "contact.html"]

# Tags that never carry a closing tag.
VOID = {"br", "img", "meta", "link", "input", "hr", "area", "base", "col",
        "embed", "source", "track", "wbr",
        # inline svg shapes used across the site
        "path", "circle", "line", "polyline", "rect", "ellipse", "polygon", "use"}


class Structure(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.mismatched: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            self.mismatched.append(tag)
            self.stack.remove(tag)


def page(repo_root: Path, name: str) -> str:
    return (repo_root / "docs" / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", PAGES)
def test_pages_are_well_formed(repo_root, name):
    parser = Structure()
    parser.feed(page(repo_root, name))
    assert not parser.stack, f"{name} leaves {parser.stack} unclosed"
    assert not parser.mismatched, f"{name} closes {parser.mismatched} out of order"


@pytest.mark.parametrize("name", PAGES)
def test_internal_links_point_at_pages_that_exist(repo_root, name):
    for href in re.findall(r'href="([^"#:]+\.html)"', page(repo_root, name)):
        assert (repo_root / "docs" / href).exists(), f"{name} links to missing {href}"


@pytest.mark.parametrize("name", PAGES)
def test_assets_referenced_exist(repo_root, name):
    # A ?v= on the stylesheet or script names a version, not a different file.
    for src in re.findall(r'(?:src|href)="((?:assets|css|js)/[^"?]+)', page(repo_root, name)):
        assert (repo_root / "docs" / src).exists(), f"{name} references missing {src}"


@pytest.mark.parametrize("name", PAGES)
def test_pages_have_no_byte_order_mark(repo_root, name):
    assert (repo_root / "docs" / name).read_bytes()[:3] != b"\xef\xbb\xbf"


# --------------------------------------------------------------------------
# the site has to describe the plugin that exists
# --------------------------------------------------------------------------

REMOVED = ["[DSU]", "Regroup", "Upscale + Interpolate", "sensitivity slider"]


@pytest.mark.parametrize("name", PAGES)
def test_the_site_does_not_document_removed_features(repo_root, name):
    text = page(repo_root, name)
    for gone in REMOVED:
        assert gone not in text, (
            f"{name} still documents {gone!r}, which the plugin no longer has"
        )


def test_the_help_page_names_the_buttons_the_panel_actually_has(repo_root):
    lua = (repo_root / "Installer" / "Eternal2x.lua").read_text(encoding="utf-8")
    help_text = page(repo_root, "help.html").lower()

    for _bid, raw in re.findall(
            r'ui:Button\{\s*ID\s*=\s*"(\w+)"\s*,\s*Text\s*=\s*"([^"]+)"', lua):
        label = re.sub(r'\\x[0-9A-Fa-f]{2}', "", raw).strip().lower()
        if not label or label == "reset":
            continue
        assert label in help_text, f"help.html never mentions the {label!r} button"


def test_the_help_page_covers_the_current_failure_messages(repo_root):
    """Every message the panel can show a user should be findable in the help."""
    help_text = page(repo_root, "help.html").lower()
    for message in ("plugin folder not configured", "could not reach resolve",
                    "nothing to do", "refresh progress"):
        assert message in help_text, f"help.html does not cover {message!r}"


def test_the_site_states_the_studio_requirement(repo_root):
    combined = " ".join(page(repo_root, n) for n in PAGES)
    assert "Studio" in combined


def test_the_download_page_reads_releases_from_the_right_repo(repo_root):
    from Installer.build_release import REPO
    assert f"'{REPO}'" in page(repo_root, "download.html"), (
        "the download page points at a different repository than releases are cut from"
    )


@pytest.mark.parametrize("name", PAGES)
def test_download_buttons_never_name_a_zip_the_release_may_not_have(repo_root, name):
    """Zips were renamed eternal2x-* to upflow2x-*, so a fixed link 404s on
    one side of that release. Buttons open the release page, and a script
    finds whichever zip it has."""
    assert "/releases/latest/download/" not in page(repo_root, name)


@pytest.mark.parametrize("name", PAGES)
def test_anchor_links_point_at_ids_that_exist(repo_root, name):
    for target, anchor in re.findall(r'href="([a-z]*\.html)?#([\w-]+)"', page(repo_root, name)):
        html = page(repo_root, target or name)
        assert f'id="{anchor}"' in html, f"{name} links to {target or name}#{anchor}, which has no such id"


@pytest.mark.parametrize("name", PAGES)
def test_the_site_only_promises_files_the_release_ships(repo_root, name):
    """The site told users to double-click an installer exe and app that no
    release has ever contained."""
    from Installer.build_release import PAYLOAD

    text = page(repo_root, name)
    for promised in ("Eternal2xInstaller", ".app installer", "installer app", "one-click"):
        assert promised.lower() not in text.lower(), f"{name} mentions {promised!r}"
    for script in re.findall(r"Installer[/\\]+(\w+\.py)", text):
        assert "Installer" in PAYLOAD and (repo_root / "Installer" / script).exists(), (
            f"{name} tells users to run Installer/{script}, which is not shipped"
        )


def test_primary_downloads_are_standalone_setup_and_manual_steps_are_separate(repo_root):
    from Installer.build_release import INSTALLERS

    download = page(repo_root, "download.html")
    assert set(re.findall(r'data-asset="([^"]+)"', download)) == set(INSTALLERS)
    home = page(repo_root, "index.html")
    assert set(re.findall(r'data-asset="([^"]+)"', home)) == set(INSTALLERS)
    help_text = page(repo_root, "help.html")
    for section in ("windows", "macos"):
        instructions = re.search(r'<section id="' + section + r'".*?</section>', help_text, re.S).group()
        assert "click Install" in instructions
        assert "gui_installer.py" not in instructions
        assert "to PATH" not in instructions
    assert 'id="manual"' in help_text


class Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.form: dict = {}
        self.fields: dict[str, dict] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.form = a
        elif tag in ("input", "textarea", "select") and a.get("name"):
            self.fields[a["name"]] = a


def test_the_contact_page_sends_the_message_itself(repo_root):
    """Visitors write to us on the page and it lands in the inbox. The page
    used to hand them an address and a list of what to put in the email."""
    parsed = Form()
    parsed.feed(page(repo_root, "contact.html"))
    form, fields = parsed.form, parsed.fields

    # Every message goes to the studio inbox. Web3Forms delivers to the address
    # its access key was made for, hello@sideband.studio, and answers in about
    # a second where FormSubmit took 20 to 40.
    assert form.get("action") == "https://api.web3forms.com/submit"
    assert form.get("method", "").lower() == "post"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        fields["access_key"].get("value", "")), "the access key is missing"
    # Web3Forms replies to the field called email.
    assert fields["email"].get("type") == "email" and "required" in fields["email"]
    assert "required" in fields["message"]
    # Bots fill every field; people never see this one, and Web3Forms drops
    # anything that arrives with it filled.
    assert "botcheck" in fields
    assert fields["subject"].get("value")
    assert "formsubmit" not in page(repo_root, "contact.html").lower()
    # A bug report asks what the old checklist asked for.
    for question in ("resolve version", "plugin version", "computer", "panel said"):
        assert question in fields, f"the bug report never asks for {question!r}"


def test_the_film_credits_hero_under_it(repo_root):
    """The film is cut from Hero, which is CC BY 4.0. The end card credits it,
    but a visitor who never plays the film never sees that."""
    film = re.search(r'<figure class="panel film">(.*?)</figure>', page(repo_root, "index.html"), re.S)
    assert film, "index.html has no film"
    caption = re.search(r"<figcaption>(.*?)</figcaption>", film.group(1), re.S)
    assert caption, "nothing under the film credits Hero"
    assert 'href="https://studio.blender.org/projects/hero/"' in caption.group(1)
    assert "Blender Foundation" in caption.group(1)
    assert 'href="https://creativecommons.org/licenses/by/4.0/"' in caption.group(1)


@pytest.mark.parametrize("name", PAGES)
def test_pages_use_no_em_dashes(repo_root, name):
    text = page(repo_root, name)
    assert "\u2014" not in text and "&mdash;" not in text, f"{name} contains an em dash"


# --------------------------------------------------------------------------
# sharing
# --------------------------------------------------------------------------

SITE = "https://upflow2x.com/"
# The same file is uploaded by hand as the GitHub repo's social preview.
SOCIAL = "assets/social-preview.png"


class Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.canonical = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")):
            self.meta[a.get("property") or a.get("name")] = a.get("content", "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href", "")


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_the_social_preview_fits_github(repo_root):
    """GitHub asks for 1280x640 and under 1 MB, and a 2:1 card is what X shows."""
    image = repo_root / "docs" / SOCIAL
    assert png_size(image) == (1280, 640)
    assert image.stat().st_size < 1_000_000

    # The page it is drawn from sits beside it, and has to redraw anywhere.
    source = image.with_suffix(".html").read_text(encoding="utf-8")
    assert not re.search(r"[A-Z]:[\\/]|/Users/|/home/|file:///\w", source), "a machine's own path"
    for ref in re.findall(r'(?:src|href)="(?!https://)([^"]+)"', source):
        assert (image.parent / ref).exists(), f"the source uses missing {ref}"


@pytest.mark.parametrize("name", PAGES)
def test_every_page_shares_the_preview_with_absolute_urls(repo_root, name):
    """Link previews fetch these URLs from somewhere else, so a relative one
    shows nothing, and without alt text a screen reader gets nothing."""
    head = Head()
    head.feed(page(repo_root, name))
    meta = head.meta

    assert head.canonical.startswith(SITE)
    assert meta["og:url"] == head.canonical
    assert meta["og:image"] == meta["twitter:image"] == SITE + SOCIAL
    assert meta["twitter:card"] == "summary_large_image"
    width, height = png_size(repo_root / "docs" / SOCIAL)
    assert (meta["og:image:width"], meta["og:image:height"]) == (str(width), str(height))
    assert meta["og:image:type"] == "image/png"
    assert meta["og:image:alt"] and meta["og:image:alt"] == meta["twitter:image:alt"]


def test_pages_load_the_same_version_of_the_stylesheet_and_script(repo_root):
    """The ?v= tag is how a browser knows a file changed. A page left on an
    older tag can keep showing an older copy after the others moved on."""
    for asset in ("css/site.css", "js/site.js"):
        tags = {name: re.findall(re.escape(asset) + r"\?v=(\w+)", page(repo_root, name))
                for name in PAGES}
        assert len({t for found in tags.values() for t in found}) == 1, f"{asset}: {tags}"


# --------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------

def test_the_free_edition_limit_names_the_resolve_release_it_began_with(repo_root):
    """Resolve Free blocks script panels from 19.1, not in every release."""
    for name in PAGES:
        text = re.sub(r"<[^>]+>", "", page(repo_root, name))
        for sentence in re.findall(r"[^.]*free edition[^.]*(?:\.\d[^.]*)*", text, re.I):
            assert "19.1" in sentence, f"{name}: {sentence.strip()!r}"


def test_the_mac_certificate_workaround_runs_the_app_setup_builds(repo_root):
    """Setup v0.5.1 can't verify its Python download on a Mac without
    python.org's Python 3.13 and its certificates. The help
    and the README tell those users how to start it with Apple's own
    certificates, which only works if the path names the real executable."""
    build = (repo_root / "Installer" / "build_installer.py").read_text(encoding="utf-8")
    app = re.search(r'name = "(\w+)"', build).group(1)
    command = f"SSL_CERT_FILE=/etc/ssl/cert.pem ~/Downloads/{app}.app/Contents/MacOS/{app}"

    help_text = page(repo_root, "help.html")
    answer = re.search(r'<details id="mac-certificate">.*?</details>', help_text, re.S)
    assert answer and command in answer.group()
    assert "CERTIFICATE_VERIFY_FAILED" in answer.group()
    assert command in (repo_root / "README.md").read_text(encoding="utf-8")
    assert 'href="help.html#mac-certificate"' in page(repo_root, "download.html")


NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 8: "eight", 10: "ten"}


def test_the_docs_describe_stop_open_log_and_try_again_as_the_code_has_them(repo_root):
    """0.5.2 changed Stop, Show in Folder and setup's Install button. The help
    and the README say so, and say what 0.5.1 does instead for the people
    still on it."""
    lua = (repo_root / "Installer" / "Eternal2x.lua").read_text(encoding="utf-8")
    setup = (repo_root / "Installer" / "gui_installer.py").read_text(encoding="utf-8")
    seconds = int(re.search(r"STOP_CONFIRM_FOR = (\d+)", lua).group(1))
    assert '"Open Log"' in lua and 'text="Try Again"' in setup

    for name, text in (("help.html", page(repo_root, "help.html")),
                       ("README.md", (repo_root / "README.md").read_text(encoding="utf-8"))):
        flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\*\*", "", text))
        windows = re.findall(r"second press within (\w+) seconds", flat)
        assert windows and set(windows) == {NUMBERS[seconds]}, f"{name}: {windows}"
        assert "In 0.5.1 one press stops it" in flat, name
        assert "Open Log" in flat and "Try Again" in flat, name
        assert "setup v0.5.1 still labels Install" in flat, name


def test_the_change_notes_leave_availability_to_the_download_page(repo_root):
    """After a tag, the release workflow publishes and then commits a new
    update/latest.json, and nothing edits the help page. So its notes say what
    a version does, and the download page, which asks GitHub, says what's out."""
    help_text = page(repo_root, "help.html")
    notes = re.search(r'<h3 id="changes-[\w-]+">Changes in [\d.]+</h3>(.*?)</section>', help_text, re.S)
    assert notes and 'href="download.html"' in notes.group(1)
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", help_text)).lower()
    for claim in ("not released", "unreleased", "not out yet", "still give", "coming soon"):
        assert claim not in flat, f"help.html says {claim!r}, which a release makes false"


# --------------------------------------------------------------------------
# the panel's two views
# --------------------------------------------------------------------------
#
# In 0.5.2 the main panel has the clip, Upscaling, Interpolate and Render, and
# Settings opens the rest until Back. These check the docs against that agreed
# layout. The button tests above check them against Installer/Eternal2x.lua.

SETTINGS_VIEW = ("Analyse", "Threshold", "Output", "Save to", "Check for Updates")


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\*\*", "", text))


def readme(repo_root) -> str:
    return (repo_root / "README.md").read_text(encoding="utf-8")


def test_render_needs_no_analyse_and_marking_stays_optional(repo_root):
    """Render finds the drawings on a clip with no markers, so the everyday
    steps go straight to Render, and Analyse comes after them as optional."""
    usage = re.search(r'<section id="usage".*?</section>', page(repo_root, "help.html"), re.S).group()
    steps, after = usage.split("</ol>", 1)
    headings = re.findall(r"<h3>(.*?)</h3>", steps)
    assert "Render" in headings
    assert not [h for h in headings if re.search(r"analyse|threshold", h, re.I)], headings
    assert "Analyse" in after and "optional" in after.lower()

    using = readme(repo_root).split("## Using it")[1].split("\n### Settings")[0]
    quick = re.match(r"\s*((?:\d+\. .*\n(?: {3}.*\n)*)+)", using).group(1)
    assert "Render" in quick and not re.search(r"Analyse|Threshold", quick), quick
    assert "Analyse" in using and "optional" in using.lower()


@pytest.mark.parametrize("doc", ["help.html", "README.md"])
def test_settings_names_its_controls_the_way_back_and_both_versions(repo_root, doc):
    """In 0.5.2 Settings holds the controls off the main panel, and Back
    returns. 0.5.1 has them all on the main panel, so both versions appear."""
    if doc == "help.html":
        section = re.search(r'<section id="settings".*?</section>', page(repo_root, doc), re.S).group()
    else:
        section = readme(repo_root).split("\n### Settings\n")[1].split("\n###")[0]
    text = plain(section)
    missing = [control for control in SETTINGS_VIEW if control.lower() not in text.lower()]
    assert not missing, f"{doc} Settings never names {missing}"
    assert re.search(r"\bback\b[^.]*\bmain panel\b", text, re.I), f"{doc} never says how to get back"
    assert "0.5.1" in text and "0.5.2" in text


@pytest.mark.parametrize("doc", ["help.html", "README.md"])
def test_the_render_chime_reads_as_new_off_and_timeline_only(repo_root, doc):
    """The chime is new in 0.5.2, so the line about 0.5.1's main panel can't
    take it along, and it plays only once a render is on the timeline."""
    if doc == "help.html":
        section = re.search(r'<section id="settings".*?</section>', page(repo_root, doc), re.S).group()
    else:
        section = readme(repo_root).split("\n### Settings\n")[1].split("\n###")[0]
    sentences = re.split(r"(?<=\.) (?=[A-Z0-9])", plain(section))
    old = [s for s in sentences if "0.5.1" in s]
    assert old and all(re.search(r"\bno render chime\b", s, re.I) for s in old), old
    # Its own entry is the last mention in both.
    chime = plain(section).lower().rsplit("render chime", 1)[-1]
    assert "0.5.2" in chime and "off until" in chime and "timeline" in chime, chime


# --------------------------------------------------------------------------
# the download page's release list
# --------------------------------------------------------------------------
#
# Rows are written into the page so it still lists downloads without script or
# with GitHub's API rate limited, and a row goes in before the release after it
# ships. The page once called 0.5.1 an older version while it was the newest.
#
# The release workflow publishes a release, then commits update/latest.json.
# In between, /releases/latest is the new version and the feed still names the
# old one, so only GitHub's release list may say which version is newest.

def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def written_rows(html: str) -> list[dict]:
    body = re.search(r'<tbody id="older-rows">(.*?)</tbody>', html, re.S).group(1)
    rows = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        rows.append({
            "version": cells[0],
            "cells": [re.sub(r"<[^>]+>", "", c) for c in cells],
            "links": [m.group(1) if (m := re.search(r'href="([^"]+)"', c)) else None
                      for c in cells],
        })
    return rows


def written_assets(html: str) -> list[str]:
    return re.findall(r'data-asset="([^"]+)"', html)


def release_cases(html: str) -> tuple[str, str]:
    """The newest written-in version, and the one that would follow it."""
    current = max((r["version"] for r in written_rows(html)), key=version_key)
    major, minor, patch = version_key(current)
    return current, f"{major}.{minor}.{patch + 1}"


def zip_url(version: str, name: str) -> str:
    from Installer.build_release import REPO
    return f"https://github.com/{REPO}/releases/download/v{version}/{name}"


def test_without_script_the_release_list_mislabels_no_version(repo_root):
    """With no script the page can't tell whether 0.5.1 is still the newest,
    so whatever it calls the list has to hold either way."""
    html = page(repo_root, "download.html")
    title = re.search(r'<h2 id="older-title">(.*?)</h2>', html).group(1)
    rows = written_rows(html)
    current, following = release_cases(html)

    for latest in (current, following):
        if "older" in title.lower():
            newer = [r["version"] for r in rows
                     if version_key(r["version"]) >= version_key(latest)]
            assert not newer, f"{title!r} lists {newer} with {latest} the newest"

    # The current release's zips stay downloadable from the list, which is
    # what manual installs use once the next release ships.
    row = next(r for r in rows if r["version"] == current)
    assert row["links"][2:] == [zip_url(current, "upflow2x-win.zip"),
                                zip_url(current, "upflow2x-mac.zip")]


# Runs the page's own script against a stand-in for the few DOM calls it
# makes, with GitHub's answers chosen by each case.
HARNESS = r"""
const PAGE = %(page)s, ANSWERS = %(answers)s, SCRIPT = %(script)s;
const API = 'https://api.github.com/repos/%(repo)s/releases';
const FEED = 'https://raw.githubusercontent.com/%(repo)s/main/update/latest.json';

class Cell {
    constructor(text = '', href = null) {
        this.text = text; this.className = '';
        this.anchor = href ? { href, textContent: text } : null;
    }
    get textContent() { return this.anchor ? this.anchor.textContent : this.text; }
    set textContent(v) { this.text = v; this.anchor = null; }
    appendChild(a) { this.anchor = a; }
}
class Row {
    constructor(cells = []) { this.cells = cells; }
    insertCell() { const c = new Cell(); this.cells.push(c); return c; }
}
class Body {
    constructor(rows) { this.rows = rows; }
    insertRow() { const r = new Row(); this.rows.push(r); return r; }
    removeChild(r) {
        const i = this.rows.indexOf(r);
        if (i < 0) throw new Error('not a row of this body');
        this.rows.splice(i, 1);
        return r;
    }
    set textContent(v) { if (v !== '') throw new Error('unexpected text'); this.rows = []; }
}

const body = new Body(PAGE.rows.map(r => new Row(r.cells.map((t, i) => new Cell(t, r.links[i])))));
const title = { textContent: PAGE.title };
const tags = PAGE.tags.map(t => ({ textContent: t, classList: { remove() {} } }));
const links = PAGE.assets.map(a => ({ asset: a, href: 'release page', lastChild: { nodeValue: a },
                                      getAttribute: n => n === 'data-asset' ? a : null }));
const odd = [], fetched = [];
const document = {
    getElementById: id => ({ 'older-rows': body, 'older-title': title })[id] || null,
    querySelectorAll(sel) {
        if (sel === '[data-version]') return tags;
        if (sel === '[data-asset]') return links;
        odd.push('selector ' + sel);
        return [];
    },
    createElement: () => ({ href: '', textContent: '' }),
};
function fetch(url) {
    fetched.push(url === API ? 'api' : url === FEED ? 'feed' : url);
    const answer = url === API ? ANSWERS.api : url === FEED ? ANSWERS.feed : (odd.push('fetch ' + url), null);
    if (!answer) return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve({ ok: answer.status === 200, status: answer.status,
                             json: () => Promise.resolve(answer.body) });
}

new Function('document', 'fetch', SCRIPT)(document, fetch);
setTimeout(() => console.log(JSON.stringify({
    title: title.textContent,
    rows: body.rows.map(r => ({ version: r.cells[0].textContent,
                               links: r.cells.slice(2).map(c => c.anchor && c.anchor.href) })),
    tags: tags.map(t => t.textContent),
    downloads: links.map(l => l.href),
    fetched,
    odd,
})), 50);
"""


def released(version: str) -> dict:
    return {
        "tag_name": f"v{version}", "draft": False, "prerelease": False,
        "published_at": "2026-11-10T23:27:40Z",
        "assets": [{"name": n, "browser_download_url": zip_url(version, n)}
                   for n in ("upflow2x-win.zip", "upflow2x-mac.zip",
                             "Upflow2xInstaller.exe", "Upflow2xInstaller-mac.zip")],
    }


def run_release_list(repo_root, tmp_path, api, feed) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node.js to run the download page's script")
    from Installer.build_release import REPO

    html = page(repo_root, "download.html")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1, "download.html should have one inline script"
    state = {
        "title": re.search(r'<h2 id="older-title">(.*?)</h2>', html).group(1),
        "rows": written_rows(html),
        "tags": re.findall(r"<span[^>]*data-version[^>]*>(.*?)</span>", html),
        "assets": written_assets(html),
    }
    harness = tmp_path / "release_list.js"
    harness.write_text(HARNESS % {
        "page": json.dumps(state), "answers": json.dumps({"api": api, "feed": feed}),
        "script": json.dumps(scripts[0]), "repo": REPO,
    }, encoding="utf-8")
    done = subprocess.run([node, str(harness)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert not result["odd"], result["odd"]
    return result


RATE_LIMITED = {"status": 403, "body": {"message": "API rate limit exceeded"}}


@pytest.mark.parametrize("newest", ["current", "next"])
def test_the_release_list_calls_only_earlier_versions_older(repo_root, tmp_path, newest):
    """Once GitHub's release list names the newest version, the heading says
    Older versions and lists what came before it. "next" is the publication
    window: the release is out and the feed still names the one before it,
    which keeps its row and zips."""
    html = page(repo_root, "download.html")
    written = [r["version"] for r in written_rows(html)]
    current, following = release_cases(html)
    latest = current if newest == "current" else following

    listed = ([] if newest == "current" else [latest]) + written
    api = {"status": 200, "body": [released(v) for v in listed]}
    stale_feed = {"status": 200, "body": {"version": current}}
    result = run_release_list(repo_root, tmp_path, api, stale_feed)

    assert result["title"] == "Older versions"
    assert [r["version"] for r in result["rows"]] == [v for v in written if v != latest]
    assert all(tag.startswith(f"v{latest} ") for tag in result["tags"])
    assert result["downloads"] == [zip_url(latest, a) for a in written_assets(html)]
    if newest == "next":
        row = next(r for r in result["rows"] if r["version"] == current)
        assert row["links"] == [zip_url(current, "upflow2x-win.zip"),
                                zip_url(current, "upflow2x-mac.zip")]
    assert result["fetched"] == ["api"]


@pytest.mark.parametrize("api", ["rate limited", "unreachable"])
@pytest.mark.parametrize("feed", ["current", "next"])
def test_without_the_release_list_the_page_stays_as_written(repo_root, tmp_path, api, feed):
    """Without GitHub's release list nothing on the page can say which version
    is newest, whatever the feed says. During the publication window the feed
    still names the release before /releases/latest. So the heading stays
    neutral, every written-in zip stays, the version tags keep their own text
    and the buttons keep opening the newest release on GitHub."""
    html = page(repo_root, "download.html")
    current, following = release_cases(html)
    answers = {"rate limited": RATE_LIMITED, "unreachable": None}[api]
    version = current if feed == "current" else following
    result = run_release_list(repo_root, tmp_path, answers,
                              {"status": 200, "body": {"version": version}})

    assert result["title"] == re.search(r'<h2 id="older-title">(.*?)</h2>', html).group(1)
    assert "older" not in result["title"].lower()
    assert result["rows"] == [{"version": r["version"], "links": r["links"][2:]}
                              for r in written_rows(html)]
    assert result["tags"] == re.findall(r"<span[^>]*data-version[^>]*>(.*?)</span>", html)
    assert result["downloads"] == ["release page"] * len(written_assets(html))
    assert result["fetched"] == ["api"]
