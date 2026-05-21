class SqlParseError(ValueError):
    pass


class SqlNaoSuportadoError(SqlParseError):
    pass


class InsertsIncompativeisError(SqlParseError):
    pass


class ColunasDivergentesError(SqlParseError):
    pass


class LinhasInvalidasError(SqlParseError):
    pass
