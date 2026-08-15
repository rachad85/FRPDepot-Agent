"""Safety tests for the FRP Depot Packing Ring media-upload tool.

Everything here is offline. There is no Playwright, no CDP, no browser and no
network: the WordPress admin screens are modelled by a small fake DOM with a real
(if tiny) CSS matcher, and the public storefront is modelled by a fake urllib
opener, so the tool's OWN selectors, URL guards, scoping, ordering and refusal
logic are genuinely exercised rather than mocked away at the boundary.
TestFakeEngineIsHonest pins the matcher down -- a permissive fake would quietly
invalidate every scoping test in this file.

THE FAKES ARE ADVERSARIAL ON PURPOSE. Every screen carries controls the tool must
never touch: a second file input for a plugin ZIP, a second submit button, a
"Delete Permanently" row action on every media row and on the attachment screen,
and a bulk-action selector. If the tool ever reaches for "the file input" or "the
first link" instead of the exact fixed one, these tests fail rather than pass
quietly.

The fakes record every navigation, file selection and click, which is what lets
these tests assert the properties that actually matter: that the browser lane
mutex is held before the plan's permanent attempt lock exists, that a busy
browser costs Rachad nothing, that upload 4 failing stops upload 5, that the
three earlier uploads are still named in the record, and that nothing anywhere
can reach a seventh file, a product, a REST verb or a delete link.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wordpress_packing_ring_media_tool as media  # noqa: E402


# ---------------------------------------------------------------------------
# The browser lane lock is PRODUCTION state. The commissioned commands are
# decorated with it, so without this redirect a test run would take the real lock
# on the real authenticated browser -- blocking for 90 seconds if the other lane
# is mid-write, and reporting a busy browser as a catastrophic suite failure.
# Redirect once per module so this suite stays hermetic.
# ---------------------------------------------------------------------------
_LANE_LOCK_TMP = None
_LANE_LOCK_ORIGINAL_DIR = None


def setUpModule():
    global _LANE_LOCK_TMP, _LANE_LOCK_ORIGINAL_DIR

    import ui_lane_lock

    _LANE_LOCK_TMP = tempfile.TemporaryDirectory()
    _LANE_LOCK_ORIGINAL_DIR = ui_lane_lock.LOCK_DIR
    ui_lane_lock.LOCK_DIR = Path(_LANE_LOCK_TMP.name)


def tearDownModule():
    import ui_lane_lock

    if _LANE_LOCK_ORIGINAL_DIR is not None:
        ui_lane_lock.LOCK_DIR = _LANE_LOCK_ORIGINAL_DIR
    if _LANE_LOCK_TMP is not None:
        _LANE_LOCK_TMP.cleanup()


ORIGIN = media.EXACT_ORIGIN
SOURCE_PATH = Path(media.__file__)
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT)
REAL_PLAN_DIR = Path(r"C:\FRPDepot\Dado\20_Working\wordpress_packing_ring_media_plans")
REAL_PLAN_BASELINE = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in REAL_PLAN_DIR.glob("*.json")
} if REAL_PLAN_DIR.exists() else {}


class Playwright:
    """Stand-ins for the Playwright exception classes, kept out of the builtins."""

    class TimeoutError(Exception):
        pass


# ===========================================================================
# A very small CSS engine: tag, .class, #id, [attr="v"], descendants.
# It supports exactly the selector vocabulary the tool uses, which is the point:
# if the tool ever reaches for a broader selector, these tests stop matching.
# ===========================================================================
_TOKEN = re.compile(
    r"^(?P<tag>[A-Za-z][\w-]*)?"
    r"(?P<rest>(?:\.[\w-]+|\#[\w-]+|\[[^\]]*\])*)$"
)
_PART = re.compile(r"\.([\w-]+)|\#([\w-]+)|\[([^\]]*)\]")
_ATTR = re.compile(r"^([\w-]+)(?:=(?:\"([^\"]*)\"|'([^']*)'))?$")


def _parse(token: str):
    matched = _TOKEN.match(token)
    if not matched:
        raise AssertionError(f"the fake CSS engine does not support selector {token!r}")
    tag = matched.group("tag")
    classes: set[str] = set()
    ident = None
    attrs: list[tuple[str, str | None]] = []
    for part in _PART.finditer(matched.group("rest") or ""):
        if part.group(1):
            classes.add(part.group(1))
        elif part.group(2):
            ident = part.group(2)
        else:
            found = _ATTR.match(part.group(3))
            if not found:
                raise AssertionError(f"unsupported attribute selector {part.group(3)!r}")
            value = found.group(2) if found.group(2) is not None else found.group(3)
            attrs.append((found.group(1), value))
    return tag, classes, ident, attrs


def _matches(element: "FakeElement", token: str) -> bool:
    tag, classes, ident, attrs = _parse(token)
    if tag and element.tag != tag:
        return False
    if ident and element.attrs.get("id") != ident:
        return False
    if not classes <= element.classes:
        return False
    for name, value in attrs:
        if name not in element.attrs:
            return False
        if value is not None and element.attrs[name] != value:
            return False
    return True


def _select(root: "FakeElement", selector: str) -> list["FakeElement"]:
    current = [root]
    for token in str(selector).split():
        found: list[FakeElement] = []
        for node in current:
            found.extend(child for child in node.descendants() if _matches(child, token))
        unique: list[FakeElement] = []
        for node in found:
            if all(node is not other for other in unique):
                unique.append(node)
        current = unique
    return current


class FakeElement:
    def __init__(self, tag, *, cls="", attrs=None, text="", children=(),
                 visible=True, on_click=None, on_set_files=None, on_input_value=None):
        self.tag = tag
        self.classes = set(str(cls).split())
        self.attrs = dict(attrs or {})
        if cls:
            self.attrs.setdefault("class", cls)
        self.text = text
        self.children = list(children)
        self.visible = bool(visible)
        self.on_click = on_click
        self.on_set_files = on_set_files
        self.on_input_value = on_input_value

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        parts = [self.text] + [child.inner_text() for child in self.children]
        return " ".join(part for part in parts if part).strip()

    def query_selector_all(self, selector):
        return _select(self, selector)

    def query_selector(self, selector):
        found = self.query_selector_all(selector)
        return found[0] if found else None

    def is_visible(self):
        return self.visible

    @staticmethod
    def _require_timeout(timeout, what):
        if timeout is None:
            raise AssertionError(f"{what} was attempted without an explicit bounded timeout")

    def click(self, timeout=None):
        self._require_timeout(timeout, "a click")
        if self.on_click is None:
            raise AssertionError(f"clicked an element with no behaviour: <{self.tag}>")
        self.on_click()

    def set_input_files(self, path, timeout=None):
        self._require_timeout(timeout, "a file selection")
        if self.on_set_files is None:
            raise AssertionError("set_input_files on an element that is not the fixed file input")
        self.on_set_files(path)

    def input_value(self, timeout=None):
        self._require_timeout(timeout, "a value read-back")
        if self.on_input_value is None:
            raise AssertionError("input_value on an element that is not a form control")
        return self.on_input_value()


def _element(tag, **kwargs):
    return FakeElement(tag, **kwargs)


# ===========================================================================
# The fake WordPress site
# ===========================================================================
class FakeAttachment:
    def __init__(self, attachment_id, filename, data=b"", *, stored_name=None,
                 url=None, filetype=None, url_fields=None,
                 filename_boxes=1, filetype_boxes=1, public_missing=False):
        self.id = int(attachment_id)
        self.filename = stored_name or filename
        self.data = data
        self.url = url or f"{ORIGIN}/wp-content/uploads/2026/08/{self.filename}"
        self.filetype = filetype if filetype is not None else _filetype_box(self.filename)
        self.url_fields = url_fields
        self.filename_boxes = filename_boxes
        self.filetype_boxes = filetype_boxes
        # Models a media row whose original is gone from the public uploads tree:
        # the admin screen still describes it, but the anonymous GET 404s.
        self.public_missing = bool(public_missing)


def _filetype_box(filename):
    """What WordPress's own "File type" box says for a given stored name."""
    return {
        ".png": "File type: PNG",
        ".jpg": "File type: JPEG",
        ".jpeg": "File type: JPEG",
        ".gif": "File type: GIF",
        ".webp": "File type: WEBP",
        ".pdf": "File type: PDF",
    }.get(Path(str(filename)).suffix.casefold(), "File type: BIN")


class FakeSite:
    """An adversarial model of the three admin screens this tool may reach."""

    PER_PAGE = 20

    def __init__(self, attachments=(), *, start_url=None):
        self.attachments = list(attachments)
        self.current_url = start_url or f"{ORIGIN}/wp-admin/upload.php"
        self.navigations: list[str] = []
        self.clicks: list[str] = []
        self.selected_files: list[str] = []
        self.uploads = 0
        self.next_id = 5000
        self.pending_file: str | None = None

        # Knobs the tests turn.
        self.result_mode = "posted"        # "posted" | "list"
        self.fail_upload_at: int | None = None
        self.fail_mode = "rejected"        # "rejected" | "timeout" | "ambiguous"
        self.stored_name_suffix = ""       # e.g. "-1" to model a name collision
        self.count_text: str | None = None  # override ".displaying-num"
        self.second_count_text: str | None = None
        self.hide_count = False
        self.login_screen = False
        self.redirects: dict[str, str] = {}
        self.break_row_links = False
        self.hide_row_filenames = False
        self.parent_edit_links = False
        self.empty_placeholder = False

    # -- navigation ---------------------------------------------------------
    def goto(self, url):
        self.navigations.append(url)
        self.current_url = self.redirects.get(url, url)

    def _query(self):
        from urllib.parse import parse_qsl, urlsplit

        return dict(parse_qsl(urlsplit(self.current_url).query, keep_blank_values=True))

    def _path(self):
        from urllib.parse import urlsplit

        return urlsplit(self.current_url).path

    # -- rendering ----------------------------------------------------------
    def render(self):
        if self.login_screen:
            return _element("body", children=[
                _element("form", attrs={"id": "loginform"}, children=[
                    _element("input", attrs={"type": "text", "name": "log"}),
                ]),
            ])
        path = self._path()
        if path == media.MEDIA_NEW_PATH:
            return self._render_media_new()
        if path == media.UPLOAD_LIST_PATH:
            return self._render_list()
        if path == media.POST_EDIT_PATH:
            return self._render_attachment()
        return _element("body", children=[_element("h1", text="Some other screen")])

    def _decoys(self):
        """Controls a sloppier tool would grab. None of them may ever be used."""
        return [
            _element("input", attrs={"type": "file", "name": "pluginzip"},
                     on_set_files=lambda path: self._forbidden("plugin zip selected")),
            _element("input", attrs={"type": "submit", "name": "save"},
                     on_click=lambda: self._forbidden("a foreign submit was clicked")),
            _element("a", attrs={"href": f"{ORIGIN}/wp-admin/post.php?post=9&action=delete"},
                     text="Delete Permanently"),
            _element("a", attrs={"href": f"{ORIGIN}/wp-admin/edit.php?post_type=product"},
                     text="Products"),
            _element("select", attrs={"name": "action", "id": "bulk-action-selector-top"}),
        ]

    @staticmethod
    def _forbidden(what):
        raise AssertionError(f"the tool touched a forbidden control: {what}")

    def _render_media_new(self):
        return _element("body", children=[
            _element("form", attrs={"id": "file-form"}, children=[
                _element("input",
                         attrs={"type": "file", "name": "async-upload", "id": "async-upload"},
                         on_set_files=self._select_file),
                _element("input",
                         attrs={"type": "submit", "name": "html-upload", "value": "Upload"},
                         on_click=self._submit_upload),
            ]),
            *self._decoys(),
        ])

    def _select_file(self, path):
        self.selected_files.append(str(path))
        self.pending_file = str(path)

    def _submit_upload(self):
        self.clicks.append("html-upload")
        if self.pending_file is None:
            raise AssertionError("the upload was submitted without selecting a file")
        path = Path(self.pending_file)
        self.pending_file = None
        self.uploads += 1
        if self.fail_upload_at == self.uploads:
            if self.fail_mode == "timeout":
                raise Playwright.TimeoutError("upload timed out")
            if self.fail_mode == "ambiguous":
                # Two new rows appear: the result cannot be identified.
                for _ in range(2):
                    self._store(path)
                self.current_url = f"{ORIGIN}/wp-admin/upload.php"
                return
            # "rejected": WordPress bounced the file and created nothing.
            self.current_url = f"{ORIGIN}/wp-admin/upload.php"
            return
        created = self._store(path)
        if self.result_mode == "posted":
            self.current_url = f"{ORIGIN}/wp-admin/upload.php?posted={created.id}"
        else:
            self.current_url = f"{ORIGIN}/wp-admin/upload.php"

    def _store(self, path: Path):
        self.next_id += 1
        stored = f"{path.stem}{self.stored_name_suffix}{path.suffix}"
        created = FakeAttachment(self.next_id, path.name, path.read_bytes(),
                                 stored_name=stored)
        self.attachments.insert(0, created)
        return created

    def _render_list(self):
        page = int(self._query().get("paged") or 1)
        start = (page - 1) * self.PER_PAGE
        rows = []
        for item in self.attachments[start:start + self.PER_PAGE]:
            links = [] if self.break_row_links else [
                _element("a",
                         attrs={"href": f"{ORIGIN}/wp-admin/post.php?post={item.id}&action=edit"},
                         text=Path(item.filename).stem),
            ]
            name_nodes = [] if self.hide_row_filenames else [
                _element("p", cls="filename", text=f"File name: {item.filename}"),
            ]
            rows.append(_element("tr", attrs={"id": f"post-{item.id}"}, children=[
                _element("td", cls="title column-title", children=[
                    _element("strong", children=links),
                    *name_nodes,
                    _element("div", cls="row-actions", children=[
                        _element("span", cls="delete", children=[
                            _element("a",
                                     attrs={"href": f"{ORIGIN}/wp-admin/post.php?post={item.id}&action=delete"},
                                     text="Delete Permanently"),
                        ]),
                    ]),
                ]),
                *([_element("td", cls="parent column-parent", children=[
                    _element("a", attrs={
                        "href": f"{ORIGIN}/wp-admin/post.php?post={item.id + 100000}&action=edit"
                    }, text="Parent product"),
                ])] if self.parent_edit_links else []),
            ]))
        if self.empty_placeholder and not rows:
            rows.append(_element("tr", children=[
                _element("td", cls="colspanchange", text="No media items found."),
            ]))
        header = []
        if not self.hide_count:
            text = self.count_text if self.count_text is not None else f"{len(self.attachments)} items"
            header.append(_element("span", cls="displaying-num", text=text))
            if self.second_count_text is not None:
                header.append(_element("span", cls="displaying-num", text=self.second_count_text))
        return _element("body", children=[
            _element("div", cls="tablenav top", children=header),
            _element("table", cls="wp-list-table", children=[
                _element("tbody", attrs={"id": "the-list"}, children=rows),
            ]),
            *self._decoys(),
        ])

    def _render_attachment(self):
        wanted = int(self._query().get("post") or 0)
        found = [item for item in self.attachments if item.id == wanted]
        if not found:
            return _element("body", children=[_element("h1", text="Invalid post")])
        item = found[0]
        urls = item.url_fields if item.url_fields is not None else [item.url]
        url_nodes = []
        for index, value in enumerate(urls):
            attrs = {"type": "text", "name": "attachment_url", "readonly": "readonly"}
            if index == 0:
                attrs["id"] = "attachment_url"
            url_nodes.append(_element("input", cls="widefat urlfield", attrs=attrs,
                                      on_input_value=lambda value=value: value))
        children = [
            _element("div", cls="misc-pub-section misc-pub-attachment", children=url_nodes),
        ]
        for _ in range(item.filename_boxes):
            children.append(_element("div", cls="misc-pub-section misc-pub-filename",
                                     text=f"File name: {item.filename}"))
        for _ in range(item.filetype_boxes):
            children.append(_element("div", cls="misc-pub-section misc-pub-filetype",
                                     text=item.filetype))
        return _element("body", children=[*children, *self._decoys()])


class FakePage:
    """Renders lazily from the site model, so a click is visible immediately."""

    def __init__(self, site: FakeSite):
        self.site = site

    @property
    def url(self):
        return self.site.current_url

    def goto(self, url, wait_until=None, timeout=None):
        if timeout is None:
            raise AssertionError("navigation was attempted without an explicit bounded timeout")
        if wait_until is None:
            raise AssertionError("navigation was attempted without an explicit wait state")
        self.site.goto(url)

    def wait_for_load_state(self, state=None, timeout=None):
        if timeout is None:
            raise AssertionError("a load wait was attempted without an explicit bounded timeout")

    def query_selector_all(self, selector):
        return _select(self.site.render(), selector)

    def query_selector(self, selector):
        found = self.query_selector_all(selector)
        return found[0] if found else None


# ===========================================================================
# Fake public HTTP. The tool's REAL download guards run against this.
# ===========================================================================
class FakeResponse:
    def __init__(self, payload: bytes, *, status=200, content_type="image/png"):
        self._payload = payload
        self.status = status
        self.headers = {"Content-Type": content_type} if content_type else {}

    def read(self, size=None):
        return self._payload if size is None else self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeOpener:
    def __init__(self, site: FakeSite, handlers, *, status=200, content_type=None,
                 payload_override: bytes | None = None):
        self.site = site
        self.handlers = handlers
        self.status = status
        self.content_type = content_type
        self.payload_override = payload_override
        self.requests: list[str] = []

    def open(self, request, timeout=None):
        if timeout is None:
            raise AssertionError("a public download was attempted without a bounded timeout")
        url = request.full_url
        self.requests.append(url)
        for name in ("Authorization", "Cookie"):
            if request.get_header(name.capitalize()) is not None:
                raise AssertionError(f"the public download carried a {name} header")
        served = self.content_type if self.content_type is not None else self._served_type(url)
        if self.payload_override is not None:
            return FakeResponse(self.payload_override, status=self.status,
                                content_type=served)
        for item in self.site.attachments:
            if item.url == url and not item.public_missing:
                return FakeResponse(item.data, status=self.status, content_type=served)
        from urllib.error import HTTPError

        raise HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))

    @staticmethod
    def _served_type(url):
        """A real server serves each format's own type; the tool checks that."""
        from urllib.parse import urlsplit

        extension = Path(urlsplit(url).path).suffix.casefold()
        return media.IMAGE_CONTENT_TYPES.get(extension, ("application/octet-stream",))[0]


@contextlib.contextmanager
def fake_browser(site: FakeSite):
    """Replace only the session factory; AdminPage itself stays under test."""
    @contextlib.contextmanager
    def session():
        yield media.AdminPage(FakePage(site))

    with mock.patch.object(media, "admin_session", session):
        yield


@contextlib.contextmanager
def fake_network(site: FakeSite, **kwargs):
    made: list[FakeOpener] = []

    def build(*handlers):
        opener = FakeOpener(site, handlers, **kwargs)
        made.append(opener)
        return opener

    with mock.patch.object(media, "build_opener", build):
        yield made


# ===========================================================================
# Shared fixtures
# ===========================================================================
class ToolTestCase(unittest.TestCase):
    """Every test runs against a temporary plan folder and receipts file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.plan_dir = root / "plans"
        self.plan_dir.mkdir()
        self.receipts = root / "receipts.jsonl"
        patches = [
            mock.patch.object(media, "PLAN_DIR", self.plan_dir),
            mock.patch.object(media, "RECEIPTS", self.receipts),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    # -- helpers ------------------------------------------------------------
    def receipt_actions(self):
        if not self.receipts.exists():
            return []
        return [json.loads(line)["action"]
                for line in self.receipts.read_text(encoding="utf-8").splitlines() if line]

    def clean_duplicate_evidence(self, total=3, images=3):
        """A COMPLETE-gate evidence block: every image row hashed, none failed.

        The scope string is the tool's own DUPLICATE_SCOPE, not a fixture phrase,
        because load_plan requires that exact wording -- a plan cut under a
        bounded or sampled gate must not be committable.
        """
        return {
            "method": "authenticated media library list screen, read-only",
            "scope": media.DUPLICATE_SCOPE,
            "checked_utc": media.utc_now().isoformat(),
            "library_total": total,
            "enumerated": total,
            "pages_read": 1,
            "enumeration_complete": True,
            "image_rows": images,
            "image_hashes_completed": images,
            "hash_failures": 0,
            "hash_bytes_read": 1024 * images,
            "hash_complete": True,
            "complete": True,
            "name_conflicts": [],
            "hash_conflicts": [],
        }

    def stage_a_plan(self):
        integrity = media.verify_local_files()
        manifest = media.verify_manifest()
        return media.stage_plan(integrity, manifest, self.clean_duplicate_evidence())

    def library_with(self, filenames):
        items = []
        for index, name in enumerate(filenames, start=1):
            path = media.GALLERY_DIR / name
            data = path.read_bytes() if path.is_file() else b"other bytes"
            items.append(FakeAttachment(100 + index, name, data))
        return FakeSite(items)

    def commit(self, plan_path, approval=media.APPROVAL_WORD):
        return media.command_commit(argparse.Namespace(plan=str(plan_path), approval=approval))


# ===========================================================================
# The fake engine itself
# ===========================================================================
class TestFakeEngineIsHonest(unittest.TestCase):
    def test_selectors_are_not_permissive(self):
        tree = _element("body", children=[
            _element("input", attrs={"type": "file", "name": "async-upload"}),
            _element("input", attrs={"type": "file", "name": "pluginzip"}),
            _element("input", attrs={"type": "submit", "name": "html-upload"}),
            _element("div", cls="misc-pub-filename", text="File name: x.png"),
        ])
        self.assertEqual(len(_select(tree, 'input[type="file"][name="async-upload"]')), 1)
        self.assertEqual(len(_select(tree, 'input[type="submit"][name="html-upload"]')), 1)
        self.assertEqual(len(_select(tree, ".misc-pub-filename")), 1)
        self.assertEqual(len(_select(tree, "#the-list tr")), 0)

    def test_descendant_scoping_is_real(self):
        tree = _element("body", children=[
            _element("tbody", attrs={"id": "the-list"}, children=[_element("tr")]),
            _element("tr"),  # outside #the-list, must not match
        ])
        self.assertEqual(len(_select(tree, "#the-list tr")), 1)

    def test_actions_require_bounded_timeouts(self):
        node = _element("input", on_click=lambda: None)
        with self.assertRaises(AssertionError):
            node.click()


# ===========================================================================
# Fixed identity
# ===========================================================================
class TestFixedIdentity(ToolTestCase):
    def test_exactly_two_cli_actions_and_no_upload_parameters(self):
        parser = media.build_parser()
        actions = [action for action in parser._actions
                   if isinstance(action, argparse._SubParsersAction)]
        self.assertEqual(len(actions), 1)
        self.assertEqual(sorted(actions[0].choices), ["commit", "stage"])
        self.assertEqual(media.CLI_ACTIONS, ("stage", "commit"))

        stage_options = {option for action in actions[0].choices["stage"]._actions
                         for option in action.option_strings}
        self.assertEqual(stage_options, {"-h", "--help"})

        commit_options = {option for action in actions[0].choices["commit"]._actions
                          for option in action.option_strings}
        self.assertEqual(commit_options, {"-h", "--help", "--plan", "--approval"})

    def test_no_parameterised_upload_surface_anywhere(self):
        forbidden = {"--image", "--images", "--file", "--path", "--url", "--sha256",
                     "--hash", "--title", "--alt", "--attachment", "--product",
                     "--variation", "--selector", "--cdp", "--endpoint", "--site",
                     "--count", "--limit", "--only", "--order", "--action"}
        strings = {node.value for node in ast.walk(SOURCE_TREE)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertEqual(forbidden & strings, set())

    def test_six_constants_match_the_files_on_disk(self):
        self.assertEqual(len(media.FIXED_IMAGES), 6)
        self.assertEqual([record["position"] for record in media.FIXED_IMAGES],
                         [1, 2, 3, 4, 5, 6])
        for record in media.FIXED_IMAGES:
            path = media.GALLERY_DIR / record["filename"]
            data = path.read_bytes()
            self.assertEqual(len(data), record["bytes"], record["filename"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"],
                             record["filename"])

    def test_six_constants_match_the_approved_manifest(self):
        manifest = json.loads(media.MANIFEST_PATH.read_text(encoding="utf-8"))
        recorded = manifest["images"]
        self.assertEqual(len(recorded), 6)
        for record, expected in zip(recorded, media.FIXED_IMAGES):
            self.assertEqual(record["filename"], expected["filename"])
            self.assertEqual(record["sha256"], expected["sha256"])
            self.assertEqual(record["bytes"], expected["bytes"])
            self.assertEqual(record["width"], media.EXPECTED_WIDTH)
            self.assertEqual(record["height"], media.EXPECTED_HEIGHT)
            self.assertEqual(record["qc"], "approved")

    def test_live_files_are_png_rgb_1024(self):
        evidence = media.verify_local_files()
        self.assertEqual(len(evidence), 6)
        for record in evidence:
            self.assertEqual(record["format"], "PNG")
            self.assertEqual(record["mode"], "RGB")
            self.assertEqual((record["width"], record["height"]), (1024, 1024))
            self.assertTrue(record["regular_file"])
            self.assertFalse(record["symlink"])
            self.assertEqual(record["manifest_qc"], "approved")

    def test_review_sheet_and_zip_are_named_but_unreachable(self):
        self.assertNotIn("00_gallery_review_sheet.jpg", media.FIXED_FILENAMES)
        for name in media.NEVER_UPLOAD:
            with self.subTest(name=name), self.assertRaises(media.MediaUploadError):
                media.fixed_path(name)

    def test_arbitrary_and_source_photo_paths_are_unreachable(self):
        for name in ("img_d4cfe6c30990.jpeg", "07_extra.png", "../../evil.png",
                     "01_hero_three_quarter.PNG", "shell.php", "payload.svg"):
            with self.subTest(name=name), self.assertRaises(media.MediaUploadError):
                media.fixed_path(name)

    def test_fixed_stems_strip_the_wordpress_dedupe_suffix(self):
        self.assertEqual(media._normalise_stem("01_hero_three_quarter-1.png"),
                         "01_hero_three_quarter")
        self.assertEqual(media._normalise_stem("01_hero_three_quarter.png"),
                         "01_hero_three_quarter")
        self.assertIn("01_hero_three_quarter", media.FIXED_STEMS)


# ===========================================================================
# Local integrity -- every tamper fails closed
# ===========================================================================
class TestLocalIntegrity(ToolTestCase):
    """Each case copies the real gallery to a temp folder and breaks one thing."""

    def setUp(self):
        super().setUp()
        self.gallery = Path(self.tmp.name) / "gallery"
        shutil.copytree(media.GALLERY_DIR, self.gallery)
        patches = [
            mock.patch.object(media, "GALLERY_DIR", self.gallery),
            mock.patch.object(media, "MANIFEST_PATH", self.gallery / "manifest.json"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def manifest(self):
        return json.loads((self.gallery / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, value):
        (self.gallery / "manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def test_the_copy_verifies_before_any_tampering(self):
        self.assertEqual(len(media.verify_local_files()), 6)

    def test_altered_bytes_fail_closed(self):
        target = self.gallery / "03_low_side_angle.png"
        data = bytearray(target.read_bytes())
        data[-1] ^= 0xFF
        target.write_bytes(bytes(data))
        with self.assertRaises(media.MediaUploadError) as caught:
            media.verify_local_files()
        self.assertIn("SHA-256", str(caught.exception))

    def test_altered_size_fails_closed(self):
        target = self.gallery / "02_top_view.png"
        target.write_bytes(target.read_bytes() + b"\x00")
        with self.assertRaises(media.MediaUploadError) as caught:
            media.verify_local_files()
        self.assertIn("bytes", str(caught.exception))

    def test_missing_file_fails_closed(self):
        (self.gallery / "05_laminate_macro.png").unlink()
        with self.assertRaises(media.MediaUploadError):
            media.verify_local_files()

    def test_renamed_file_fails_closed(self):
        target = self.gallery / "06_edge_profile.png"
        target.rename(self.gallery / "06_edge_profile_v2.png")
        with self.assertRaises(media.MediaUploadError):
            media.verify_local_files()

    def test_wrong_dimensions_fail_closed(self):
        from PIL import Image

        target = self.gallery / "01_hero_three_quarter.png"
        with Image.open(target) as image:
            image.resize((512, 512)).save(target)
        with self.assertRaises(media.MediaUploadError):
            media.verify_local_files()

    def test_wrong_mode_fails_closed(self):
        from PIL import Image

        target = self.gallery / "01_hero_three_quarter.png"
        with Image.open(target) as image:
            image.convert("RGBA").save(target)
        with self.assertRaises(media.MediaUploadError):
            media.verify_local_files()

    def test_wrong_format_fails_closed(self):
        """A JPEG wearing a .png name is refused on format, not just on hash."""
        from PIL import Image

        target = self.gallery / "04_opposite_face.png"
        with Image.open(target) as image:
            image.save(target, format="JPEG")
        with self.assertRaises(media.MediaUploadError):
            media.verify_local_files()

    def test_unreadable_image_fails_closed(self):
        (self.gallery / "02_top_view.png").write_bytes(b"not a png at all")
        with self.assertRaises(media.MediaUploadError):
            media.verify_local_files()

    def test_disabled_decompression_bomb_ceiling_is_refused(self):
        from PIL import Image

        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", None):
            with self.assertRaises(media.MediaUploadError) as caught:
                media.verify_local_files()
        self.assertIn("decompression-bomb", str(caught.exception))

    def test_manifest_qc_downgrade_fails_closed(self):
        manifest = self.manifest()
        manifest["images"][2]["qc"] = "rejected"
        self.write_manifest(manifest)
        with self.assertRaises(media.MediaUploadError) as caught:
            media.verify_manifest()
        self.assertIn("qc", str(caught.exception))

    def test_manifest_hash_change_fails_closed(self):
        manifest = self.manifest()
        manifest["images"][0]["sha256"] = "0" * 64
        self.write_manifest(manifest)
        with self.assertRaises(media.MediaUploadError):
            media.verify_manifest()

    def test_manifest_reordering_fails_closed(self):
        manifest = self.manifest()
        manifest["images"][0], manifest["images"][1] = (manifest["images"][1],
                                                       manifest["images"][0])
        self.write_manifest(manifest)
        with self.assertRaises(media.MediaUploadError):
            media.verify_manifest()

    def test_manifest_extra_image_fails_closed(self):
        manifest = self.manifest()
        manifest["images"].append({"filename": "07_extra.png", "sha256": "0" * 64,
                                   "bytes": 1, "width": 1024, "height": 1024,
                                   "qc": "approved"})
        self.write_manifest(manifest)
        with self.assertRaises(media.MediaUploadError):
            media.verify_manifest()

    def test_manifest_subset_fails_closed(self):
        manifest = self.manifest()
        manifest["images"] = manifest["images"][:5]
        self.write_manifest(manifest)
        with self.assertRaises(media.MediaUploadError):
            media.verify_manifest()

    def test_extra_file_in_the_folder_is_simply_never_reachable(self):
        (self.gallery / "07_extra.png").write_bytes(b"junk")
        self.assertEqual(len(media.verify_local_files()), 6)
        with self.assertRaises(media.MediaUploadError):
            media.fixed_path("07_extra.png")

    def test_evidence_order_is_one_to_six(self):
        evidence = media.verify_local_files()
        self.assertEqual([item["position"] for item in evidence], [1, 2, 3, 4, 5, 6])
        self.assertEqual([item["filename"] for item in evidence],
                         list(media.FIXED_FILENAMES))


# ===========================================================================
# Approval
# ===========================================================================
class TestApproval(ToolTestCase):
    def test_only_the_exact_word_is_accepted(self):
        media.require_rachad_approval("APPROVED")
        for bad in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
                    "YES", "ok", "proceed", "", None, True, 1, ["APPROVED"]):
            with self.subTest(approval=bad), self.assertRaises(media.MediaUploadError):
                media.require_rachad_approval(bad)

    def test_wrong_approval_refuses_before_browser_network_and_lock(self):
        plan_path, plan = self.stage_a_plan()
        site = FakeSite()
        with fake_browser(site), fake_network(site) as openers:
            with self.assertRaises(media.MediaUploadError):
                self.commit(plan_path, approval="approved")
        self.assertEqual(site.navigations, [])
        self.assertEqual(site.selected_files, [])
        self.assertEqual(openers, [])
        self.assertFalse(media.lock_path(plan_path).exists())


# ===========================================================================
# Plans
# ===========================================================================
class TestPlans(ToolTestCase):
    def test_staged_plan_is_closed_hashed_and_dated(self):
        plan_path, plan = self.stage_a_plan()
        self.assertTrue(str(plan_path).startswith(str(self.plan_dir)))
        stored = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(set(stored) - {"sha256"}, media.PLAN_KEYS)
        core = dict(stored)
        self.assertEqual(media.digest_for({k: v for k, v in core.items() if k != "sha256"}),
                         stored["sha256"])
        created = media.datetime.fromisoformat(stored["created_utc"])
        expires = media.datetime.fromisoformat(stored["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(stored["approval_word"], "APPROVED")
        self.assertEqual(stored["risk"], media.RISK_DISCLOSURE)
        self.assertIn("NOT ATOMIC", stored["risk"])
        self.assertEqual(len(stored["nonce"]), 32)
        self.assertEqual(len(stored["images"]), 6)
        self.assertEqual([item["filename"] for item in stored["images"]],
                         list(media.FIXED_FILENAMES))
        self.assertIn("packing_ring_media_plan_staged", self.receipt_actions())

    def test_plan_round_trips(self):
        plan_path, _ = self.stage_a_plan()
        loaded = media.load_plan(plan_path)
        self.assertEqual(loaded["action"], media.ACTION)

    def test_tampered_plan_fails_the_hash_check(self):
        plan_path, _ = self.stage_a_plan()
        stored = json.loads(plan_path.read_text(encoding="utf-8"))
        stored["images"][0]["sha256"] = "0" * 64
        plan_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(media.MediaUploadError) as caught:
            media.load_plan(plan_path)
        self.assertIn("hash check", str(caught.exception))

    def test_rehashed_foreign_images_still_fail_on_identity(self):
        """Re-hashing a doctored plan cannot help: the six records are fixed."""
        plan_path, _ = self.stage_a_plan()
        stored = json.loads(plan_path.read_text(encoding="utf-8"))
        stored.pop("sha256")
        stored["images"][3]["filename"] = "00_gallery_review_sheet.jpg"
        stored["sha256"] = media.digest_for(stored)
        plan_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(media.MediaUploadError) as caught:
            media.load_plan(plan_path)
        self.assertIn("six fixed", str(caught.exception))

    def test_reordered_images_fail(self):
        plan_path, _ = self.stage_a_plan()
        stored = json.loads(plan_path.read_text(encoding="utf-8"))
        stored.pop("sha256")
        stored["images"] = list(reversed(stored["images"]))
        stored["sha256"] = media.digest_for(stored)
        plan_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(media.MediaUploadError):
            media.load_plan(plan_path)

    def test_extra_or_missing_schema_field_fails(self):
        for mutate in (lambda plan: plan.update({"extra": 1}),
                       lambda plan: plan.pop("provenance")):
            with self.subTest(mutate=mutate):
                plan_path, _ = self.stage_a_plan()
                stored = json.loads(plan_path.read_text(encoding="utf-8"))
                stored.pop("sha256")
                mutate(stored)
                stored["sha256"] = media.digest_for(stored)
                plan_path.write_text(json.dumps(stored), encoding="utf-8")
                with self.assertRaises(media.MediaUploadError):
                    media.load_plan(plan_path)

    def test_foreign_tool_version_or_origin_fails(self):
        for field, value in (("tool_version", "0.9.0"), ("tool", "Some Other Tool"),
                             ("origin", "https://example.com"), ("action", "product_update"),
                             ("cdp_endpoint", "http://127.0.0.1:9228"),
                             ("approval_word", "YES"), ("schema_version", 99)):
            with self.subTest(field=field):
                plan_path, _ = self.stage_a_plan()
                stored = json.loads(plan_path.read_text(encoding="utf-8"))
                stored.pop("sha256")
                stored[field] = value
                stored["sha256"] = media.digest_for(stored)
                plan_path.write_text(json.dumps(stored), encoding="utf-8")
                with self.assertRaises(media.MediaUploadError):
                    media.load_plan(plan_path)

    def test_expired_plan_fails(self):
        plan_path, _ = self.stage_a_plan()
        stored = json.loads(plan_path.read_text(encoding="utf-8"))
        stored.pop("sha256")
        created = media.utc_now() - timedelta(hours=25)
        stored["created_utc"] = created.isoformat()
        stored["expires_utc"] = (created + timedelta(hours=24)).isoformat()
        stored["sha256"] = media.digest_for(stored)
        plan_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(media.MediaUploadError) as caught:
            media.load_plan(plan_path)
        self.assertIn("expired", str(caught.exception))

    def test_stretched_lifetime_fails(self):
        plan_path, _ = self.stage_a_plan()
        stored = json.loads(plan_path.read_text(encoding="utf-8"))
        stored.pop("sha256")
        created = media.datetime.fromisoformat(stored["created_utc"])
        stored["expires_utc"] = (created + timedelta(days=30)).isoformat()
        stored["sha256"] = media.digest_for(stored)
        plan_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(media.MediaUploadError):
            media.load_plan(plan_path)

    def test_plan_outside_the_dedicated_folder_is_refused(self):
        outside = Path(self.tmp.name) / "elsewhere.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(media.MediaUploadError):
            media.resolve_plan_path(str(outside))

    def test_traversal_out_of_the_plan_folder_is_refused(self):
        sneaky = self.plan_dir / ".." / "elsewhere.json"
        with self.assertRaises(media.MediaUploadError):
            media.resolve_plan_path(str(sneaky))

    def test_symlinked_plan_path_is_refused(self):
        real = self.plan_dir / "real.json"
        real.write_text("{}", encoding="utf-8")
        link = Path(self.tmp.name) / "link.json"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        with self.assertRaises(media.MediaUploadError):
            media.resolve_plan_path(str(link))

    def test_a_reparse_point_is_refused_even_without_symlink_privilege(self):
        """The Windows attribute path, tested deterministically.

        Creating a real symlink needs a privilege this service account may not
        have, so the skip above is honest but not sufficient on its own. This
        pins the attribute branch itself.
        """
        import stat as stat_module

        real = self.plan_dir / "real.json"
        real.write_text("{}", encoding="utf-8")
        info = real.lstat()

        class ReparseStat:
            st_mode = info.st_mode
            st_file_attributes = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

        with mock.patch.object(Path, "lstat", lambda self: ReparseStat()):
            self.assertTrue(media._is_reparse_point(real))
            with self.assertRaises(media.MediaUploadError) as caught:
                media.resolve_plan_path(str(real))
        self.assertIn("reparse point", str(caught.exception))

    def test_plan_staged_without_a_clean_duplicate_check_cannot_be_loaded(self):
        for broken in ({"complete": False},
                       {"enumeration_complete": False},
                       {"hash_complete": False},
                       {"hash_failures": 1},
                       # A partial hash gate that still claims completeness: the
                       # counts must contradict it, not the flag be believed.
                       {"image_hashes_completed": 2, "image_rows": 3},
                       # The old bounded-sample wording, and any other scope text,
                       # is not committable by this build.
                       {"scope": "hash gate bounded to the newest 12 image rows"},
                       {"name_conflicts": [{"attachment_id": 5, "filename": "x", "stem": "x"}]},
                       {"hash_conflicts": [{"attachment_id": 5,
                                            "matches_fixed_image": "02_top_view.png"}]}):
            with self.subTest(broken=broken):
                integrity = media.verify_local_files()
                manifest = media.verify_manifest()
                evidence = {**self.clean_duplicate_evidence(), **broken}
                plan_path, _ = media.stage_plan(integrity, manifest, evidence)
                with self.assertRaises(media.MediaUploadError):
                    media.load_plan(plan_path)


# ===========================================================================
# URL guards
# ===========================================================================
class TestNavigationGuards(unittest.TestCase):
    def test_only_three_admin_paths(self):
        self.assertEqual(media.ALLOWED_ADMIN_PATHS,
                         frozenset({"/wp-admin/media-new.php", "/wp-admin/upload.php",
                                    "/wp-admin/post.php"}))

    def test_accepted_admin_urls(self):
        for url in (f"{ORIGIN}/wp-admin/media-new.php?browser-uploader",
                    f"{ORIGIN}/wp-admin/upload.php",
                    f"{ORIGIN}/wp-admin/upload.php?posted=4321",
                    f"{ORIGIN}/wp-admin/upload.php?mode=list&paged=7",
                    f"{ORIGIN}/wp-admin/post.php?post=4321&action=edit"):
            with self.subTest(url=url):
                media.assert_admin_url(url)

    def test_refused_admin_urls(self):
        for url in (
            "http://frpdepots.com/wp-admin/upload.php",
            "https://frpdepots.com:8443/wp-admin/upload.php",
            "https://www.frpdepots.com/wp-admin/upload.php",
            "https://evil.example/wp-admin/upload.php",
            "https://user:pass@frpdepots.com/wp-admin/upload.php",
            f"{ORIGIN}/wp-login.php",
            f"{ORIGIN}/wp-admin/plugins.php",
            f"{ORIGIN}/wp-admin/options-general.php",
            f"{ORIGIN}/wp-admin/edit.php?post_type=product",
            f"{ORIGIN}/wp-json/wp/v2/media",
            f"{ORIGIN}/wp-admin/post.php?post=5&action=delete",
            f"{ORIGIN}/wp-admin/post.php?post=5&action=trash",
            f"{ORIGIN}/wp-admin/post.php?post=5",
            f"{ORIGIN}/wp-admin/post.php?post=0&action=edit",
            f"{ORIGIN}/wp-admin/post.php?post=-3&action=edit",
            f"{ORIGIN}/wp-admin/post.php?post=5&action=edit&extra=1",
            f"{ORIGIN}/wp-admin/upload.php?mode=grid",
            f"{ORIGIN}/wp-admin/upload.php?mode=list&paged=1&s=secret",
            f"{ORIGIN}/wp-admin/upload.php?posted=abc",
            f"{ORIGIN}/wp-admin/upload.php?deleted=3",
            f"{ORIGIN}/wp-admin/media-new.php",
            f"{ORIGIN}/wp-admin/media-new.php?browser-uploader&post_id=7",
        ):
            with self.subTest(url=url), self.assertRaises(media.MediaUploadError):
                media.assert_admin_url(url)

    def test_library_page_is_bounded(self):
        media.library_page_url(media.MAX_LIBRARY_PAGES)
        for bad in (0, -1, media.MAX_LIBRARY_PAGES + 1):
            with self.subTest(page=bad), self.assertRaises(media.MediaUploadError):
                media.library_page_url(bad)

    def test_edit_link_parser_accepts_only_the_exact_shape(self):
        self.assertEqual(
            media.parse_attachment_edit_link(f"{ORIGIN}/wp-admin/post.php?post=88&action=edit"), 88)
        for bad in (f"{ORIGIN}/wp-admin/post.php?post=88&action=delete",
                    f"{ORIGIN}/wp-admin/post.php?post=88&action=edit&x=1",
                    f"{ORIGIN}/wp-admin/upload.php?posted=88",
                    "https://evil.example/wp-admin/post.php?post=88&action=edit",
                    "javascript:alert(1)", ""):
            with self.subTest(href=bad):
                self.assertIsNone(media.parse_attachment_edit_link(bad))

    def test_public_upload_url_guard(self):
        good = f"{ORIGIN}/wp-content/uploads/2026/08/01_hero_three_quarter.png"
        self.assertEqual(media.assert_public_upload_url(good), "01_hero_three_quarter.png")
        media.assert_public_upload_url(good, expected_basename="01_hero_three_quarter.png")
        legacy_root = f"{ORIGIN}/wp-content/uploads/legacy-original.webp"
        self.assertEqual(
            media.assert_public_upload_url(
                legacy_root, allowed_extensions=media.SCANNED_EXTENSIONS
            ),
            "legacy-original.webp",
        )
        for bad in (
            f"{ORIGIN}/wp-content/uploads/2026/08/01_hero_three_quarter.png?v=1",
            f"{ORIGIN}/wp-content/uploads/2026/08/../../../wp-config.php",
            f"{ORIGIN}/wp-content/uploads/legacy/nested.png",
            f"{ORIGIN}/wp-content/uploads/2026/08/shell.php",
            f"{ORIGIN}/wp-content/uploads/2026/08/x.svg",
            f"{ORIGIN}/wp-content/plugins/evil/x.png",
            "https://cdn.example/wp-content/uploads/2026/08/x.png",
            "http://frpdepots.com/wp-content/uploads/2026/08/x.png",
        ):
            with self.subTest(url=bad), self.assertRaises(media.MediaUploadError):
                media.assert_public_upload_url(bad)

    def test_wordpress_dedupe_suffix_is_refused_as_the_stored_name(self):
        with self.assertRaises(media.MediaUploadError) as caught:
            media.assert_public_upload_url(
                f"{ORIGIN}/wp-content/uploads/2026/08/01_hero_three_quarter-1.png",
                expected_basename="01_hero_three_quarter.png")
        self.assertIn("-N suffix", str(caught.exception))


# ===========================================================================
# The public verification download
# ===========================================================================
class TestPublicDownload(ToolTestCase):
    def payload(self):
        return (media.GALLERY_DIR / "02_top_view.png").read_bytes()

    def test_downloads_and_refuses_redirects_by_construction(self):
        site = FakeSite([FakeAttachment(1, "02_top_view.png", self.payload())])
        with fake_network(site) as openers:
            data = media.download_public_bytes(site.attachments[0].url)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         media.FIXED_IMAGES[1]["sha256"])
        self.assertEqual(len(openers), 1)
        self.assertTrue(any(isinstance(handler, media.NoRedirectHandler)
                            for handler in openers[0].handlers))

    def test_redirect_handler_never_builds_a_follow_up(self):
        self.assertIsNone(media.NoRedirectHandler().redirect_request(
            None, None, 302, "Found", {}, "https://evil.example/"))

    def test_foreign_host_is_refused_before_any_request(self):
        site = FakeSite()
        with fake_network(site) as openers:
            with self.assertRaises(media.MediaUploadError):
                media.download_public_bytes(
                    "https://evil.example/wp-content/uploads/2026/08/x.png")
        self.assertEqual(openers, [])

    def test_byte_bound_is_enforced(self):
        site = FakeSite([FakeAttachment(1, "02_top_view.png", self.payload())])
        with mock.patch.object(media, "MAX_DOWNLOAD_BYTES", 128):
            with fake_network(site):
                with self.assertRaises(media.MediaUploadError) as caught:
                    media.download_public_bytes(site.attachments[0].url)
        self.assertIn("read bound", str(caught.exception))

    def test_non_png_content_type_is_refused(self):
        site = FakeSite([FakeAttachment(1, "02_top_view.png", self.payload())])
        with fake_network(site, content_type="text/html"):
            with self.assertRaises(media.MediaUploadError):
                media.download_public_bytes(site.attachments[0].url)

    def test_non_200_is_refused(self):
        site = FakeSite([FakeAttachment(1, "02_top_view.png", self.payload())])
        with fake_network(site, status=302):
            with self.assertRaises(media.MediaUploadError):
                media.download_public_bytes(site.attachments[0].url)

    def test_missing_file_is_refused(self):
        site = FakeSite()
        with fake_network(site):
            with self.assertRaises(media.MediaUploadError):
                media.download_public_bytes(
                    f"{ORIGIN}/wp-content/uploads/2026/08/02_top_view.png")


# ===========================================================================
# Duplicate preflight
# ===========================================================================
class TestDuplicatePreflight(ToolTestCase):
    def evidence_for(self, site):
        with fake_network(site):
            return media.duplicate_preflight(media.AdminPage(FakePage(site)))

    def test_clean_library_passes(self):
        site = self.library_with(["unrelated_photo.png", "brochure.pdf"])
        evidence = self.evidence_for(site)
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["name_conflicts"], [])
        self.assertEqual(evidence["hash_conflicts"], [])
        media.require_no_duplicates(evidence)

    def test_exact_basename_conflict_refuses(self):
        site = self.library_with(["03_low_side_angle.png"])
        evidence = self.evidence_for(site)
        self.assertEqual(len(evidence["name_conflicts"]), 1)
        with self.assertRaises(media.DuplicateFound):
            media.require_no_duplicates(evidence)

    def test_wordpress_dedupe_variant_conflict_refuses(self):
        site = FakeSite([FakeAttachment(400, "01_hero_three_quarter-1.png", b"different")])
        evidence = self.evidence_for(site)
        self.assertEqual(len(evidence["name_conflicts"]), 1)
        with self.assertRaises(media.DuplicateFound):
            media.require_no_duplicates(evidence)

    def test_same_bytes_under_another_name_is_caught_by_the_hash_gate(self):
        data = (media.GALLERY_DIR / "05_laminate_macro.png").read_bytes()
        site = FakeSite([FakeAttachment(401, "totally_unrelated.png", data)])
        evidence = self.evidence_for(site)
        self.assertEqual(evidence["name_conflicts"], [])
        self.assertEqual(len(evidence["hash_conflicts"]), 1)
        self.assertEqual(evidence["hash_conflicts"][0]["matches_fixed_image"],
                         "05_laminate_macro.png")
        with self.assertRaises(media.DuplicateFound):
            media.require_no_duplicates(evidence)

    def test_incomplete_enumeration_fails_closed(self):
        site = self.library_with(["unrelated.png"])
        site.count_text = "999 items"
        evidence = self.evidence_for(site)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError) as caught:
            media.require_no_duplicates(evidence)
        self.assertIn("cannot be proven", str(caught.exception))

    def test_unreadable_item_count_fails_closed(self):
        site = self.library_with(["unrelated.png"])
        site.hide_count = True
        evidence = self.evidence_for(site)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_matching_top_and_bottom_item_counts_are_one_attestation(self):
        site = self.library_with(["unrelated.png"])
        site.second_count_text = "1 item"
        evidence = self.evidence_for(site)
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["library_total"], 1)

    def test_disagreeing_top_and_bottom_item_counts_fail_closed(self):
        site = self.library_with(["unrelated.png"])
        site.second_count_text = "2 items"
        evidence = self.evidence_for(site)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_parent_product_edit_link_does_not_ambiguate_attachment_identity(self):
        site = self.library_with(["unrelated.png"])
        site.parent_edit_links = True
        evidence = self.evidence_for(site)
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["enumerated"], 1)

    def test_standard_empty_table_placeholder_is_not_an_unidentified_media_row(self):
        site = self.library_with([])
        site.empty_placeholder = True
        admin = media.AdminPage(FakePage(site))
        admin._goto(media.library_page_url(1))
        self.assertEqual(admin._row_records(), [])

    def test_unidentifiable_row_fails_closed(self):
        site = self.library_with(["unrelated.png"])
        site.break_row_links = True
        evidence = self.evidence_for(site)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_pagination_is_walked_and_every_page_is_hashed(self):
        items = [FakeAttachment(600 + index, f"file_{index}.png", f"x{index}".encode())
                 for index in range(45)]
        site = FakeSite(items)
        evidence = self.evidence_for(site)
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["enumerated"], 45)
        self.assertEqual(evidence["pages_read"], 3)
        # Not "at most a probe window": every single enumerated image row.
        self.assertEqual(evidence["image_rows"], 45)
        self.assertEqual(evidence["image_hashes_completed"], 45)
        self.assertEqual(evidence["hash_failures"], 0)

    def test_preflight_performs_no_write_action(self):
        site = self.library_with(["unrelated.png"])
        self.evidence_for(site)
        self.assertEqual(site.selected_files, [])
        self.assertEqual(site.clicks, [])
        self.assertEqual(site.uploads, 0)
        for url in site.navigations:
            media.assert_admin_url(url)


# ===========================================================================
# The hash gate is COMPLETE over every enumerated image attachment.
#
# This is the blocker an independent review raised: the first build hashed only
# name conflicts plus the newest few rows, so an identical image already on the
# site under an unrelated OLDER filename was never looked at, and the plan's own
# evidence admitted it. Every test below puts the interesting file at the very
# END of a multi-page library -- the oldest row, far outside any recency window
# the old gate could have used -- so a regression to sampling fails here.
# ===========================================================================
class TestCompleteHashGate(ToolTestCase):
    OLD_WINDOW = 12  # the size of the retired recency probe window

    def evidence_for(self, site):
        with fake_network(site):
            return media.duplicate_preflight(media.AdminPage(FakePage(site)))

    def library_ending_with(self, tail, *, newer=45, extension=".png"):
        """`newer` unrelated images, then `tail` as the OLDEST row in the library."""
        items = [FakeAttachment(700 + index, f"gallery_{index}{extension}",
                                f"body-{index}".encode())
                 for index in range(newer)]
        if tail is not None:
            items.append(tail)
        site = FakeSite(items)
        if tail is not None:
            position = len(items)
            self.assertGreater(position, self.OLD_WINDOW,
                               "the fixture must place the tail outside the old window")
            self.assertGreater(position, FakeSite.PER_PAGE,
                               "the fixture must place the tail beyond the first page")
        return site

    def test_matching_bytes_under_an_old_unrelated_filename_are_caught(self):
        data = (media.GALLERY_DIR / "04_opposite_face.png").read_bytes()
        old = FakeAttachment(999, "scan_of_a_ring_2019.png", data)
        site = self.library_ending_with(old)

        evidence = self.evidence_for(site)

        self.assertEqual(evidence["name_conflicts"], [],
                         "the name gate cannot see this one; only the hash gate can")
        self.assertEqual(evidence["image_rows"], 46)
        self.assertEqual(evidence["image_hashes_completed"], 46)
        self.assertEqual(len(evidence["hash_conflicts"]), 1)
        self.assertEqual(evidence["hash_conflicts"][0]["attachment_id"], 999)
        self.assertEqual(evidence["hash_conflicts"][0]["matches_fixed_image"],
                         "04_opposite_face.png")
        with self.assertRaises(media.DuplicateFound):
            media.require_no_duplicates(evidence)

    def test_every_one_of_the_six_is_caught_under_an_old_unrelated_name(self):
        for record in media.FIXED_IMAGES:
            with self.subTest(image=record["filename"]):
                data = (media.GALLERY_DIR / record["filename"]).read_bytes()
                old = FakeAttachment(990, "archive_photo.png", data)
                evidence = self.evidence_for(self.library_ending_with(old, newer=25))
                self.assertEqual(
                    [item["matches_fixed_image"] for item in evidence["hash_conflicts"]],
                    [record["filename"]])

    def test_an_old_unrelated_nonmatching_image_is_still_fully_hashed(self):
        old = FakeAttachment(998, "old_brochure_cover.png",
                             b"nothing at all like the approved packing ring images")
        site = self.library_ending_with(old)

        evidence = self.evidence_for(site)

        self.assertTrue(evidence["enumeration_complete"])
        self.assertTrue(evidence["hash_complete"])
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["image_rows"], 46)
        self.assertEqual(evidence["image_hashes_completed"], 46)
        self.assertEqual(evidence["hash_failures"], 0)
        self.assertEqual(evidence["hash_conflicts"], [])
        self.assertGreater(evidence["hash_bytes_read"], 0)
        media.require_no_duplicates(evidence)

        # Proven by navigation, not by the counter alone: every image attachment
        # was opened on its own fixed edit screen, the oldest one included.
        for item in site.attachments:
            self.assertIn(media.attachment_edit_url(item.id), site.navigations)
        for url in site.navigations:
            media.assert_admin_url(url)
        self.assertEqual(site.uploads, 0)
        self.assertEqual(site.clicks, [])

    def test_one_unhashable_older_row_fails_the_whole_check_closed(self):
        old = FakeAttachment(997, "unreadable_old.png", b"x", url_fields=[])
        evidence = self.evidence_for(self.library_ending_with(old, newer=30))

        self.assertTrue(evidence["enumeration_complete"])
        self.assertEqual(evidence["hash_failures"], 1)
        self.assertFalse(evidence["hash_complete"])
        self.assertFalse(evidence["complete"])
        self.assertLess(evidence["image_hashes_completed"], evidence["image_rows"])
        with self.assertRaises(media.MediaUploadError) as caught:
            media.require_no_duplicates(evidence)
        self.assertIn("does not sample", str(caught.exception))

    def test_an_older_image_whose_original_cannot_be_downloaded_fails_closed(self):
        old = FakeAttachment(996, "gone_from_uploads.png", b"x", public_missing=True)
        evidence = self.evidence_for(self.library_ending_with(old, newer=30))

        self.assertEqual(evidence["hash_failures"], 1)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_an_older_image_served_as_the_wrong_type_fails_closed(self):
        site = self.library_ending_with(
            FakeAttachment(995, "mislabelled_old.png", b"x"), newer=25)
        with fake_network(site, content_type="text/html"):
            evidence = media.duplicate_preflight(media.AdminPage(FakePage(site)))
        self.assertGreaterEqual(evidence["hash_failures"], 1)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_a_row_with_no_readable_filename_fails_closed(self):
        site = self.library_ending_with(None, newer=25)
        site.hide_row_filenames = True
        evidence = self.evidence_for(site)
        self.assertFalse(evidence["enumeration_complete"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["image_hashes_completed"], 0)
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_non_png_image_formats_are_hashed_too(self):
        for extension in (".jpg", ".jpeg", ".gif", ".webp"):
            with self.subTest(extension=extension):
                site = self.library_ending_with(
                    FakeAttachment(994, f"old_shot{extension}", b"unrelated bytes"),
                    newer=21, extension=extension)
                evidence = self.evidence_for(site)
                self.assertTrue(evidence["complete"])
                self.assertEqual(evidence["image_rows"], 22)
                self.assertEqual(evidence["image_hashes_completed"], 22)

    def test_a_non_image_row_is_never_opened_or_downloaded(self):
        site = FakeSite([FakeAttachment(300, "unrelated_photo.png", b"a"),
                         FakeAttachment(301, "price_list.pdf", b"b"),
                         FakeAttachment(302, "terms.docx", b"c")])
        with fake_network(site) as openers:
            evidence = media.duplicate_preflight(media.AdminPage(FakePage(site)))
        self.assertEqual(evidence["image_rows"], 1)
        self.assertEqual(evidence["image_hashes_completed"], 1)
        self.assertTrue(evidence["complete"])
        self.assertNotIn(media.attachment_edit_url(301), site.navigations)
        self.assertNotIn(media.attachment_edit_url(302), site.navigations)
        downloaded = [url for opener in openers for url in opener.requests]
        self.assertEqual(downloaded, [site.attachments[0].url])

    # -- the bounds refuse; they never sample -------------------------------
    def test_the_image_count_bound_refuses_rather_than_sampling(self):
        site = self.library_ending_with(None, newer=30)
        with mock.patch.object(media, "MAX_IMAGE_ATTACHMENTS", 5):
            with self.assertRaises(media.MediaUploadError) as caught:
                self.evidence_for(site)
        message = str(caught.exception)
        self.assertIn("does not sample", message)
        self.assertIn("image attachments", message)

    def test_the_cumulative_byte_bound_refuses_rather_than_sampling(self):
        site = self.library_ending_with(None, newer=8)
        with mock.patch.object(media, "MAX_TOTAL_DOWNLOAD_BYTES", 10):
            with self.assertRaises(media.MediaUploadError) as caught:
                self.evidence_for(site)
        message = str(caught.exception)
        self.assertIn("read bound", message)
        self.assertIn("does not sample", message)

    def test_the_row_bound_refuses_rather_than_sampling(self):
        site = self.library_ending_with(None, newer=45)
        with mock.patch.object(media, "MAX_LIBRARY_ROWS", 10):
            with self.assertRaises(media.MediaUploadError) as caught:
                self.evidence_for(site)
        self.assertIn("row read bound", str(caught.exception))

    def test_the_page_bound_leaves_the_check_incomplete_not_clean(self):
        site = self.library_ending_with(None, newer=45)
        with mock.patch.object(media, "MAX_LIBRARY_PAGES", 1):
            evidence = self.evidence_for(site)
        self.assertFalse(evidence["enumeration_complete"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["image_hashes_completed"], 0)
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    def test_a_per_file_over_bound_download_fails_closed(self):
        site = self.library_ending_with(
            FakeAttachment(993, "huge_old_image.png", b"0123456789"), newer=21)
        with mock.patch.object(media, "MAX_DOWNLOAD_BYTES", 4):
            evidence = self.evidence_for(site)
        self.assertEqual(evidence["hash_failures"], 1)
        self.assertFalse(evidence["complete"])
        with self.assertRaises(media.MediaUploadError):
            media.require_no_duplicates(evidence)

    # -- the evidence says COMPLETE, and never says "bounded to newest" -----
    def test_the_scope_statement_claims_completeness_without_caveats(self):
        scope = media.DUPLICATE_SCOPE
        self.assertIn("COMPLETE", scope)
        self.assertIn("EVERY enumerated image attachment", scope)
        for retired in ("newest", "recency", "probe window", "NOT proven absent",
                        "is NOT detected"):
            with self.subTest(phrase=retired):
                self.assertNotIn(retired, scope)
        self.assertIn("COMPLETE", media.UPLOAD_CONTRACT["duplicate_gate"])

    def test_no_module_text_still_claims_an_older_filename_is_unproven(self):
        lowered = SOURCE_TEXT.casefold()
        for retired in ("newest few", "recency probe", "is not detected",
                        "not proven absent", "labelled as bounded"):
            with self.subTest(phrase=retired):
                self.assertNotIn(retired, lowered)

    def test_a_staged_plan_carries_the_complete_gate_totals(self):
        site = self.library_ending_with(
            FakeAttachment(992, "old_unrelated.png", b"harmless"), newer=25)
        buffer = io.StringIO()
        with fake_browser(site), fake_network(site), contextlib.redirect_stdout(buffer):
            media.command_stage(argparse.Namespace())
        plans = list(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        plan = media.load_plan(plans[0])
        evidence = plan["duplicate_check"]
        self.assertEqual(evidence["scope"], media.DUPLICATE_SCOPE)
        self.assertEqual(evidence["library_total"], 26)
        self.assertEqual(evidence["enumerated"], 26)
        self.assertEqual(evidence["image_rows"], 26)
        self.assertEqual(evidence["image_hashes_completed"], 26)
        self.assertEqual(evidence["hash_failures"], 0)
        self.assertTrue(evidence["enumeration_complete"])
        self.assertTrue(evidence["hash_complete"])
        self.assertTrue(evidence["complete"])
        self.assertEqual(site.uploads, 0)

    def test_commit_refuses_before_the_attempt_lock_when_the_gate_is_incomplete(self):
        plan_path, _ = self.stage_a_plan()
        site = self.library_ending_with(
            FakeAttachment(991, "unreadable_old.png", b"x", url_fields=[]), newer=25)
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.MediaUploadError) as caught:
                media.command_commit(argparse.Namespace(plan=str(plan_path),
                                                        approval=media.APPROVAL_WORD))
        self.assertIn("does not sample", str(caught.exception))
        self.assertFalse(media.lock_path(plan_path).exists(),
                         "an incomplete duplicate proof must not burn the plan")
        self.assertEqual(site.uploads, 0)
        self.assertEqual(site.selected_files, [])


# ===========================================================================
# The commit flow
# ===========================================================================
class TestCommitFlow(ToolTestCase):
    def clean_site(self):
        return self.library_with(["unrelated_photo.png"])

    def run_commit(self, site, plan_path, approval=media.APPROVAL_WORD):
        buffer = io.StringIO()
        with fake_browser(site), fake_network(site), contextlib.redirect_stdout(buffer):
            self.commit(plan_path, approval=approval)
        return json.loads(buffer.getvalue())

    def test_happy_path_uploads_six_in_order_once_each(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        report = self.run_commit(site, plan_path)

        self.assertEqual(report["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(report["uploads"], 6)
        self.assertEqual(report["products_changed"], 0)
        self.assertEqual(report["emails"], 0)
        self.assertEqual(report["deleted"], 0)
        self.assertTrue(report["replay_locked"])

        self.assertEqual(site.uploads, 6)
        self.assertEqual([Path(path).name for path in site.selected_files],
                         list(media.FIXED_FILENAMES))
        self.assertEqual(site.clicks, ["html-upload"] * 6)
        self.assertEqual([item["position"] for item in report["uploaded"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(len({item["attachment_id"] for item in report["uploaded"]}), 6)
        for item in report["uploaded"]:
            self.assertGreater(item["attachment_id"], 0)

    def test_every_selected_path_is_exactly_one_fixed_file(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        self.run_commit(site, plan_path)
        allowed = {str(media.GALLERY_DIR.resolve() / name) for name in media.FIXED_FILENAMES}
        self.assertEqual({str(Path(path).resolve()) for path in site.selected_files}, allowed)

    def test_every_navigation_is_allowlisted(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        self.run_commit(site, plan_path)
        for url in site.navigations:
            media.assert_admin_url(url)

    def test_result_manifest_and_receipts_are_written(self):
        site = self.clean_site()
        plan_path, plan = self.stage_a_plan()
        self.run_commit(site, plan_path)
        result = json.loads(media.result_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(len(result["sha256_to_attachment"]), 6)
        self.assertEqual(sorted(result["sha256_to_attachment"]),
                         sorted(record["sha256"] for record in media.FIXED_IMAGES))
        self.assertEqual(result["products_changed"], 0)
        for record in result["uploaded_verified"]:
            self.assertEqual(set(record), media.RESULT_KEYS)
        actions = self.receipt_actions()
        self.assertIn("packing_ring_media_uploads_committed", actions)
        self.assertIn("packing_ring_media_result_verified", actions)

    def test_lock_finishes_committed_verified(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        self.run_commit(site, plan_path)
        lock = json.loads(media.lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertTrue(lock["no_retry"])
        self.assertEqual(len(lock["uploaded_verified"]), 6)

    def test_plan_cannot_be_replayed(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        self.run_commit(site, plan_path)
        before = site.uploads
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.MediaUploadError) as caught:
                self.commit(plan_path)
        self.assertIn("already entered commit", str(caught.exception))
        self.assertEqual(site.uploads, before)

    def test_list_mode_result_identification_also_works(self):
        site = self.clean_site()
        site.result_mode = "list"
        plan_path, _ = self.stage_a_plan()
        report = self.run_commit(site, plan_path)
        self.assertEqual(report["uploads"], 6)

    def test_failure_at_upload_four_stops_five_and_six(self):
        site = self.clean_site()
        site.fail_upload_at = 4
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.IndeterminateError) as caught:
                self.commit(plan_path)
        message = str(caught.exception)
        self.assertIn("Upload 4 of 6", message)
        self.assertIn("permanently locked", message)
        self.assertEqual(site.uploads, 4)
        self.assertEqual([Path(path).name for path in site.selected_files],
                         list(media.FIXED_FILENAMES[:4]))

        lock = json.loads(media.lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertTrue(lock["no_retry"])
        self.assertEqual(lock["stage"], "upload_4")
        self.assertEqual(lock["uploads_completed"], 3)
        self.assertEqual(lock["uploads_remaining"], 3)
        self.assertEqual([item["position"] for item in lock["uploaded_verified"]], [1, 2, 3])
        self.assertFalse(lock["rollback_performed"])
        self.assertFalse(lock["delete_performed"])

        result = json.loads(media.result_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertTrue(result["no_retry"])
        self.assertIn("packing_ring_media_indeterminate_no_retry", self.receipt_actions())

    def test_a_failed_plan_is_never_retried(self):
        site = self.clean_site()
        site.fail_upload_at = 2
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.IndeterminateError):
                self.commit(plan_path)
        uploads_after_failure = site.uploads
        site.fail_upload_at = None
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.MediaUploadError):
                self.commit(plan_path)
        self.assertEqual(site.uploads, uploads_after_failure)

    def test_a_timeout_is_a_failure_not_a_pass(self):
        site = self.clean_site()
        site.fail_upload_at = 2
        site.fail_mode = "timeout"
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.IndeterminateError):
                self.commit(plan_path)
        lock = json.loads(media.lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["reason"], "TimeoutError")
        self.assertEqual(lock["uploads_completed"], 1)

    def test_an_ambiguous_result_is_a_failure(self):
        site = self.clean_site()
        site.fail_upload_at = 1
        site.fail_mode = "ambiguous"
        site.result_mode = "list"
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.IndeterminateError):
                self.commit(plan_path)
        lock = json.loads(media.lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["uploads_completed"], 0)

    def test_a_renamed_stored_file_is_refused_at_readback(self):
        site = self.clean_site()
        site.stored_name_suffix = "-1"
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.IndeterminateError):
                self.commit(plan_path)
        lock = json.loads(media.lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["uploads_completed"], 0)
        self.assertEqual(lock["stage"], "upload_1")

    def test_a_public_hash_mismatch_is_refused(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site, payload_override=b"wrong bytes"):
            with self.assertRaises(media.IndeterminateError):
                self.commit(plan_path)
        lock = json.loads(media.lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["uploads_completed"], 0)

    def test_duplicate_refuses_before_the_attempt_lock_exists(self):
        site = self.library_with(["02_top_view.png"])
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.DuplicateFound):
                self.commit(plan_path)
        self.assertEqual(site.uploads, 0)
        self.assertEqual(site.selected_files, [])
        self.assertFalse(media.lock_path(plan_path).exists())

    def test_changed_local_file_refuses_before_the_attempt_lock(self):
        plan_path, _ = self.stage_a_plan()
        site = self.clean_site()
        broken = [dict(record) for record in media.verify_local_files()]
        broken[0]["sha256"] = "0" * 64
        with mock.patch.object(media, "verify_local_files", return_value=broken):
            with fake_browser(site), fake_network(site):
                with self.assertRaises(media.MediaUploadError) as caught:
                    self.commit(plan_path)
        self.assertIn("no longer match", str(caught.exception))
        self.assertEqual(site.uploads, 0)
        self.assertFalse(media.lock_path(plan_path).exists())

    def test_login_screen_refuses_without_uploading(self):
        site = self.clean_site()
        site.login_screen = True
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.MediaUploadError) as caught:
                self.commit(plan_path)
        self.assertIn("sign-in", str(caught.exception))
        self.assertEqual(site.uploads, 0)
        self.assertFalse(media.lock_path(plan_path).exists())

    def test_redirect_to_a_foreign_screen_refuses(self):
        site = self.clean_site()
        site.redirects[media.library_page_url(1)] = f"{ORIGIN}/wp-admin/plugins.php"
        plan_path, _ = self.stage_a_plan()
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.MediaUploadError):
                self.commit(plan_path)
        self.assertEqual(site.uploads, 0)
        self.assertFalse(media.lock_path(plan_path).exists())

    def test_the_run_finishes_on_a_harmless_list_screen(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        self.run_commit(site, plan_path)
        self.assertEqual(site.current_url, media.UPLOAD_LIST_URL)


# ===========================================================================
# The uploader screen itself
# ===========================================================================
class TestUploaderScreen(ToolTestCase):
    def admin_for(self, site):
        return media.AdminPage(FakePage(site))

    def test_requires_exactly_one_file_input(self):
        site = self.library_with([])
        admin = self.admin_for(site)
        original = site._render_media_new

        def two_inputs():
            tree = original()
            tree.children.append(_element(
                "input", attrs={"type": "file", "name": "async-upload"},
                on_set_files=lambda path: None))
            return tree

        site._render_media_new = two_inputs
        with self.assertRaises(media.MediaUploadError) as caught:
            admin.upload_one(media.GALLERY_DIR / media.FIXED_FILENAMES[0], set())
        self.assertIn("exactly one file input", str(caught.exception))
        self.assertEqual(site.selected_files, [])

    def test_requires_exactly_one_submit_control(self):
        site = self.library_with([])
        admin = self.admin_for(site)
        original = site._render_media_new

        def no_submit():
            tree = original()
            tree.children[0].children = tree.children[0].children[:1]
            return tree

        site._render_media_new = no_submit
        with self.assertRaises(media.MediaUploadError) as caught:
            admin.upload_one(media.GALLERY_DIR / media.FIXED_FILENAMES[0], set())
        self.assertIn("exactly one upload control", str(caught.exception))

    def test_refuses_a_file_outside_the_fixed_six(self):
        site = self.library_with([])
        admin = self.admin_for(site)
        for path in (media.GALLERY_DIR / "00_gallery_review_sheet.jpg",
                     media.GALLERY_DIR / "manifest.json",
                     Path(self.tmp.name) / "anything.png"):
            with self.subTest(path=path), self.assertRaises(media.MediaUploadError):
                admin.upload_one(path, set())
        self.assertEqual(site.navigations, [])
        self.assertEqual(site.selected_files, [])

    def test_an_id_that_already_existed_is_refused_as_a_result(self):
        site = self.library_with([])
        admin = self.admin_for(site)
        site.current_url = f"{ORIGIN}/wp-admin/upload.php?posted=77"
        with self.assertRaises(media.MediaUploadError) as caught:
            admin._identify_upload({77})
        self.assertIn("already existed", str(caught.exception))

    def test_landing_off_the_media_list_is_refused(self):
        site = self.library_with([])
        admin = self.admin_for(site)
        site.current_url = f"{ORIGIN}/wp-admin/media-new.php?browser-uploader"
        with self.assertRaises(media.MediaUploadError):
            admin._identify_upload(set())


# ===========================================================================
# Attachment read-back
# ===========================================================================
class TestAttachmentReadback(ToolTestCase):
    def admin_with(self, attachment):
        site = FakeSite([attachment])
        return site, media.AdminPage(FakePage(site))

    def good(self):
        name = "02_top_view.png"
        return FakeAttachment(900, name, (media.GALLERY_DIR / name).read_bytes())

    def test_reads_a_safe_projection_only(self):
        site, admin = self.admin_with(self.good())
        detail = admin.read_attachment(900, expected_basename="02_top_view.png")
        self.assertEqual(set(detail), {"attachment_id", "filename", "source_url",
                                       "extension", "filetype_matches_name"})
        self.assertEqual(detail["attachment_id"], 900)
        self.assertEqual(detail["filename"], "02_top_view.png")

    def test_ambiguous_file_url_is_refused(self):
        item = self.good()
        item.url_fields = [item.url, f"{ORIGIN}/wp-content/uploads/2026/08/other.png"]
        site, admin = self.admin_with(item)
        with self.assertRaises(media.MediaUploadError) as caught:
            admin.read_attachment(900)
        self.assertIn("more than one file URL", str(caught.exception))

    def test_missing_file_url_is_refused(self):
        item = self.good()
        item.url_fields = []
        site, admin = self.admin_with(item)
        with self.assertRaises(media.MediaUploadError):
            admin.read_attachment(900)

    def test_disagreeing_filename_box_is_refused(self):
        item = self.good()
        item.url = f"{ORIGIN}/wp-content/uploads/2026/08/something_else.png"
        site, admin = self.admin_with(item)
        with self.assertRaises(media.MediaUploadError) as caught:
            admin.read_attachment(900)
        self.assertIn("disagree", str(caught.exception))

    def test_non_png_filetype_is_refused(self):
        item = self.good()
        item.filetype = "File type: JPG"
        site, admin = self.admin_with(item)
        with self.assertRaises(media.MediaUploadError):
            admin.read_attachment(900)

    def test_ambiguous_identity_boxes_are_refused(self):
        for field in ("filename_boxes", "filetype_boxes"):
            with self.subTest(field=field):
                item = self.good()
                setattr(item, field, 2)
                site, admin = self.admin_with(item)
                with self.assertRaises(media.MediaUploadError):
                    admin.read_attachment(900)

    def test_a_foreign_hosted_file_url_is_refused(self):
        item = self.good()
        item.url = "https://cdn.example/wp-content/uploads/2026/08/02_top_view.png"
        site, admin = self.admin_with(item)
        with self.assertRaises(media.MediaUploadError):
            admin.read_attachment(900)


# ===========================================================================
# The browser lane lock
# ===========================================================================
class TestBrowserLaneLock(ToolTestCase):
    def test_both_commands_are_decorated_with_the_shared_wordpress_lane(self):
        decorated = {}
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.FunctionDef) and node.name in ("command_stage",
                                                                   "command_commit"):
                decorated[node.name] = [
                    ast.unparse(item) for item in node.decorator_list
                ]
        self.assertEqual(sorted(decorated), ["command_commit", "command_stage"])
        for name, decorators in decorated.items():
            with self.subTest(command=name):
                self.assertTrue(any(item.startswith("holds_wordpress_browser(")
                                    for item in decorators), decorators)
        self.assertIn('ui_browser_lock("wordpress"', SOURCE_TEXT)

    def test_the_lane_name_is_the_shared_wordpress_browser(self):
        import ui_lane_lock

        self.assertIn("wordpress", ui_lane_lock.LANES)
        self.assertEqual(ui_lane_lock.LANES["wordpress"], 9229)
        self.assertIn(str(9229), media.CDP_ENDPOINT)

    def test_mutex_is_held_before_the_attempt_lock_and_through_the_uploads(self):
        import ui_lane_lock

        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()
        observed: list[tuple[str, bool]] = []
        real_write_lock = media.write_lock

        def spy_write_lock(path, value, *, exclusive=False):
            observed.append((f"lock:{value.get('status')}:{exclusive}",
                             ui_lane_lock.lane_status("wordpress")["held"]))
            return real_write_lock(path, value, exclusive=exclusive)

        real_upload = media.AdminPage.upload_one

        def spy_upload(self, path, known_ids):
            observed.append((f"upload:{Path(path).name}",
                             ui_lane_lock.lane_status("wordpress")["held"]))
            return real_upload(self, path, known_ids)

        with mock.patch.object(media, "write_lock", spy_write_lock), \
                mock.patch.object(media.AdminPage, "upload_one", spy_upload):
            buffer = io.StringIO()
            with fake_browser(site), fake_network(site), contextlib.redirect_stdout(buffer):
                self.commit(plan_path)

        self.assertTrue(observed)
        self.assertTrue(all(held for _, held in observed), observed)
        first_lock = next(index for index, (label, _) in enumerate(observed)
                          if label.startswith("lock:"))
        first_upload = next(index for index, (label, _) in enumerate(observed)
                            if label.startswith("upload:"))
        self.assertLess(first_lock, first_upload)
        self.assertEqual(observed[first_lock][0], "lock:acting:True")

    def test_a_busy_browser_is_a_free_refusal(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()

        @contextlib.contextmanager
        def busy(lane, *, purpose, wait_seconds=None):
            raise media.UiLaneBusy("the other lane is driving the wordpress browser")
            yield  # pragma: no cover

        with mock.patch.object(media, "ui_browser_lock", busy):
            with fake_browser(site), fake_network(site) as openers:
                with self.assertRaises(media.UiLaneBusy):
                    self.commit(plan_path)
        self.assertEqual(site.navigations, [])
        self.assertEqual(site.uploads, 0)
        self.assertEqual(openers, [])
        self.assertFalse(media.lock_path(plan_path).exists())
        self.assertNotIn("packing_ring_media_indeterminate_no_retry", self.receipt_actions())

    def test_a_busy_browser_exits_one_without_a_traceback(self):
        site = self.clean_site()
        plan_path, _ = self.stage_a_plan()

        @contextlib.contextmanager
        def busy(lane, *, purpose, wait_seconds=None):
            raise media.UiLaneBusy("busy")
            yield  # pragma: no cover

        argv = ["tool", "commit", "--plan", str(plan_path), "--approval", "APPROVED"]
        errors = io.StringIO()
        with mock.patch.object(media, "ui_browser_lock", busy), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stderr(errors):
            self.assertEqual(media.main(), 1)
        self.assertTrue(errors.getvalue().startswith("ERROR: "))

    def test_the_real_mutex_excludes_a_second_thread(self):
        import ui_lane_lock

        held = threading.Event()
        release = threading.Event()
        outcome: list[str] = []

        def holder():
            with ui_lane_lock.ui_browser_lock("wordpress", purpose="test holder"):
                held.set()
                release.wait(5)

        def waiter():
            try:
                with ui_lane_lock.ui_browser_lock("wordpress", purpose="test waiter",
                                                  wait_seconds=0.2):
                    outcome.append("entered")
            except ui_lane_lock.UiLaneBusy:
                outcome.append("busy")

        first = threading.Thread(target=holder)
        first.start()
        self.assertTrue(held.wait(5))
        second = threading.Thread(target=waiter)
        second.start()
        second.join(10)
        release.set()
        first.join(10)
        self.assertEqual(outcome, ["busy"])

    def clean_site(self):
        return self.library_with(["unrelated_photo.png"])


# ===========================================================================
# Staging
# ===========================================================================
class TestStageCommand(ToolTestCase):
    def test_stage_writes_a_plan_and_no_website_write(self):
        site = self.library_with(["unrelated_photo.png"])
        buffer = io.StringIO()
        with fake_browser(site), fake_network(site), contextlib.redirect_stdout(buffer):
            media.command_stage(argparse.Namespace())
        printed = buffer.getvalue()
        self.assertIn("STAGED ONLY - ZERO WEBSITE WRITES", printed)
        report = json.loads(printed[:printed.rindex("}") + 1])
        self.assertEqual(report["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(report["uploads"], 0)
        self.assertEqual(report["website_writes"], 0)
        self.assertEqual(report["products_changed"], 0)
        self.assertEqual(report["emails"], 0)
        self.assertEqual(site.uploads, 0)
        self.assertEqual(site.selected_files, [])
        self.assertEqual(site.clicks, [])
        plans = list(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        media.load_plan(plans[0])

    def test_stage_refuses_when_a_duplicate_exists(self):
        site = self.library_with(["06_edge_profile.png"])
        with fake_browser(site), fake_network(site):
            with self.assertRaises(media.DuplicateFound):
                media.command_stage(argparse.Namespace())
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])


# ===========================================================================
# Source audit -- capabilities that must not exist at all
# ===========================================================================
class TestSourceHasNoForbiddenCapability(unittest.TestCase):
    @staticmethod
    def _docstring_nodes(tree):
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    found.add(id(body[0].value))
        return found

    def literals(self):
        skip = self._docstring_nodes(SOURCE_TREE)
        return [node.value for node in ast.walk(SOURCE_TREE)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip]

    def test_no_rest_route_or_write_verb(self):
        for token in ("wp-json", "/wc/v3", "wp/v2", "async-upload.php",
                      "admin-ajax.php", "xmlrpc.php"):
            with self.subTest(token=token):
                self.assertFalse(any(token in text for text in self.literals()), token)

    def test_no_http_write_verb_anywhere(self):
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, self.literals())
        self.assertIn("GET", self.literals())

    def test_no_product_variation_or_commerce_route(self):
        # Route-shaped tokens only. "changes_product_or_variation": False is a
        # DECLARATION that the capability is absent, and must not be mistaken for
        # the capability itself.
        for token in ("/products", "product_create", "product_update", "/variations",
                      "variation_create", "variation_update", "post.php?post_type",
                      "/orders", "/customers", "/coupons", "/refunds", "/settings",
                      "post_type=product", "action=delete", "action=trash",
                      "plugins.php", "themes.php", "users.php", "options-general.php"):
            with self.subTest(token=token):
                self.assertFalse(any(token in text for text in self.literals()), token)

    def test_no_credential_or_storage_access(self):
        for token in ("document.cookie", "localStorage", "sessionStorage",
                      "Set-Cookie", "Authorization", "consumer_key", "consumer_secret",
                      "_wpnonce", "nonce_value", "csrf"):
            with self.subTest(token=token):
                self.assertFalse(any(token in text for text in self.literals()), token)

    def test_no_forbidden_imports(self):
        imported = set()
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("subprocess", "requests", "woocommerce_common", "smtplib",
                          "ftplib", "socket", "shutil", "ctypes"):
            with self.subTest(module=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_no_browser_launch_or_page_dump_calls(self):
        called = set()
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
        for forbidden in ("launch", "launch_persistent_context", "new_context",
                          "add_cookies", "cookies", "storage_state", "evaluate",
                          "evaluate_handle", "content", "inner_html", "set_content",
                          "route", "expose_function", "add_init_script",
                          "set_extra_http_headers", "screenshot", "pdf"):
            with self.subTest(call=forbidden):
                self.assertNotIn(forbidden, called)
        self.assertIn("connect_over_cdp", called)

    def test_no_delete_rollback_or_retry_implementation(self):
        names = {node.name for node in ast.walk(SOURCE_TREE)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in sorted(names):
            lowered = name.casefold()
            for banned in ("delete", "remove", "trash", "rollback", "retry",
                           "detach", "rename", "replace", "cleanup", "undo"):
                with self.subTest(function=name, banned=banned):
                    self.assertNotIn(banned, lowered)

    def test_only_one_file_selection_site_in_the_module(self):
        selections = [node for node in ast.walk(SOURCE_TREE)
                      if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "set_input_files"]
        self.assertEqual(len(selections), 1)
        clicks = [node for node in ast.walk(SOURCE_TREE)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "click"]
        self.assertEqual(len(clicks), 1)

    def test_the_contract_states_what_it_cannot_do(self):
        self.assertFalse(media.UPLOAD_CONTRACT["atomic"])
        self.assertFalse(media.UPLOAD_CONTRACT["rollback_available"])
        self.assertFalse(media.UPLOAD_CONTRACT["delete_available"])
        self.assertFalse(media.UPLOAD_CONTRACT["retry_available"])
        self.assertFalse(media.UPLOAD_CONTRACT["changes_product_or_variation"])
        self.assertFalse(media.UPLOAD_CONTRACT["rest_api_used"])
        self.assertFalse(media.UPLOAD_CONTRACT["credentials_read"])
        self.assertFalse(media.UPLOAD_CONTRACT["sends_email"])
        self.assertEqual(media.UPLOAD_CONTRACT["uploads"], 6)
        self.assertIn("dimensional", media.IMAGE_PROVENANCE)


# ===========================================================================
# The tests themselves change no real commissioned state
# ===========================================================================
class TestSuitePerformedNoLiveAction(unittest.TestCase):
    def test_real_plan_folder_is_byte_identical_to_the_pre_test_baseline(self):
        current = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in REAL_PLAN_DIR.glob("*.json")
        } if REAL_PLAN_DIR.exists() else {}
        self.assertEqual(
            current,
            REAL_PLAN_BASELINE,
            "the offline test run created, removed or changed real plan state",
        )

    def test_the_gallery_on_disk_is_untouched(self):
        for record in media.FIXED_IMAGES:
            path = Path(r"C:\FRPDepot\Dado\20_Working\packing_rings"
                        r"\generated_gallery_20260812") / record["filename"]
            with self.subTest(name=record["filename"]):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 record["sha256"])


if __name__ == "__main__":
    unittest.main()
