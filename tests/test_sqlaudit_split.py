import unittest

from app.model_db import split_statements
from app.sqlaudit.sql_split import split_with_positions


def texts(sql):
    return [statement.text for statement in split_with_positions(sql)]


class SplitPositionTests(unittest.TestCase):
    def test_lines_and_offsets_point_to_the_original_text(self):
        sql = "-- cabecalho\nCREATE TABLE a (\n  id INT\n);\n\nALTER TABLE a\n  ADD b INT;\n"
        first, second = split_with_positions(sql)
        self.assertEqual((first.line_start, first.line_end), (2, 4))
        self.assertEqual((second.line_start, second.line_end), (6, 7))
        self.assertTrue(sql[first.start : first.end].startswith("CREATE TABLE a"))
        self.assertTrue(sql[second.start : second.end].endswith("ADD b INT"))
        self.assertEqual([first.index, second.index], [1, 2])

    def test_line_of_a_statement_after_a_comment_block(self):
        sql = "/* bloco\nde\ncomentario */\nSELECT 1;"
        (statement,) = split_with_positions(sql)
        self.assertEqual(statement.line_start, 4)

    def test_comments_are_removed_and_separate_words(self):
        self.assertEqual(texts("SELECT/**/1; # x\nSELECT 2 -- fim"), ["SELECT 1", "SELECT 2"])

    def test_dash_dash_without_space_is_not_a_comment(self):
        self.assertEqual(texts("SELECT 5--3;"), ["SELECT 5--3"])

    def test_text_and_backticks_hide_semicolons_and_comment_marks(self):
        sql = "INSERT INTO t VALUES ('a;b', \"c#d\", `e;f`);INSERT INTO t VALUES ('it''s; ok', 'x\\'y;z');"
        self.assertEqual(len(texts(sql)), 2)

    def test_delimiter_with_indentation_keeps_procedure_whole(self):
        sql = "  DELIMITER $$\nCREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\nEND$$\nDELIMITER ;\nCALL p();"
        statements = split_with_positions(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("SELECT 1;", statements[0].text)
        self.assertEqual(statements[1].text, "CALL p()")
        self.assertEqual(statements[1].line_start, 7)

    def test_delimiter_word_in_the_middle_of_a_statement_is_not_a_command(self):
        self.assertEqual(texts("SELECT delimiter FROM t;"), ["SELECT delimiter FROM t"])

    def test_executable_comment_is_kept(self):
        self.assertEqual(texts("/*!40101 SET x=1 */;"), ["/*!40101 SET x=1 */"])

    def test_empty_and_comment_only_scripts(self):
        self.assertEqual(split_with_positions("  \n-- so comentario\n"), [])
        self.assertEqual(split_with_positions(""), [])

    def test_last_statement_without_semicolon(self):
        self.assertEqual(texts("SELECT 1"), ["SELECT 1"])


class SplitProblemTests(unittest.TestCase):
    def test_unclosed_quote_is_flagged(self):
        (statement,) = split_with_positions("INSERT INTO t VALUES ('abc;\nSELECT 2;")
        self.assertEqual(statement.problem, "aspas nao fechadas")

    def test_unclosed_block_comment_is_flagged(self):
        (statement,) = split_with_positions("SELECT 1 /* sem fim; SELECT 2;")
        self.assertEqual(statement.problem, "comentario /* */ nao fechado")

    def test_problem_does_not_leak_into_the_next_statement(self):
        statements = split_with_positions("SELECT 'a;\nSELECT 1;")
        self.assertEqual([s.problem for s in statements], ["aspas nao fechadas"])
        clean = split_with_positions("SELECT 1; SELECT 2;")
        self.assertEqual([s.problem for s in clean], ["", ""])


class SplitConsistencyTests(unittest.TestCase):
    def test_same_statements_as_the_execution_splitter(self):
        samples = [
            "-- c\nCREATE TABLE a (id INT); # x\n/* b\n; */ INSERT INTO a VALUES (1);\n",
            "INSERT INTO t VALUES ('a;b', \"c;d\", `e;f`);\nINSERT INTO t VALUES ('it''s; ok', 'x\\'y;z');",
            "DELIMITER //\nCREATE PROCEDURE p()\nBEGIN\n SELECT 1;\n SELECT 2;\nEND//\nDELIMITER ;\nCALL p();\n",
            "SELECT 5--3;",
            "/*!40101 SET x=1 */;",
        ]
        for sample in samples:
            self.assertEqual(texts(sample), split_statements(sample), sample)

    def test_many_statements_in_a_large_script(self):
        sql = "".join(f"ALTER TABLE t ADD COLUMN c{i} INT;\n" for i in range(20000))
        statements = split_with_positions(sql)
        self.assertEqual(len(statements), 20000)
        self.assertEqual(statements[-1].line_start, 20000)


if __name__ == "__main__":
    unittest.main()
