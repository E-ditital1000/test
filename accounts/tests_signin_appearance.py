"""
The sign-in screen, which is the only page most people see every day.

Two faults here were the same fault wearing different hats, and both were
reported by A-1 rather than caught by anything: text that could not be read
because it was wearing the wrong ink for the surface under it.

First the search box, where the typed text had nowhere to go. Then the
"Sign in" heading, which stayed dark on navy while the labels beside it
turned white — because `body` resolves `var(--text)` to dark ONCE and
children inherit that computed colour. Redefining the token lower down only
reaches elements that use `var(--text)` in a declaration of their own. A
heading with no colour rule never re-resolves it.

So the rule these defend: wherever the ground changes, something has to
*declare* the ink, not merely redefine the token.
"""
from pathlib import Path

from django.test import SimpleTestCase, tag
from django.urls import reverse

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "css" / "a1.css").read_text(encoding="utf-8")


def block(selector):
    """The declarations of one rule, by selector."""
    start = CSS.index(selector + " {")
    return CSS[start:CSS.index("}", start)]


class SignInInkTests(SimpleTestCase):
    @tag("acceptance")
    def test_the_form_declares_its_ink_and_not_only_the_token(self):
        """
        The bug exactly: `--text` was redefined on the form and the heading
        stayed dark anyway, because nothing on that element used it.
        """
        rule = block(".fbox")
        self.assertIn("--text: #FFFFFF", rule, "the token is restated for the dark ground")
        self.assertIn(
            "color: var(--text)", rule,
            "and something must USE it, or an element with no colour rule of "
            "its own keeps the dark ink it inherited from body",
        )

    def test_anything_painting_its_own_light_surface_keeps_dark_ink(self):
        """
        The note is pale gold, the refusal panel white, the message chips
        pale. They set a background and inherit their ink, so the white
        above would land white-on-pale.
        """
        rule = block(".fbox .note, .fbox .deny, .fbox .msg")
        self.assertIn("--text: #1A1D26", rule)
        self.assertIn("color: var(--text)", rule)

    def test_the_inputs_keep_a_light_face(self):
        """White type in a white box is a field that looks empty."""
        rule = block(".fbox input, .fbox select, .fbox textarea")
        self.assertIn("background: #FFFFFF", rule)
        self.assertIn("color: #1A1D26", rule)

    def test_focus_is_visible_against_navy(self):
        rule = block(".fbox input:focus, .fbox select:focus, .fbox textarea:focus")
        self.assertIn("var(--gold)", rule, "the light focus ring disappears on navy")


class SignInSurfaceTests(SimpleTestCase):
    def test_the_form_sits_on_the_panel_with_no_card(self):
        """
        A-1 asked for one unbroken navy, with nothing drawing a box around
        the thing they are trying to fill in.
        """
        rule = block(".fbox")
        self.assertIn("background: none", rule)
        self.assertIn("border: 0", rule)
        self.assertIn("box-shadow: none", rule)

    def test_both_sides_of_the_screen_are_navy(self):
        self.assertIn("background: var(--navy)", block(".login .rgt"))


class SignInLogoTests(SimpleTestCase):
    def test_the_lockup_is_large_enough_to_read_its_own_ring_text(self):
        """
        It carries the company's full name around the circle. At 72px that
        text was about four pixels tall.
        """
        rule = block(".logo-full")
        self.assertIn("width: 112px", rule)

    @tag("acceptance")
    def test_the_lockup_is_transparent_so_the_ring_text_shows(self):
        """
        An opaque version sat on the navy panel as a visible square, and the
        circle-only mark dropped the name entirely. This one is white on
        transparent: the ring text reads, and the "A" shows the panel
        through its own cut-out.
        """
        from PIL import Image

        logo = Image.open(ROOT / "static" / "img" / "a1-logo.png").convert("RGBA")
        width, height = logo.size

        self.assertEqual(
            logo.getpixel((4, 4))[3], 0, "the ground around the lockup must be transparent"
        )
        # The ring text sits in the band between the circle and the edge.
        band = [
            logo.getpixel((x, y))
            for y in range(int(height * 0.08), int(height * 0.22))
            for x in range(int(width * 0.3), int(width * 0.7))
        ]
        self.assertTrue(
            any(p[3] > 200 and p[0] > 200 for p in band),
            "the ring text must actually be drawn, in white",
        )

    def test_the_sign_in_page_serves_the_lockup_above_the_form(self):
        """
        The circle-only mark is still on this page — it sits beside "A1 360"
        in the left panel, where ring text at 32px would be mush. This is
        about the one above the form.
        """
        import re

        body = self.client.get(reverse("login"), secure=True).content.decode()
        above_form = re.search(r'<img class="logo-full" src="([^"]+)"', body)
        self.assertIsNotNone(above_form, "the form must carry a logo")
        self.assertIn("a1-logo", above_form.group(1))
