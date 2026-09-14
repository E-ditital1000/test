"""
My profile: each person's own page for their details, photo, password and
activity. What is pinned down here is what would do harm if it broke --
private details leaking into the audit log, a photo carrying its GPS
position, a password changing without the current one.
"""
import io
import shutil
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import AuditEntry, Profile, Role, UserRole

User = get_user_model()
PASSWORD = "Testing!12345"


def make_user(email, role_names=()):
    user = User.objects.create_user(
        username=email, email=email, password=PASSWORD, first_name="Moses", last_name="Toe"
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


def image_upload(fmt="JPEG", size=(1200, 800), gps=False, name="me.jpg"):
    from PIL import Image

    image = Image.new("RGB" if fmt == "JPEG" else "RGBA", size, (200, 30, 30))
    out = io.BytesIO()
    kwargs = {}
    if gps:
        exif = Image.Exif()
        # GPSInfo pointer with a latitude, as a phone camera writes it.
        exif[0x8825] = {1: "N", 2: (6.0, 18.0, 0.0)}
        kwargs["exif"] = exif
    image.save(out, format=fmt, **kwargs)
    content_type = {"JPEG": "image/jpeg", "PNG": "image/png"}[fmt]
    return SimpleUploadedFile(name, out.getvalue(), content_type=content_type)


VALID = {
    "date_of_birth": "1990-04-12",
    "personal_phone": "+231 770 123 456",
    "address": "Broad Street, Monrovia",
    "bio": "Electrical lead, Ganta yard.",
    "emergency_contact_name": "Ruth Toe",
    "emergency_contact_relationship": "Sister",
    "emergency_contact_phone": "+231 880 000 111",
    "linkedin_url": "linkedin.com/in/moses-toe",
    "x_url": "",
    "facebook_url": "",
    "website_url": "",
}


class ProfileAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.user = make_user("worker@test.local", ["Employee"])

    def test_signed_out_visitors_are_sent_to_sign_in(self):
        for name in ("profile", "profile-security", "profile-activity"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_every_role_has_a_profile(self):
        """Holding a profile is not a privilege; the least-privileged role opens all three."""
        self.client.force_login(self.user)
        for name in ("profile", "profile-security", "profile-activity"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_the_account_menu_links_to_the_profile(self):
        self.client.force_login(self.user)
        body = self.client.get(reverse("profile")).content.decode()
        self.assertIn(f'href="{reverse("profile")}"', body)
        self.assertIn(f'href="{reverse("profile-security")}"', body)

    def test_the_forced_first_password_reset_still_comes_first(self):
        self.user.must_reset_password = True
        self.user.save(update_fields=["must_reset_password"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("profile"))
        self.assertRedirects(response, reverse("password-reset-required"))

    def test_photo_endpoints_refuse_a_get(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("profile-photo")).status_code, 405)
        self.assertEqual(self.client.get(reverse("profile-photo-remove")).status_code, 405)


class ProfileDetailsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.user = make_user("worker@test.local", ["Employee"])

    def setUp(self):
        self.client.force_login(self.user)

    def post(self, **changes):
        return self.client.post(reverse("profile"), {**VALID, **changes})

    def test_details_save(self):
        response = self.post()
        self.assertRedirects(response, reverse("profile"))
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.date_of_birth, date(1990, 4, 12))
        self.assertEqual(profile.emergency_contact_name, "Ruth Toe")

    def test_a_link_pasted_without_https_is_accepted(self):
        self.post()
        self.assertEqual(
            Profile.objects.get(user=self.user).linkedin_url,
            "https://linkedin.com/in/moses-toe",
        )

    def test_the_audit_log_records_which_fields_changed_but_never_their_values(self):
        """Administrators read the log. A home address is none of their business."""
        self.post()
        entry = AuditEntry.objects.get(action="profile.updated")
        written = str(entry.before) + str(entry.after) + entry.reason
        self.assertIn("address", written)
        for private in ("Broad Street", "1990", "770 123", "Ruth"):
            self.assertNotIn(private, written)

    def test_saving_nothing_new_writes_no_audit_row(self):
        self.post()
        self.post()
        self.assertEqual(AuditEntry.objects.filter(action="profile.updated").count(), 1)

    def test_a_date_of_birth_in_the_future_is_refused(self):
        response = self.post(date_of_birth=f"{date.today().year + 1}-01-01")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has to be in the past")

    def test_an_implausible_age_is_refused(self):
        response = self.post(date_of_birth=f"{date.today().year - 3}-01-01")
        self.assertContains(response, "Check the year")

    def test_a_link_under_the_wrong_label_is_refused(self):
        response = self.post(linkedin_url="https://evil.example/linkedin.com")
        self.assertContains(response, "That is not a linkedin.com address")
        response = self.post(linkedin_url="https://linkedin.com.evil.example/in/x")
        self.assertContains(response, "That is not a linkedin.com address")

    def test_half_an_emergency_contact_is_refused(self):
        response = self.post(emergency_contact_phone="")
        self.assertContains(response, "Add a number for Ruth Toe")
        response = self.post(emergency_contact_name="")
        self.assertContains(response, "Whose number is this?")

    def test_a_phone_number_must_look_like_one(self):
        response = self.post(personal_phone="call me")
        self.assertContains(response, "Use digits and spaces")

    def test_what_is_typed_is_escaped(self):
        self.post(bio="<script>alert(1)</script>")
        body = self.client.get(reverse("profile")).content.decode()
        self.assertNotIn("<script>alert(1)</script>", body)


class ProfilePhotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.user = make_user("worker@test.local", ["Employee"])

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def upload(self, upload):
        return self.client.post(reverse("profile-photo"), {"photo": upload})

    def stored(self):
        from PIL import Image

        profile = Profile.objects.get(user=self.user)
        with profile.photo.open("rb") as handle:
            image = Image.open(io.BytesIO(handle.read()))
            image.load()
        return profile, image

    def test_a_photo_is_stored_as_a_small_square_jpeg(self):
        self.assertRedirects(self.upload(image_upload(size=(1600, 900))), reverse("profile"))
        _profile, image = self.stored()
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (400, 400))

    def test_the_gps_position_a_camera_writes_is_stripped(self):
        """A photo taken at home must not publish where the person lives."""
        self.upload(image_upload(gps=True))
        _profile, image = self.stored()
        self.assertNotIn(0x8825, image.getexif())

    def test_a_transparent_png_is_accepted(self):
        self.assertRedirects(self.upload(image_upload(fmt="PNG", name="me.png")), reverse("profile"))
        self.assertEqual(self.stored()[1].size, (400, 400))

    def test_a_file_that_is_not_a_photo_is_refused(self):
        fake = SimpleUploadedFile("me.jpg", b"not an image at all", content_type="image/jpeg")
        response = self.upload(fake)
        # Not followed here: following it would use up the message.
        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.assertFalse(Profile.objects.get(user=self.user).photo)
        body = self.client.get(reverse("profile")).content.decode()
        self.assertIn("toast-error", body)

    def test_the_photo_replaces_the_initials_in_the_header(self):
        self.upload(image_upload())
        profile = Profile.objects.get(user=self.user)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertIn(profile.photo.url, body)

    def test_replacing_a_photo_deletes_the_old_file(self):
        self.upload(image_upload())
        first = Profile.objects.get(user=self.user).photo
        storage, first_name = first.storage, first.name
        self.upload(image_upload())
        self.assertFalse(storage.exists(first_name))
        self.assertTrue(storage.exists(Profile.objects.get(user=self.user).photo.name))

    def test_removing_the_photo_removes_the_file(self):
        self.upload(image_upload())
        photo = Profile.objects.get(user=self.user).photo
        storage, name = photo.storage, photo.name
        self.client.post(reverse("profile-photo-remove"))
        self.assertFalse(Profile.objects.get(user=self.user).photo)
        self.assertFalse(storage.exists(name))


class ProfilePasswordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.user = make_user("worker@test.local", ["Employee"])

    def change(self, client, old=PASSWORD, new="A-much-better-one-2026"):
        return client.post(
            reverse("profile-security"),
            {"old_password": old, "new_password1": new, "new_password2": new},
        )

    def test_the_current_password_is_required(self):
        """An unlocked screen must not be enough to take an account over."""
        self.client.force_login(self.user)
        response = self.change(self.client, old="wrong")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_changing_it_keeps_this_device_and_signs_out_the_others(self):
        other = Client()
        other.force_login(self.user)
        self.client.force_login(self.user)

        self.assertRedirects(self.change(self.client), reverse("profile-security"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("A-much-better-one-2026"))

        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)
        self.assertEqual(other.get(reverse("profile")).status_code, 302)
        self.assertTrue(AuditEntry.objects.filter(action="profile.password_changed").exists())


class ProfileActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)
        cls.user = make_user("worker@test.local", ["Employee"])
        cls.admin = make_user("admin@test.local", ["Admin"])
        cls.stranger = make_user("stranger@test.local", ["Employee"])

    def test_it_lists_my_actions_and_changes_made_to_my_account_only(self):
        AuditEntry.objects.create(
            actor=self.admin, action="user.role_assigned",
            target_type="accounts.User", target_id=str(self.user.pk), reason="New starter",
        )
        AuditEntry.objects.create(
            actor=self.user, action="attendance.corrected",
            target_type="hr.ClockEvent", target_id="1",
        )
        AuditEntry.objects.create(
            actor=self.admin, action="user.role_assigned",
            target_type="accounts.User", target_id=str(self.stranger.pk), reason="Not mine",
        )

        self.client.force_login(self.user)
        body = self.client.get(reverse("profile-activity")).content.decode()
        self.assertIn("A role was added to your account", body)
        self.assertIn("New starter", body)
        self.assertIn("Attendance: corrected", body)
        self.assertNotIn("Not mine", body)
