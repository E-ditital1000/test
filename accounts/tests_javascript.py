"""
Syntax checks for every line of JavaScript this system ships.

The offline layer is the least forgiving code in the build — it is the only
thing standing between a technician and a lost afternoon — and until now
none of it had ever been parsed, let alone run. A stray bracket would have
shipped, and the failure would have looked like "the app just doesn't save
my work".

This parses with esprima, a JavaScript parser written in Python, so the
check runs in the ordinary test suite with no Node toolchain on the box.

It is a syntax check, not a behaviour check. `tests_browser.py` covers
behaviour where a browser is available.
"""
import re
from pathlib import Path

import esprima
from django.conf import settings
from django.test import SimpleTestCase, tag

ROOT = Path(settings.BASE_DIR)

# Inline <script> in a Django template is not valid JavaScript until the
# template has rendered — {{ url }} and {% if %} are syntax errors to a
# parser. These stand-ins let the surrounding code be checked.
TEMPLATE_TAG = re.compile(r"\{%.*?%\}", re.S)
TEMPLATE_VAR = re.compile(r"\{\{.*?\}\}", re.S)
SCRIPT_BLOCK = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def strip_django(source):
    """
    Replace template syntax with a literal of the same shape.

    A {{ var }} inside quotes becomes a harmless string; a {% if %} becomes
    nothing. What is left is what the browser would actually receive, near
    enough to parse.
    """
    source = TEMPLATE_TAG.sub("", source)
    return TEMPLATE_VAR.sub("0", source)


def javascript_files():
    return sorted((ROOT / "static" / "js").glob("*.js"))


def template_scripts():
    """(path, block index, source) for every inline script in a template."""
    found = []
    for template in sorted((ROOT / "templates").rglob("*.html")):
        text = template.read_text(encoding="utf-8")
        for index, block in enumerate(SCRIPT_BLOCK.findall(text)):
            if block.strip():
                found.append((template, index, block))
    return found


@tag("acceptance")
class JavaScriptSyntaxTests(SimpleTestCase):
    """Every script must parse. A build that ships a syntax error is broken."""

    def test_static_javascript_parses(self):
        files = javascript_files()
        self.assertTrue(files, "no javascript found — has it moved?")
        for path in files:
            with self.subTest(file=path.name):
                try:
                    esprima.parseScript(path.read_text(encoding="utf-8"))
                except esprima.Error as error:
                    self.fail(f"{path.relative_to(ROOT)} does not parse: {error}")

    def test_the_service_worker_parses(self):
        worker = ROOT / "templates" / "sw.js"
        self.assertTrue(worker.exists(), "the service worker has moved")
        try:
            esprima.parseScript(strip_django(worker.read_text(encoding="utf-8")))
        except esprima.Error as error:
            self.fail(f"sw.js does not parse: {error}")

    def test_inline_template_scripts_parse(self):
        scripts = template_scripts()
        self.assertTrue(scripts, "no inline scripts found — have they moved?")
        for path, index, block in scripts:
            with self.subTest(template=str(path.relative_to(ROOT)), block=index):
                try:
                    esprima.parseScript(strip_django(block))
                except esprima.Error as error:
                    self.fail(
                        f"{path.relative_to(ROOT)} script #{index} does not parse: {error}"
                    )


class JavaScriptDisciplineTests(SimpleTestCase):
    """
    A few habits worth holding to, since this code runs where nobody can see
    it fail.
    """

    def test_every_script_is_strict_mode(self):
        """
        Sloppy mode turns a typo'd assignment into a silent global. On a page
        that queues a technician's work, that is how data goes missing.
        """
        for path in javascript_files():
            with self.subTest(file=path.name):
                self.assertIn('"use strict"', path.read_text(encoding="utf-8"))
        for path, index, block in template_scripts():
            with self.subTest(template=str(path.relative_to(ROOT)), block=index):
                self.assertIn('"use strict"', block)

    def test_no_console_logging_ships(self):
        """Debug output left in is noise at best and a leak at worst."""
        for path in javascript_files():
            with self.subTest(file=path.name):
                self.assertNotIn("console.log", path.read_text(encoding="utf-8"))
        for path, index, block in template_scripts():
            with self.subTest(template=str(path.relative_to(ROOT)), block=index):
                self.assertNotIn("console.log", block)

    def test_storage_access_is_guarded(self):
        """
        localStorage throws outright in a private window and in some Android
        webviews. Every read and write has to be wrapped, or the screen dies
        on load for the people least able to report why.
        """
        for path in javascript_files():
            source = path.read_text(encoding="utf-8")
            if "localStorage" not in source:
                continue
            with self.subTest(file=path.name):
                self.assertIn(
                    "try", source, f"{path.name} touches localStorage without a guard"
                )
