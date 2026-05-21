import pytest

from app.core.exceptions import (
    ColunasDivergentesError,
    InsertsIncompativeisError,
    SqlNaoSuportadoError,
    SqlParseError,
)
from app.core.parser_insert import parsear_insert


def test_insert_simples_com_colunas():
    d = parsear_insert("INSERT INTO clientes (id, nome) VALUES (1, 'Ana');")
    assert d.tabela == "clientes"
    assert d.colunas == ["id", "nome"]
    assert d.linhas == [["1", "'Ana'"]]


def test_insert_simples_sem_colunas():
    d = parsear_insert("INSERT INTO logs VALUES (1, 'ok', NULL);")
    assert d.colunas is None
    assert d.nomes_campos() == ["col_1", "col_2", "col_3"]


def test_replace_simples():
    d = parsear_insert("REPLACE INTO clientes (id, nome) VALUES (1, 'Ana');")
    assert d.tipo_comando == "REPLACE"


def test_multiplas_linhas_no_mesmo_values():
    d = parsear_insert("INSERT INTO clientes (id, nome) VALUES (1, 'Ana'), (2, 'Bia');")
    assert d.linhas == [["1", "'Ana'"], ["2", "'Bia'"]]


def test_multiplos_inserts_mesma_tabela():
    d = parsear_insert(
        "INSERT INTO clientes (id, nome) VALUES (1, 'Ana');"
        "INSERT INTO clientes (id, nome) VALUES (2, 'Bia');"
    )
    assert d.quantidade_inserts_origem == 2
    assert d.linhas == [["1", "'Ana'"], ["2", "'Bia'"]]


def test_multiplos_inserts_tabela_diferente_falha():
    with pytest.raises(InsertsIncompativeisError):
        parsear_insert("INSERT INTO a (id) VALUES (1); INSERT INTO b (id) VALUES (2);")


def test_multiplos_inserts_colunas_diferentes_falha():
    with pytest.raises(ColunasDivergentesError):
        parsear_insert("INSERT INTO a (id) VALUES (1); INSERT INTO a (nome) VALUES ('Ana');")


def test_valor_com_virgula_dentro_da_string():
    d = parsear_insert("INSERT INTO t (id, texto) VALUES (1, 'a,b');")
    assert d.linhas == [["1", "'a,b'"]]


def test_valor_com_ponto_e_virgula_dentro_da_string():
    d = parsear_insert("INSERT INTO t (id, texto) VALUES (1, 'a;b');")
    assert d.linhas == [["1", "'a;b'"]]


def test_valor_com_aspas_escapadas():
    d = parsear_insert("INSERT INTO t (id, texto) VALUES (1, 'O''Reilly');")
    assert d.linhas == [["1", "'O''Reilly'"]]


def test_funcao_dentro_values():
    d = parsear_insert("INSERT INTO t (id, criado_em) VALUES (1, NOW());")
    assert d.linhas == [["1", "NOW()"]]


def test_parenteses_dentro_de_string():
    d = parsear_insert("INSERT INTO t (id, texto) VALUES (1, 'valor (teste)');")
    assert d.linhas == [["1", "'valor (teste)'"]]


def test_comentario_linha_hifen():
    d = parsear_insert("-- cabecalho\nINSERT INTO t (id) VALUES (1); -- fim")
    assert d.linhas == [["1"]]


def test_comentario_bloco():
    d = parsear_insert("/* cabecalho */ INSERT INTO t (id) VALUES (1);")
    assert d.linhas == [["1"]]


def test_comentario_hash():
    d = parsear_insert("# cabecalho\nINSERT INTO t (id, texto) VALUES (1, '#nao comentario');")
    assert d.linhas == [["1", "'#nao comentario'"]]


def test_hash_dentro_de_string_produto_123():
    d = parsear_insert("INSERT INTO t (texto) VALUES ('Produto #123'); # comentario real")
    assert d.linhas == [["'Produto #123'"]]


def test_hifen_hifen_dentro_de_string():
    d = parsear_insert("INSERT INTO t (texto) VALUES ('abc -- teste'); -- comentario real")
    assert d.linhas == [["'abc -- teste'"]]


def test_comentario_bloco_dentro_de_string():
    d = parsear_insert("INSERT INTO t (texto) VALUES ('abc /* teste */'); /* comentario real */")
    assert d.linhas == [["'abc /* teste */'"]]


def test_now_uuid_concat_dentro_values():
    d = parsear_insert("INSERT INTO t (criado, uid, nome) VALUES (NOW(), UUID(), CONCAT('A', 'B'));")
    assert d.linhas == [["NOW()", "UUID()", "CONCAT('A', 'B')"]]


def test_texto_com_parenteses():
    d = parsear_insert("INSERT INTO t (texto) VALUES ('Produto (novo) com (parenteses)');")
    assert d.linhas == [["'Produto (novo) com (parenteses)'"]]


def test_on_duplicate_texto_dentro_string_nao_e_bloqueado():
    d = parsear_insert("INSERT INTO t (texto) VALUES ('ON DUPLICATE KEY UPDATE');")
    assert d.linhas == [["'ON DUPLICATE KEY UPDATE'"]]


def test_linha_com_quantidade_divergente():
    d = parsear_insert("INSERT INTO t (id, nome) VALUES (1, 'Ana'), (2);")
    assert d.indices_linhas_com_quantidade_invalida() == [1]


@pytest.mark.parametrize(
    "sql,mensagem",
    [
        ("INSERT IGNORE INTO t (id) VALUES (1);", "INSERT IGNORE"),
        ("INSERT INTO t SET id = 1;", "INSERT ... SET"),
        ("INSERT INTO t (id) SELECT id FROM origem;", "INSERT ... SELECT"),
        ("INSERT INTO t (id) VALUES (1) ON DUPLICATE KEY UPDATE id = 2;", "ON DUPLICATE KEY UPDATE"),
    ],
)
def test_formatos_nao_suportados(sql, mensagem):
    with pytest.raises(SqlNaoSuportadoError, match=mensagem):
        parsear_insert(sql)


def test_sql_vazio_falha():
    with pytest.raises(SqlParseError):
        parsear_insert("")


def test_comando_nao_insert_replace_falha():
    with pytest.raises(SqlNaoSuportadoError):
        parsear_insert("UPDATE t SET id = 1;")


def test_texto_residual_perigoso_apos_values_falha():
    with pytest.raises(SqlNaoSuportadoError, match="Texto residual apos VALUES"):
        parsear_insert("INSERT INTO t (id) VALUES (1) RETURNING id;")
