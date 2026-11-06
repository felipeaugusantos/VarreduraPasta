import tempfile
import threading
import unittest
from pathlib import Path

from app import settings
from app.sqlaudit.file_scanner import scan_sql_files
from app.sqlaudit.version_discovery import database_name_for, discover_versions, is_version_name
from app.sqlaudit.version_tree import TreeError, VersionTree, effective_scripts, suggest_base
from tests.test_settings import SettingsTestCase

ROOT_NAME = "379.48.2.30"
KNOWN_DATABASES = {
    "48.2.30.72": "BANCO_AUTCOM_379_48_02_30_72",
    "48.2.30.74.111": "BANCO_AUTCOM_379_48_02_30_74_111",
    "48.2.30.74.150": "BANCO_AUTCOM_379_48_02_30_74_150",
    "48.2.30.74.150.45": "BANCO_AUTCOM_379_48_02_30_74_150_45",
    "48.2.30.74.150.100": "BANCO_AUTCOM_379_48_02_30_74_150_100",
}


class TempRootTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / ROOT_NAME
        self.root.mkdir()

    def make(self, relative, content=None):
        path = self.root / relative
        if content is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return path


class DatabaseNameTests(unittest.TestCase):
    def test_matches_the_five_known_databases(self):
        for version, expected in KNOWN_DATABASES.items():
            self.assertEqual(database_name_for(ROOT_NAME, version), expected)

    def test_unusable_names_give_no_suggestion(self):
        self.assertEqual(database_name_for("Versoes", "48.2.30.72"), "")
        self.assertEqual(database_name_for(ROOT_NAME, "backup"), "")

    def test_version_name_check(self):
        self.assertTrue(is_version_name("48.2.30.74.150.45"))
        self.assertFalse(is_version_name("Versões Descontinuada"))
        self.assertFalse(is_version_name("48"))


class DiscoveryTests(TempRootTestCase):
    def build_structure(self):
        for relative in (
            "48.2.30.72/SCRIPTS",
            "48.2.30.74.111/SCRIPTS",
            "Versões Descontinuada/48.2.30.74.150/SCRIPTS",
            "48.2.30.74.150.45/Scripts",
            "48.2.30.74.150.100/SCRIPTS",
            "48.2.30.72/Backup",
        ):
            self.make(relative)

    def test_finds_the_five_versions_with_suggested_databases(self):
        self.build_structure()
        result = discover_versions(self.root)
        self.assertEqual(
            [item.version for item in result.versions],
            ["48.2.30.72", "48.2.30.74.111", "48.2.30.74.150", "48.2.30.74.150.45", "48.2.30.74.150.100"],
        )
        self.assertEqual({item.version: item.database for item in result.versions}, KNOWN_DATABASES)
        self.assertEqual(result.warnings, [])

    def test_discontinued_folder_is_flagged_even_with_accents(self):
        self.build_structure()
        flags = {item.version: item.discontinued for item in discover_versions(self.root).versions}
        self.assertTrue(flags["48.2.30.74.150"])
        self.assertFalse(flags["48.2.30.72"])

    def test_scripts_folder_without_version_parent_is_reported_not_listed(self):
        self.build_structure()
        self.make("outros/SCRIPTS")
        result = discover_versions(self.root)
        self.assertEqual(len(result.versions), 5)
        self.assertTrue(any("outros" in warning for warning in result.warnings))

    def test_same_version_in_two_places_is_reported(self):
        self.build_structure()
        self.make("Versões Descontinuada/48.2.30.72/SCRIPTS")
        result = discover_versions(self.root)
        self.assertTrue(any("48.2.30.72 aparece em mais de uma pasta" in warning for warning in result.warnings))

    def test_missing_root_and_cancel(self):
        self.assertTrue(discover_versions(self.root / "x").warnings)
        self.build_structure()
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(discover_versions(self.root, cancel_event=cancel).versions, [])

    def test_does_not_descend_into_scripts_folders(self):
        self.make("48.2.30.72/SCRIPTS/48.2.30.99/SCRIPTS")
        self.assertEqual([item.version for item in discover_versions(self.root).versions], ["48.2.30.72"])


class SuggestBaseTests(unittest.TestCase):
    VERSIONS = list(KNOWN_DATABASES)

    def test_suggests_the_longest_known_prefix(self):
        self.assertEqual(suggest_base("48.2.30.74.150.45", self.VERSIONS), "48.2.30.74.150")
        self.assertEqual(suggest_base("48.2.30.74.150.100", self.VERSIONS), "48.2.30.74.150")

    def test_no_suggestion_when_no_prefix_is_known(self):
        for version in ("48.2.30.72", "48.2.30.74.111", "48.2.30.74.150"):
            self.assertIsNone(suggest_base(version, self.VERSIONS))

    def test_prefix_is_by_parts_not_by_text(self):
        self.assertIsNone(suggest_base("48.2.30.74.1500", ["48.2.30.74.150"]))


class VersionTreeTests(unittest.TestCase):
    def tree(self):
        return VersionTree.build(list(KNOWN_DATABASES))

    def test_build_uses_suggestions(self):
        tree = self.tree()
        self.assertEqual(tree.chain("48.2.30.74.150.45"), ["48.2.30.74.150", "48.2.30.74.150.45"])
        self.assertEqual(tree.chain("48.2.30.72"), ["48.2.30.72"])

    def test_operator_can_chain_versions_the_name_does_not_reveal(self):
        tree = self.tree()
        tree.set_base("48.2.30.74.111", "48.2.30.72", fork=10)
        tree.set_base("48.2.30.74.150", "48.2.30.74.111")
        self.assertEqual(
            tree.chain("48.2.30.74.150.100"),
            ["48.2.30.72", "48.2.30.74.111", "48.2.30.74.150", "48.2.30.74.150.100"],
        )

    def test_cycle_self_base_unknown_and_bad_fork_are_rejected_and_state_kept(self):
        tree = self.tree()
        tree.set_base("48.2.30.74.111", "48.2.30.72")
        with self.assertRaises(TreeError):
            tree.set_base("48.2.30.72", "48.2.30.74.111")
        self.assertIsNone(tree.nodes["48.2.30.72"].base)
        for args in (("48.2.30.72", "48.2.30.72"), ("48.2.30.72", "9.9"), ("9.9", None), ("48.2.30.74.111", "48.2.30.72", -1)):
            with self.assertRaises(TreeError):
                tree.set_base(*args)

    def test_removing_the_base_clears_the_fork(self):
        tree = self.tree()
        tree.set_base("48.2.30.74.150.45", "48.2.30.74.150", fork=5000)
        tree.set_base("48.2.30.74.150.45", None)
        self.assertIsNone(tree.nodes["48.2.30.74.150.45"].fork)

    def test_saved_configuration_overrides_suggestion_and_ignores_garbage(self):
        saved = {
            "48.2.30.74.150.45": {"base": "48.2.30.74.150", "fork": 5100},
            "48.2.30.74.150.100": {"base": "nao-existe", "fork": "x"},
            "versao-que-sumiu": {"base": "", "fork": None},
        }
        tree = VersionTree.build(list(KNOWN_DATABASES), saved)
        self.assertEqual(tree.nodes["48.2.30.74.150.45"].fork, 5100)
        self.assertEqual(tree.nodes["48.2.30.74.150.100"].base, "48.2.30.74.150")
        self.assertNotIn("versao-que-sumiu", tree.nodes)
        self.assertEqual(VersionTree.build(list(KNOWN_DATABASES), tree.to_saved()).to_saved(), tree.to_saved())


class EffectiveScriptsTests(TempRootTestCase):
    def scripts(self, layout):
        """layout: {versao: {nome: conteudo}} -> {versao: [ScriptFile]}"""
        result = {}
        for version, files in layout.items():
            for name, content in files.items():
                self.make(f"{version}/SCRIPTS/{name}", content)
            result[version] = scan_sql_files(self.root / version / "SCRIPTS").files
        return result

    def tree_150(self, fork=None):
        tree = VersionTree.build(["48.2.30.74.150", "48.2.30.74.150.45"])
        tree.set_base("48.2.30.74.150.45", "48.2.30.74.150", fork)
        return tree

    def names(self, effective):
        return [(item.version.split(".")[-1], item.script.relative, item.certain) for item in effective.scripts]

    def test_fork_limits_inherited_scripts_and_marks_them_certain(self):
        layout = {
            "48.2.30.74.150": {"SCRIPT_100.SQL": "a;", "SCRIPT_101.SQL": "b;", "SCRIPT_102.SQL": "c;"},
            "48.2.30.74.150.45": {"SCRIPT_103.SQL": "d;"},
        }
        effective = effective_scripts(self.tree_150(fork=101), self.scripts(layout), "48.2.30.74.150.45")
        self.assertEqual(
            self.names(effective),
            [("150", "SCRIPT_100.SQL", True), ("150", "SCRIPT_101.SQL", True), ("45", "SCRIPT_103.SQL", True)],
        )
        self.assertEqual(effective.excluded, [("48.2.30.74.150", "SCRIPT_102.SQL")])

    def test_unknown_fork_keeps_inherited_scripts_as_uncertain(self):
        layout = {
            "48.2.30.74.150": {"SCRIPT_100.SQL": "a;"},
            "48.2.30.74.150.45": {"SCRIPT_101.SQL": "d;"},
        }
        effective = effective_scripts(self.tree_150(), self.scripts(layout), "48.2.30.74.150.45")
        self.assertEqual(self.names(effective), [("150", "SCRIPT_100.SQL", False), ("45", "SCRIPT_101.SQL", True)])
        self.assertTrue(any("fork nao informado" in warning for warning in effective.warnings))

    def test_target_alone_has_only_its_own_certain_scripts(self):
        layout = {"48.2.30.74.150": {"SCRIPT_1.SQL": "a;"}, "48.2.30.74.150.45": {"SCRIPT_2.SQL": "b;"}}
        effective = effective_scripts(self.tree_150(), self.scripts(layout), "48.2.30.74.150")
        self.assertEqual(self.names(effective), [("150", "SCRIPT_1.SQL", True)])

    def test_copy_of_inherited_script_is_applied_once_and_becomes_certain(self):
        layout = {
            "48.2.30.74.150": {"SCRIPT_100.SQL": "same;"},
            "48.2.30.74.150.45": {"SCRIPT_100.SQL": "same;", "SCRIPT_101.SQL": "x;"},
        }
        effective = effective_scripts(self.tree_150(), self.scripts(layout), "48.2.30.74.150.45")
        self.assertEqual(self.names(effective), [("150", "SCRIPT_100.SQL", True), ("45", "SCRIPT_101.SQL", True)])
        self.assertTrue(any("repete" in warning for warning in effective.warnings))

    def test_same_number_with_different_content_is_warned_and_both_kept(self):
        layout = {
            "48.2.30.74.150": {"SCRIPT_100.SQL": "a;"},
            "48.2.30.74.150.45": {"SCRIPT_100.SQL": "b;"},
        }
        effective = effective_scripts(self.tree_150(fork=100), self.scripts(layout), "48.2.30.74.150.45")
        self.assertEqual(len(effective.scripts), 2)
        self.assertTrue(any("conteudo diferente" in warning for warning in effective.warnings))

    def test_child_numbering_below_the_base_still_comes_after_it(self):
        layout = {
            "48.2.30.74.150": {"SCRIPT_500.SQL": "a;"},
            "48.2.30.74.150.45": {"SCRIPT_3.SQL": "b;"},
        }
        effective = effective_scripts(self.tree_150(fork=500), self.scripts(layout), "48.2.30.74.150.45")
        self.assertEqual([item.script.number for item in effective.scripts], [500, 3])

    def test_unnumbered_scripts_are_never_certain(self):
        layout = {"48.2.30.74.150": {"ajuste.sql": "a;", "SCRIPT_1.SQL": "b;"}}
        effective = effective_scripts(self.tree_150(), self.scripts(layout), "48.2.30.74.150")
        self.assertEqual(self.names(effective), [("150", "SCRIPT_1.SQL", True), ("150", "ajuste.sql", False)])

    def test_three_level_chain_applies_each_fork_to_its_own_base(self):
        versions = ["48.2.30.72", "48.2.30.74.111", "48.2.30.74.150"]
        tree = VersionTree.build(versions)
        tree.set_base("48.2.30.74.111", "48.2.30.72", fork=10)
        tree.set_base("48.2.30.74.150", "48.2.30.74.111", fork=22)
        layout = {
            "48.2.30.72": {"SCRIPT_10.SQL": "a;", "SCRIPT_11.SQL": "b;"},
            "48.2.30.74.111": {"SCRIPT_21.SQL": "c;", "SCRIPT_22.SQL": "d;", "SCRIPT_23.SQL": "e;"},
            "48.2.30.74.150": {"SCRIPT_30.SQL": "f;"},
        }
        effective = effective_scripts(tree, self.scripts(layout), "48.2.30.74.150")
        self.assertEqual(
            [item.script.relative for item in effective.scripts],
            ["SCRIPT_10.SQL", "SCRIPT_21.SQL", "SCRIPT_22.SQL", "SCRIPT_30.SQL"],
        )
        self.assertTrue(all(item.certain for item in effective.scripts))


class SqlAuditSettingsTests(SettingsTestCase):
    def test_defaults(self):
        self.assertEqual(settings.load_sqlaudit_config(), {"root": "", "tree": {}, "databases": {}})

    def test_roundtrip_keeps_tree_fork_and_databases(self):
        tree = {"48.2.30.74.150.45": {"base": "48.2.30.74.150", "fork": 5100}}
        settings.save_sqlaudit_config(r"\\srv\379.48.2.30", tree, {"48.2.30.72": "BANCO_X", "vazio": " "})
        loaded = settings.load_sqlaudit_config()
        self.assertEqual(loaded["root"], r"\\srv\379.48.2.30")
        self.assertEqual(loaded["tree"], tree)
        self.assertEqual(loaded["databases"], {"48.2.30.72": "BANCO_X"})

    def test_invalid_values_are_discarded_and_other_settings_survive(self):
        settings.save_base_directory(r"C:\Outra")
        settings.save_sqlaudit_config("", {"v": {"base": "", "fork": True}}, {})
        self.assertIsNone(settings.load_sqlaudit_config()["tree"]["v"]["fork"])
        self.assertEqual(settings.load_base_directory(), Path(r"C:\Outra"))

    def test_server_defaults_to_the_model_database_server(self):
        settings.save_model_db("10.0.0.35", 43309, "modelo", "operador")
        server = settings.load_sqlaudit_server()
        self.assertEqual((server["host"], server["port"], server["username"], server["tls"]), ("10.0.0.35", 43309, "", "encrypt"))

    def test_server_roundtrip_and_tls_is_normalized(self):
        settings.save_sqlaudit_server("srv", "3307", "auditor", "verify", "C:\\ca.pem")
        self.assertEqual(
            settings.load_sqlaudit_server(),
            {"host": "srv", "port": 3307, "username": "auditor", "tls": "verify", "ca_file": "C:\\ca.pem"},
        )
        settings.save_sqlaudit_server("srv", "abc", "auditor", "qualquer-coisa")
        loaded = settings.load_sqlaudit_server()
        self.assertEqual((loaded["port"], loaded["tls"]), (3306, "encrypt"))

    def test_saving_the_tree_keeps_the_server_and_saving_the_server_keeps_the_tree(self):
        settings.save_sqlaudit_server("srv", 3306, "auditor")
        settings.save_sqlaudit_config("raiz", {"v": {"base": "", "fork": 3}}, {"v": "BANCO"})
        self.assertEqual(settings.load_sqlaudit_server()["username"], "auditor")
        settings.save_sqlaudit_server("outro", 3306, "auditor")
        config = settings.load_sqlaudit_config()
        self.assertEqual((config["root"], config["tree"]["v"]["fork"], config["databases"]), ("raiz", 3, {"v": "BANCO"}))

    def test_server_settings_have_no_password_field(self):
        settings.save_sqlaudit_server("srv", 3306, "auditor")
        text = settings.SETTINGS_FILE.read_text(encoding="utf-8").lower()
        self.assertNotIn("password", text)
        self.assertNotIn("senha", text)

    def test_never_stores_credentials(self):
        settings.save_sqlaudit_config("r", {}, {"v": "BANCO"})
        self.assertNotIn("password", settings.SETTINGS_FILE.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
