#!/usr/bin/env python3
"""The 4CLOP settings menu: writing the login, and saying what state she is in.

Two things are worth pinning here.

The first is that `_save_env_value` edits the file that also holds the user's LLM API key.
A bug in it does not fail loudly -- it silently eats a key the user then has to go and find
again. So the tests assert on the surrounding lines, not just on the line being written.

The second is `_clop_status`. "It is off" and "your password is wrong" are the same thing
to the code and completely different things to the person reading the menu, and the whole
reason this menu exists is that neither was being said at all.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clop_monitor"))

try:  # PyQt5 is not needed to run the rest of the suite, so this file skips without it.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from desktop_pet.context_menu import ContextMenuBuilder, _save_env_value
    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the machine, not the code
    HAVE_QT = False


ORIGINAL_ENV = """# Copy this to .env and fill in your keys
BONZI_LLM_API_KEY=sk-the-users-actual-key
BONZI_LLM_PROVIDER=openai

BONZI_ELEVENLABS_API_KEY=
"""


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class EnvWritingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = Path(self.dir.name) / ".env"
        self.env.write_text(ORIGINAL_ENV, encoding="utf-8")
        self.addCleanup(self.dir.cleanup)
        for name in ("CLOP_USERNAME", "CLOP_PASSWORD"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)

    def test_it_appends_without_touching_what_was_there(self):
        _save_env_value("CLOP_USERNAME", "anonymousmare", str(self.env))
        text = self.env.read_text()
        self.assertIn("CLOP_USERNAME=anonymousmare", text)
        # The point of the test: the key that was already there is still there.
        self.assertIn("BONZI_LLM_API_KEY=sk-the-users-actual-key", text)
        self.assertIn("BONZI_LLM_PROVIDER=openai", text)

    def test_rewriting_a_value_does_not_duplicate_the_line(self):
        _save_env_value("CLOP_PASSWORD", "first", str(self.env))
        _save_env_value("CLOP_PASSWORD", "second", str(self.env))
        text = self.env.read_text()
        self.assertEqual(1, text.count("CLOP_PASSWORD"))
        self.assertIn("CLOP_PASSWORD=second", text)

    def test_clearing_removes_the_line_rather_than_blanking_it(self):
        # A blank CLOP_USERNAME= would read back as an empty string rather than as absent,
        # and the status line would then say "enabled but no password" forever.
        _save_env_value("CLOP_USERNAME", "someone", str(self.env))
        _save_env_value("CLOP_USERNAME", "", str(self.env))
        self.assertNotIn("CLOP_USERNAME", self.env.read_text())

    def test_setting_then_clearing_restores_the_file_exactly(self):
        _save_env_value("CLOP_USERNAME", "someone", str(self.env))
        _save_env_value("CLOP_PASSWORD", "hunter2", str(self.env))
        _save_env_value("CLOP_USERNAME", "", str(self.env))
        _save_env_value("CLOP_PASSWORD", "", str(self.env))
        self.assertEqual(ORIGINAL_ENV, self.env.read_text())

    def test_it_creates_the_file_when_there_is_none(self):
        fresh = Path(self.dir.name) / "brand-new.env"
        self.assertTrue(_save_env_value("CLOP_USERNAME", "someone", str(fresh)))
        self.assertEqual("CLOP_USERNAME=someone\n", fresh.read_text())

    def test_it_handles_export_prefixes_and_a_missing_final_newline(self):
        odd = Path(self.dir.name) / "odd.env"
        odd.write_text("export CLOP_USERNAME=old\nOTHER=keep", encoding="utf-8")
        _save_env_value("CLOP_USERNAME", "new", str(odd))
        _save_env_value("CLOP_PASSWORD", "pw", str(odd))
        text = odd.read_text()
        self.assertIn("CLOP_USERNAME=new", text)
        self.assertNotIn("old", text)
        self.assertIn("OTHER=keep", text)
        self.assertTrue(text.endswith("\n"))

    def test_a_commented_out_line_is_left_alone(self):
        commented = Path(self.dir.name) / "c.env"
        commented.write_text("# CLOP_USERNAME=example\n", encoding="utf-8")
        _save_env_value("CLOP_USERNAME", "real", str(commented))
        text = commented.read_text()
        self.assertIn("# CLOP_USERNAME=example", text)
        self.assertIn("CLOP_USERNAME=real", text)

    def test_the_value_reaches_the_environment_too(self):
        _save_env_value("CLOP_USERNAME", "someone", str(self.env))
        self.assertEqual("someone", os.environ.get("CLOP_USERNAME"))
        _save_env_value("CLOP_USERNAME", "", str(self.env))
        self.assertIsNone(os.environ.get("CLOP_USERNAME"))


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class StatusLineTests(unittest.TestCase):
    """What the menu tells you when she is not watching the game."""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from core.config_loader import load_config

        root = Path(__file__).resolve().parent.parent
        self.config = load_config(root / "config.yaml.example")
        self.menu = ContextMenuBuilder(config=self.config,
                                       config_path=str(root / "config.yaml.example"))
        for name in ("CLOP_USERNAME", "CLOP_PASSWORD"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)

    def _login(self, user="anonymousmare", password="x"):
        os.environ["CLOP_USERNAME"] = user
        os.environ["CLOP_PASSWORD"] = password

    def test_a_fresh_install_says_there_is_no_login(self):
        self.assertIn("no login", self.menu._clop_status())

    def test_a_login_with_the_feature_off_says_to_switch_it_on(self):
        self._login()
        status = self.menu._clop_status()
        self.assertIn("Off", status)
        self.assertIn("Enabled", status)

    def test_switched_on_but_never_connected_asks_for_a_restart(self):
        self._login()
        self.config.clop.enabled = True
        self.assertIn("restart", self.menu._clop_status().lower())

    def test_a_login_failure_is_reported_rather_than_looking_like_off(self):
        self._login()
        self.config.clop.enabled = True

        class Failed:
            available = False
            last_error = "Could not log in to CLOP: bad username or password"

        self.menu.clop_bridge = Failed()
        self.assertIn("bad username", self.menu._clop_status())

    def test_a_half_filled_login_names_the_missing_half(self):
        os.environ["CLOP_USERNAME"] = "anonymousmare"
        self.config.clop.enabled = True
        self.assertIn("password", self.menu._clop_status())

    def test_connected_says_who_she_is_logged_in_as(self):
        self._login()
        self.config.clop.enabled = True

        class Up:
            available = True
            last_error = None

        self.menu.clop_bridge = Up()
        self.assertIn("anonymousmare", self.menu._clop_status())

    def test_the_submenu_offers_clear_only_when_there_is_something_to_clear(self):
        from PyQt5.QtWidgets import QMenu, QWidget

        parent = QWidget()

        def entries():
            holder = QMenu(parent)
            self.menu._build_clop_menu(holder, parent)
            sub = [a for a in holder.actions() if a.text() == "4CLOP"][0].menu()
            return [a.text() for a in sub.actions()]

        self.assertNotIn("Clear Login", entries())
        self._login()
        self.assertIn("Clear Login", entries())

    def test_every_clop_setting_is_reachable_from_the_menu(self):
        """The gap this menu was built to close: a setting you can only reach by
        hand-editing a file might as well not exist."""
        from PyQt5.QtWidgets import QMenu, QWidget

        parent = QWidget()
        holder = QMenu(parent)
        self.menu._build_clop_menu(holder, parent)
        sub = [a for a in holder.actions() if a.text() == "4CLOP"][0].menu()
        labels = " ".join(a.text() for a in sub.actions())
        for expected in ("Enabled", "Set Login", "Speak Alerts",
                         "Strategy Thoughts", "Thread", "Alliance Chat"):
            self.assertIn(expected, labels)


if __name__ == "__main__":
    unittest.main()
