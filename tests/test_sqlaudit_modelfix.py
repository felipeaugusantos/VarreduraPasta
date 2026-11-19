"""`Gerar script do que falta`: so os comandos dos objetos que faltam, para revisao. Nunca executa."""

import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.sqlaudit import sql_parse
from app.sqlaudit.model_check import STATUS_MISSING, STATUS_OK, check_operations
from app.sqlaudit.model_fix import (
    build_missing_script,
    build_plan_script,
    create_table_from_operation,
    write_missing_script,
    write_plan_script,
)
from tests.test_sqlaudit_compare import col, db_schema, db_table, fk, idx

AVAILABLE = sql_parse.is_available()

WS_584 = """
-- Tabela para o Produto "Pai", que agrupa as variacoes.
CREATE TABLE CONECTA_VAR_PAI (
    VAP_CODPAI VARCHAR(7) NOT NULL COMMENT 'Codigo unico do produto pai.',
    VAP_NOMPAI VARCHAR(255) DEFAULT NULL COMMENT 'Nome do produto pai.',
    PRIMARY KEY (VAP_CODPAI)
) ENGINE=INNODB DEFAULT CHARSET=latin1;

CREATE TABLE CONECTA_VAR_FILHO (
    AUTOINCREM VARCHAR(20) NOT NULL,
    VAF_CODPAI VARCHAR(7) NOT NULL,
    PRIMARY KEY(AUTOINCREM),
    UNIQUE KEY (VAF_CODPAI),
    KEY IDX_FILHO_PAI (VAF_CODPAI),
    CONSTRAINT FK_FILHO_PAI FOREIGN KEY (VAF_CODPAI) REFERENCES CONECTA_VAR_PAI (VAP_CODPAI) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=latin1;

-- Adiciona as novas colunas
ALTER TABLE CONECTA_MAP_PRODUTOS
ADD COLUMN MAP_CODPAI VARCHAR(7) DEFAULT NULL COMMENT 'FK da CONECTA_VAR_PAI.' AFTER MAP_CODPLA,
ADD COLUMN MAP_PAIPLA VARCHAR(50) DEFAULT NULL COMMENT 'ID do produto pai na plataforma.' AFTER MAP_CODPAI;

ALTER TABLE CONECTA_MAP_PRODUTOS
DROP FOREIGN KEY FK_MAP_PROD_INTEGRACOES;

ALTER TABLE CONECTA_MAP_PRODUTOS
DROP INDEX IDX_MAP_01,
DROP INDEX IDX_MAP_02;

ALTER TABLE CONECTA_MAP_PRODUTOS
ADD UNIQUE KEY UK_MAP_03 (MAP_CODAUT, MAP_CODITE, MAP_CODPAI);

UPDATE CONECTA_MAP_PRODUTOS SET MAP_PAIPLA = '' WHERE MAP_PAIPLA IS NULL;

ALTER TABLE CONECTA_MAP_PRODUTOS
ADD CONSTRAINT FK_MAP_VAR_PAI FOREIGN KEY (MAP_CODPAI) REFERENCES CONECTA_VAR_PAI (VAP_CODPAI);

ALTER TABLE CONECTA_ITELIB ADD COLUMN CONECTA_CODPAI VARCHAR(7) DEFAULT NULL;
"""


def parsed(**scripts):
    return [(name, sql_parse.parse_text(text)) for name, text in scripts.items()]


def check(schema, script=WS_584, name="SCRIPT_WS_584.sql"):
    return {c.script: c for c in check_operations(parsed(**{name: script}), schema, "BANCO_MODELO")}[name]


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class MissingScriptTests(unittest.TestCase):
    def partial_schema(self):
        """As 2 tabelas novas ja existem e o final do script ja rodou; o bloco de CONECTA_MAP_PRODUTOS nao."""
        return db_schema(
            db_table("CONECTA_VAR_PAI", col("VAP_CODPAI", "varchar(7)", null=False, comment="Codigo unico do produto pai."),
                     col("VAP_NOMPAI", "varchar(255)", comment="Nome do produto pai."), indexes=[idx("PRIMARY", "VAP_CODPAI", unique=True)]),
            db_table("CONECTA_VAR_FILHO", col("AUTOINCREM", "varchar(20)", null=False), col("VAF_CODPAI", "varchar(7)", null=False),
                     indexes=[idx("PRIMARY", "AUTOINCREM", unique=True), idx("VAF_CODPAI", "VAF_CODPAI", unique=True), idx("IDX_FILHO_PAI", "VAF_CODPAI")],
                     foreign_keys=[fk("FK_FILHO_PAI", ["VAF_CODPAI"], "CONECTA_VAR_PAI", ["VAP_CODPAI"], delete="CASCADE")]),
            db_table("CONECTA_MAP_PRODUTOS", col("AUTOINCREM", "varchar(20)", null=False), col("MAP_CODAUT", "varchar(7)", null=False),
                     col("MAP_CODITE", "varchar(7)", null=False), col("MAP_CODPLA", "varchar(50)", null=False),
                     indexes=[idx("IDX_MAP_01", "MAP_CODAUT"), idx("IDX_MAP_02", "MAP_CODITE")],
                     foreign_keys=[fk("FK_MAP_PROD_INTEGRACOES", ["MAP_CODAUT"], "CONECTA_INTEGRACOES", ["INT_CODAUT"])]),
            db_table("CONECTA_ITELIB", col("CONECTA_CODPAI", "varchar(7)")),
        )

    def live(self, text):
        return [line for line in text.splitlines() if line.strip() and not line.startswith("--")]

    def test_only_what_is_missing_is_generated_in_the_order_of_the_script(self):
        item = check(self.partial_schema())
        self.assertEqual(item.status, STATUS_MISSING)
        text = build_missing_script(item, database="BANCO_MODELO", user="ana", now=datetime(2026, 11, 18, 10, 0))
        live = self.live(text)
        joined = "\n".join(live)
        self.assertNotIn("CREATE TABLE", joined)  # as tabelas ja existem: o erro 1050 que o script inteiro daria
        self.assertNotIn("CONECTA_ITELIB", joined)  # esse ALTER ja rodou
        statements = [line for line in live if line.startswith("ALTER TABLE")]
        self.assertEqual(
            statements,
            [
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` ADD COLUMN `MAP_CODPAI` varchar(7) DEFAULT NULL COMMENT 'FK da CONECTA_VAR_PAI.' AFTER `MAP_CODPLA`;",
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` ADD COLUMN `MAP_PAIPLA` varchar(50) DEFAULT NULL COMMENT 'ID do produto pai na plataforma.' AFTER `MAP_CODPAI`;",
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` DROP FOREIGN KEY `FK_MAP_PROD_INTEGRACOES`;",
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` DROP INDEX `IDX_MAP_01`;",
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` DROP INDEX `IDX_MAP_02`;",
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` ADD UNIQUE INDEX `UK_MAP_03` (`MAP_CODAUT`, `MAP_CODITE`, `MAP_CODPAI`);",
                "ALTER TABLE `CONECTA_MAP_PRODUTOS` ADD CONSTRAINT `FK_MAP_VAR_PAI` FOREIGN KEY (`MAP_CODPAI`) REFERENCES `CONECTA_VAR_PAI` (`VAP_CODPAI`);",
            ],
        )
        self.assertIn("-- Script de origem: SCRIPT_WS_584.sql", text)
        self.assertIn("por ana", text)
        self.assertIn("-- USE `BANCO_MODELO`;", text)

    def test_every_generated_statement_is_fully_understood_by_the_system_parser(self):
        text = build_missing_script(check(self.partial_schema()))
        for statement in self.live(text):
            operations = sql_parse.parse_text(statement)
            self.assertTrue(operations and all(op.interpreted for op in operations), statement)

    def test_statements_the_system_does_not_check_stay_commented_in_their_original_position(self):
        text = build_missing_script(check(self.partial_schema()))
        lines = text.splitlines()
        position = {
            "update": next(i for i, line in enumerate(lines) if "UPDATE CONECTA_MAP_PRODUTOS SET MAP_PAIPLA" in line),
            "uk": next(i for i, line in enumerate(lines) if "ADD UNIQUE INDEX `UK_MAP_03`" in line),
            "fk": next(i for i, line in enumerate(lines) if "ADD CONSTRAINT `FK_MAP_VAR_PAI`" in line),
        }
        self.assertTrue(lines[position["update"]].startswith("-- "))  # comentado
        self.assertLess(position["uk"], position["update"])  # entre o indice e a chave, como no original
        self.assertLess(position["update"], position["fk"])
        self.assertIn("nao conferido pelo sistema", text)

    def test_missing_tables_are_created_from_the_script_definition_with_options(self):
        item = check(db_schema(db_table("CONECTA_MAP_PRODUTOS", col("MAP_CODPLA", "varchar(50)"))))
        text = build_missing_script(item)
        live = "\n".join(self.live(text))
        self.assertIn("CREATE TABLE `CONECTA_VAR_PAI` (", live)
        self.assertIn("PRIMARY KEY (`VAP_CODPAI`)", live)
        self.assertIn(") ENGINE=innodb DEFAULT CHARSET=latin1;", live)
        self.assertIn("CREATE TABLE `CONECTA_VAR_FILHO` (", live)
        self.assertIn("CONSTRAINT `FK_FILHO_PAI` FOREIGN KEY (`VAF_CODPAI`) REFERENCES `CONECTA_VAR_PAI` (`VAP_CODPAI`) ON DELETE CASCADE", live)

    def test_a_rebuilt_create_table_keeps_columns_keys_and_comments(self):
        op = next(o for o in sql_parse.parse_text(WS_584) if o.kind == "create_table" and o.table == "CONECTA_VAR_FILHO")
        again = sql_parse.parse_text(create_table_from_operation(op))
        (rebuilt,) = [o for o in again if o.kind == "create_table"]
        self.assertEqual([c.name for c in rebuilt.columns], [c.name for c in op.columns])
        self.assertEqual(rebuilt.primary_key, op.primary_key)
        self.assertEqual([(i.name, i.columns, i.unique) for i in rebuilt.indexes], [(i.name, i.columns, i.unique) for i in op.indexes])
        self.assertEqual([(f.name, f.ref_table, f.on_delete) for f in rebuilt.foreign_keys], [(f.name, f.ref_table, f.on_delete) for f in op.foreign_keys])
        pai = next(o for o in sql_parse.parse_text(WS_584) if o.kind == "create_table" and o.table == "CONECTA_VAR_PAI")
        rebuilt_pai = [o for o in sql_parse.parse_text(create_table_from_operation(pai)) if o.kind == "create_table"][0]
        self.assertEqual([c.comment for c in rebuilt_pai.columns], ["Codigo unico do produto pai.", "Nome do produto pai."])

    def test_a_modify_of_a_column_that_does_not_exist_is_not_invented_and_names_who_creates_it(self):
        schema = db_schema(db_table("T", col("A", "int")))
        results = {c.script: c for c in check_operations(
            parsed(**{"WS_1.sql": "ALTER TABLE T ADD COLUMN X INT;", "WS_2.sql": "ALTER TABLE T MODIFY X INT NOT NULL;"}), schema, "B")}
        text = build_missing_script(results["WS_2.sql"])
        self.assertEqual(self.live(text), [])  # nada executavel
        self.assertIn("NAO GERADO", text)
        self.assertIn("falta rodar WS_1.sql", text)
        self.assertIn("ANTES deste arquivo, rode os scripts que criam o que ele usa: WS_1.sql", text)

    def test_index_that_exists_with_other_columns_is_recreated_in_one_statement(self):
        schema = db_schema(db_table("T", col("A", "int"), col("B", "int"), indexes=[idx("UK", "A", unique=True)]))
        item = check(schema, "ALTER TABLE T ADD UNIQUE KEY UK (A, B);", "s.sql")
        text = build_missing_script(item)
        self.assertEqual(self.live(text), ["ALTER TABLE `T` DROP INDEX `UK`, ADD UNIQUE INDEX `UK` (`A`, `B`);"])
        self.assertIn("recria o indice", text)

    def test_destructive_removals_are_commented_and_safe_ones_are_not(self):
        schema = db_schema(db_table("T", col("A", "int"), col("X", "int"), indexes=[idx("IX", "A")]), db_table("V", col("A", "int")))
        item = check(schema, "ALTER TABLE T DROP COLUMN X; ALTER TABLE T DROP INDEX IX; DROP TABLE V;", "s.sql")
        text = build_missing_script(item)
        self.assertEqual(self.live(text), ["ALTER TABLE `T` DROP INDEX `IX`;"])
        self.assertIn("-- ALTER TABLE `T` DROP COLUMN `X`;", text)
        self.assertIn("-- DROP TABLE `V`;", text)
        self.assertIn("COMENTADO", text)

    def test_only_scripts_that_fall_short_have_something_to_generate(self):
        ok = check(db_schema(db_table("T", col("A", "int"))), "ALTER TABLE T ADD COLUMN A INT;", "s.sql")
        self.assertEqual(ok.status, STATUS_OK)
        with self.assertRaises(ValueError):
            build_missing_script(ok)

    def test_write_is_atomic_utf8_and_only_sql_and_never_contains_secrets(self):
        item = check(self.partial_schema())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "falta.sql"
            path.write_text("antigo", encoding="utf-8")
            text = write_missing_script(item, path, database="BANCO_MODELO", user="ana")
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertNotIn("\r", path.read_bytes().decode("utf-8"))
            with self.assertRaises(ValueError):
                write_missing_script(item, Path(folder) / "falta.txt")
            with self.assertRaises(OSError):
                write_missing_script(item, Path(folder) / "nao-existe" / "x.sql")
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["falta.sql"])
        lowered = text.lower()
        for forbidden in ("password", "senha", "sha256", "expectedmodel"):
            self.assertNotIn(forbidden, lowered)

    def test_missing_items_keep_the_operation_without_affecting_equality_or_repr(self):
        item = check(self.partial_schema())
        first = item.missing[0]
        self.assertIsNotNone(first.op)
        self.assertNotIn("Operation(", repr(first))
        self.assertEqual(first, type(first)(first.kind, first.table, first.name, first.detail, first.providers))


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class PlanTests(unittest.TestCase):
    SCRIPTS = {
        "WS_10.sql": "ALTER TABLE T ADD COLUMN B INT;",
        "WS_2.sql": "ALTER TABLE T ADD COLUMN A INT;",
        "WS_11.sql": "ALTER TABLE T ADD COLUMN C INT; ALTER TABLE T MODIFY A INT NOT NULL;",
        "WS_3.sql": "ALTER TABLE T ADD COLUMN Z INT;",
    }

    def results(self, **scripts):
        schema = db_schema(db_table("T", col("Z", "int")))
        return check_operations(parsed(**(scripts or self.SCRIPTS)), schema, "BANCO_MODELO")

    def test_one_file_with_every_pending_script_in_the_suggested_order(self):
        results = self.results()
        text = build_plan_script(results, database="BANCO_MODELO", user="ana", now=datetime(2026, 11, 18, 10, 0))
        sections = re.findall(r"^-- (\d+)/(\d+)  (\S+)$", text, re.MULTILINE)
        # WS_3 esta OK (Z existe): fora. WS_11 usa A, criado por WS_2: vem depois dele. Os demais, em ordem numerica.
        self.assertEqual([name for _n, _t, name in sections], ["WS_2.sql", "WS_10.sql", "WS_11.sql"])
        self.assertEqual({total for _n, total, _name in sections}, {"3"})
        self.assertIn("-- depois de: WS_2.sql", text)
        live = [line for line in text.splitlines() if line.strip() and not line.startswith("--")]
        self.assertEqual(live[0], "ALTER TABLE `T` ADD COLUMN `A` int;")
        self.assertIn("ALTER TABLE `T` ADD COLUMN `C` int;", live)
        self.assertNotIn("`Z`", "\n".join(live))  # o que ja existe nao entra
        self.assertIn("-- USE `BANCO_MODELO`;", text)
        self.assertIn("por ana", text)
        self.assertIn("PLANO DO QUE FALTA RODAR", text)
        for statement in live:
            operations = sql_parse.parse_text(statement)
            self.assertTrue(operations and all(op.interpreted for op in operations), statement)

    def test_nothing_pending_means_nothing_to_generate(self):
        with self.assertRaises(ValueError):
            build_plan_script(self.results(**{"ok.sql": "ALTER TABLE T ADD COLUMN Z INT;"}))

    def test_the_plan_never_includes_copies_or_ok_scripts_and_writes_atomically(self):
        results = self.results()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plano.sql"
            path.write_text("antigo", encoding="utf-8")
            text = write_plan_script(results, path, database="B", user="ana")
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertNotIn("WS_3.sql\n", "\n".join(line for line in text.splitlines() if re.match(r"^-- \d+/\d+", line)))
            with self.assertRaises(ValueError):
                write_plan_script(results, Path(folder) / "plano.txt")
            with self.assertRaises(OSError):
                write_plan_script(results, Path(folder) / "nao-existe" / "x.sql")
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["plano.sql"])
        for forbidden in ("password", "senha", "sha256"):
            self.assertNotIn(forbidden, text.lower())


if __name__ == "__main__":
    unittest.main()
