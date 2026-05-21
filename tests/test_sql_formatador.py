from app.core.sql_formatador import normalizar_valor_editado


def test_string_vazia_vira_null():
    assert normalizar_valor_editado("") == "NULL"


def test_null_vira_null():
    assert normalizar_valor_editado("NULL") == "NULL"


def test_null_minusculo_vira_null():
    assert normalizar_valor_editado("null") == "NULL"


def test_numero_inteiro():
    assert normalizar_valor_editado("123") == "123"


def test_numero_decimal():
    assert normalizar_valor_editado("10.50") == "10.50"


def test_numero_negativo():
    assert normalizar_valor_editado("-123") == "-123"


def test_numero_decimal_negativo():
    assert normalizar_valor_editado("-10.5") == "-10.5"


def test_notacao_cientifica():
    assert normalizar_valor_editado("1.2e-3") == "1.2e-3"


def test_texto_comum_recebe_aspas():
    assert normalizar_valor_editado("abc") == "'abc'"


def test_aspas_internas_sao_escapadas():
    assert normalizar_valor_editado("O'Reilly") == "'O''Reilly'"


def test_texto_ja_entre_aspas_preservado():
    assert normalizar_valor_editado("'abc'") == "'abc'"


def test_expressao_now_com_prefixo_igual():
    assert normalizar_valor_editado("=NOW()") == "NOW()"


def test_expressao_current_date_com_prefixo_igual():
    assert normalizar_valor_editado("=CURRENT_DATE") == "CURRENT_DATE"


def test_expressao_uuid_com_prefixo_igual():
    assert normalizar_valor_editado("=UUID()") == "UUID()"


def test_now_sem_prefixo_igual_vira_texto():
    assert normalizar_valor_editado("NOW()") == "'NOW()'"
