import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("mcd_mcp", Path(__file__).parents[1] / "scripts/mcd_mcp.py")
mcd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcd)


class PersonaRenderTests(unittest.TestCase):
    def test_windows_user_and_system_chrome_without_path(self):
        for key, suffix in [("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
                            ("ProgramFiles", "Google/Chrome/Application/chrome.exe"),
                            ("ProgramFiles(x86)", "Google/Chrome/Application/chrome.exe")]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                executable = Path(root) / suffix
                executable.parent.mkdir(parents=True)
                executable.touch()
                with patch.dict(os.environ, {key: root}, clear=True), patch.object(mcd.sys, "platform", "win32"), patch("shutil.which", return_value=None):
                    self.assertEqual(mcd.find_chrome_executable(), str(executable))

    def test_chrome_on_path_wins(self):
        with patch("shutil.which", side_effect=lambda name: "/bin/chrome" if name == "chrome" else None):
            self.assertEqual(mcd.find_chrome_executable(), "/bin/chrome")

    def test_mac_standard_install(self):
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        with patch.object(mcd.sys, "platform", "darwin"), patch("shutil.which", return_value=None), patch("os.path.isfile", side_effect=lambda p: p == chrome):
            self.assertEqual(mcd.find_chrome_executable(), chrome)

    def test_missing_browser_does_not_invoke_process(self):
        with patch.object(mcd, "find_chrome_executable", return_value=None), patch("subprocess.run") as run:
            self.assertFalse(mcd.render_persona_png(Path("unused")))
            run.assert_not_called()

    def test_failed_render_does_not_report_or_replace_old_png(self):
        for failure in [subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired("chrome", 60)]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                home = Path(root)
                old = home / "persona-card.png"
                old.write_bytes(b"old" * 20000)
                with patch.object(mcd, "find_chrome_executable", return_value="chrome"), patch("subprocess.run", side_effect=failure if isinstance(failure, Exception) else None, return_value=failure):
                    self.assertFalse(mcd.render_persona_png(home))
                self.assertEqual(old.read_bytes(), b"old" * 20000)

    def test_successful_render_uses_file_uri_and_atomic_result(self):
        with tempfile.TemporaryDirectory(prefix="persona card ") as root:
            home = Path(root)
            (home / "card.html").write_text("<html></html>")
            def render(argv, **kwargs):
                self.assertEqual(argv[-1], (home / "card.html").resolve().as_uri())
                screenshot = next(x.split("=", 1)[1] for x in argv if x.startswith("--screenshot="))
                self.assertNotEqual(screenshot, str(home / "persona-card.png"))
                Path(screenshot).write_bytes(b"png" * 15000)
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(mcd, "find_chrome_executable", return_value="chrome"), patch("subprocess.run", side_effect=render):
                self.assertTrue(mcd.render_persona_png(home))
            self.assertEqual((home / "persona-card.png").stat().st_size, 45000)


if __name__ == "__main__":
    unittest.main()
