import unittest

from app.pattern import (
    _is_supported_file,
    _normalize_groups,
    pattern_key_from_project_name,
    pattern_scope_from_project_name,
)


class PatternKeyTests(unittest.TestCase):
    def test_key_keeps_first_six_parts(self):
        self.assertEqual(
            pattern_key_from_project_name("379.48.2.30.74.150.90"),
            "379.48.2.30.74.150",
        )

    def test_cloud_suffix_is_stripped(self):
        self.assertEqual(
            pattern_key_from_project_name("379.48.2.30.74.150.90_CLOUD"),
            "379.48.2.30.74.150",
        )

    def test_short_name_is_kept_as_is(self):
        self.assertEqual(pattern_key_from_project_name("pasta"), "pasta")


class PatternScopeTests(unittest.TestCase):
    def test_local_scope(self):
        self.assertEqual(
            pattern_scope_from_project_name("379.48.2.30.74.150.90"),
            "379.48.2.30.74.150|local",
        )

    def test_cloud_scope(self):
        self.assertEqual(
            pattern_scope_from_project_name("379.48.2.30.74.150.90_CLOUD"),
            "379.48.2.30.74.150|cloud",
        )


class SupportedFileTests(unittest.TestCase):
    def test_exe_and_dll_are_supported(self):
        self.assertTrue(_is_supported_file("Autcom.exe"))
        self.assertTrue(_is_supported_file("libAutban.dll"))

    def test_err_prefix_is_ignored(self):
        self.assertFalse(_is_supported_file("err_Autcom.exe"))

    def test_optional_files_are_ignored(self):
        self.assertFalse(_is_supported_file("libfuncoes.dll"))

    def test_other_extensions_are_ignored(self):
        self.assertFalse(_is_supported_file("leiame.txt"))
        self.assertFalse(_is_supported_file("autcom.zip"))


class NormalizeGroupsTests(unittest.TestCase):
    def test_empty_input_returns_none(self):
        self.assertIsNone(_normalize_groups(None))
        self.assertIsNone(_normalize_groups([]))

    def test_known_alternatives_are_expanded(self):
        groups = _normalize_groups(
            [
                {
                    "name": "Autban",
                    "accepted_files": ["libAutban.dll"],
                    "validate_version": True,
                }
            ]
        )
        self.assertIsNotNone(groups)
        accepted = groups[0]["accepted_files"]
        self.assertIn("libAutban.dll", accepted)
        self.assertIn("libAutban.exe", accepted)
        self.assertIn("AutBan.exe", accepted)

    def test_groups_without_accepted_files_are_dropped(self):
        groups = _normalize_groups(
            [{"name": "Vazio", "accepted_files": [], "validate_version": True}]
        )
        self.assertIsNone(groups)

    def test_validate_version_defaults_to_true(self):
        groups = _normalize_groups(
            [{"name": "Autcom", "accepted_files": ["Autcom.exe"]}]
        )
        self.assertTrue(groups[0]["validate_version"])


if __name__ == "__main__":
    unittest.main()
