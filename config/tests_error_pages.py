"""
The error pages, rendered as a stranger sees them.

Every mistyped URL on the live system answered **500 Internal Server
Error** instead of 404. The 404 and 403 templates extend the application
shell, and the shell renders

    {{ user.get_full_name|default:user.email }}

A filter *argument* that fails to resolve is not swallowed the way a plain
variable is — it raises `VariableDoesNotExist`. `AnonymousUser` has no
`email`, so every signed-out render of the shell died, and the page meant to
say "that record isn't here" was itself the error.

It survived because nothing tests a page nobody deliberately visits, and
because signed in — which is how anyone developing it is — the shell renders
perfectly.
"""
from django.test import SimpleTestCase, override_settings


@override_settings(DEBUG=False)
class ErrorPagesRenderForAStrangerTests(SimpleTestCase):
    """
    Signed out, and therefore with no user to put in the header. These are
    the only pages in the system an unauthenticated visitor can reach by
    accident, which is exactly why they have to work.
    """

    def test_a_missing_page_answers_not_found_and_not_a_server_error(self):
        response = self.client.get("/no-such-page-exists-here/", secure=True)
        self.assertEqual(
            response.status_code,
            404,
            "a mistyped URL must not report a server error — the 404 page "
            "renders the shell, and the shell used to raise when signed out",
        )

    def test_the_missing_page_still_says_something_useful(self):
        response = self.client.get("/no-such-page-exists-here/", secure=True)
        self.assertContains(response, "isn", status_code=404)

    def test_a_signed_out_visitor_is_offered_a_way_in(self):
        """
        The header has nowhere to put an account menu, so it offers the one
        action that is any use to them.
        """
        response = self.client.get("/no-such-page-exists-here/", secure=True)
        self.assertContains(response, "Sign in", status_code=404)

    def test_the_account_menu_is_not_rendered_for_a_stranger(self):
        """
        Asserted on the markup, not on the word: "user-sheet" also appears
        in the shell's own script, which is still shipped and now guards
        itself against the elements being absent.
        """
        response = self.client.get("/no-such-page-exists-here/", secure=True)
        self.assertNotContains(response, 'id="user-toggle"', status_code=404)
        self.assertNotContains(response, "Sign out", status_code=404)

    def test_the_shell_script_survives_having_no_account_menu(self):
        """
        The script reaches for #user-toggle and #user-sheet. Signed out they
        do not exist, and calling addEventListener on null throws — taking
        everything registered after it, including the Ctrl+K shortcut, with
        it.
        """
        response = self.client.get("/no-such-page-exists-here/", secure=True)
        self.assertContains(
            response, "if (!userToggle || !sheet) { return; }", status_code=404
        )
