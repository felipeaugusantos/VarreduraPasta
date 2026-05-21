from dataclasses import dataclass
from typing import List, Optional

from .exceptions import LinhasInvalidasError


@dataclass
class DeclaracaoInsert:
    tabela: str
    colunas: Optional[List[str]]
    linhas: List[List[str]]
    tinha_colunas: bool
    quantidade_inserts_origem: int = 1
    tipo_comando: str = "INSERT"

    def nomes_campos(self) -> List[str]:
        if self.colunas is not None:
            return self.colunas
        max_len = max((len(ln) for ln in self.linhas), default=0)
        return [f"col_{i + 1}" for i in range(max_len)]

    def indices_linhas_com_quantidade_invalida(self) -> List[int]:
        if self.colunas is None:
            return []
        total_colunas = len(self.colunas)
        return [
            i for i, linha in enumerate(self.linhas)
            if len(linha) != total_colunas
        ]

    def validar_para_geracao(self) -> None:
        if not self.linhas:
            raise LinhasInvalidasError("Nao ha linhas para gerar SQL.")
        ruins = self.indices_linhas_com_quantidade_invalida()
        if ruins:
            amostra = ", ".join(str(i) for i in ruins[:20])
            if len(ruins) > 20:
                amostra += ", ..."
            raise LinhasInvalidasError(
                "SQL nao foi gerado por seguranca. "
                "Existem linhas com quantidade de valores diferente das colunas. "
                f"Total de linhas problematicas: {len(ruins)}. "
                f"Amostra dos indices: {amostra}."
            )

    def resumo(self) -> str:
        problemas = self.indices_linhas_com_quantidade_invalida()
        partes = [
            f"Comando: {self.tipo_comando}",
            f"Tabela: {self.tabela}",
            f"Statements: {self.quantidade_inserts_origem}",
            f"Linhas: {len(self.linhas)}",
            f"Colunas: {len(self.nomes_campos())}",
        ]
        if problemas:
            partes.append(f"Linhas com divergencia: {len(problemas)}")
        return " | ".join(partes)

    def remover_colunas_por_indices(self, indices: List[int]):
        if not indices:
            return
        indices = sorted(set(indices), reverse=True)

        if self.colunas is not None:
            for i in indices:
                if 0 <= i < len(self.colunas):
                    self.colunas.pop(i)

        for linha in self.linhas:
            for i in indices:
                if 0 <= i < len(linha):
                    linha.pop(i)

    def remover_linha(self, indice_linha: int):
        if 0 <= indice_linha < len(self.linhas):
            self.linhas.pop(indice_linha)

    def _parte_colunas(self) -> str:
        if self.tinha_colunas and self.colunas is not None:
            return f" ({', '.join(self.colunas)})"
        return ""

    def para_sql(self) -> str:
        self.validar_para_geracao()
        parte_colunas = self._parte_colunas()
        grupos_valores = [f"({', '.join(linha)})" for linha in self.linhas]
        parte_valores = ", ".join(grupos_valores)
        return f"{self.tipo_comando} INTO {self.tabela}{parte_colunas} VALUES {parte_valores};"

    def para_sql_multiplos(self) -> str:
        self.validar_para_geracao()
        parte_colunas = self._parte_colunas()
        partes = [
            f"{self.tipo_comando} INTO {self.tabela}{parte_colunas} VALUES ({', '.join(linha)});"
            for linha in self.linhas
        ]
        return "\n".join(partes)
