"""Sugere o ponto de fork de uma versao filha a partir do banco dela.

Os dumps nao sao guardados, entao o fork nao tem prova direta. Aqui compara-se o
modelo COMPLETO da base com o banco da filha, script a script:
- script que a filha NAO tem (algum objeto AUSENTE) -> provavelmente nao herdado;
- script cujo efeito aparece (COMPATIVEL) -> provavelmente herdado;
- o resto (substituido, divergente, nao suportado...) nao prova nada.

E so uma SUGESTAO: o operador confirma. Divergente fica neutro porque a filha
pode ter alterado depois aquilo que herdou.
"""

from dataclasses import dataclass, field

from app.sqlaudit.comparator import AUSENTE, COMPATIVEL


@dataclass
class ForkSuggestion:
    candidate: int | None = None  # maior script com efeito comprovado antes do primeiro ausente
    upper: int | None = None  # maior numero anterior ao primeiro ausente (inclui os sem prova)
    first_missing: int | None = None
    inconsistent: list = field(default_factory=list)  # scripts apos o primeiro ausente que parecem herdados
    verifiable: int = 0
    earliest_restore: object = None  # menor CREATE_TIME do banco: limite superior da restauracao
    notes: list = field(default_factory=list)


def suggest_fork(comparison, base_version=None, earliest_restore=None):
    """`comparison` = compare(modelo completo da base, schema do banco da filha)."""
    verdicts = {}
    numbers = set()
    for item in comparison.operations:
        provenance = item.applied.provenance
        if provenance.number is None or (base_version and provenance.version != base_version):
            continue
        numbers.add(provenance.number)
        if item.status == AUSENTE:
            verdicts[provenance.number] = AUSENTE
        elif item.status == COMPATIVEL and verdicts.get(provenance.number) != AUSENTE:
            verdicts[provenance.number] = COMPATIVEL
    suggestion = ForkSuggestion(verifiable=len(verdicts), earliest_restore=earliest_restore)
    if not verdicts:
        suggestion.notes.append("Nenhum script da base tem efeito estrutural verificavel: informe o fork manualmente.")
        return suggestion
    missing = sorted(number for number, verdict in verdicts.items() if verdict == AUSENTE)
    if missing:
        first = missing[0]
        suggestion.first_missing = first
        reflected = [n for n, verdict in verdicts.items() if verdict == COMPATIVEL and n < first]
        suggestion.candidate = max(reflected) if reflected else None
        before = [n for n in numbers if n < first]
        suggestion.upper = max(before) if before else None
        suggestion.inconsistent = sorted(n for n, verdict in verdicts.items() if verdict == COMPATIVEL and n > first)
        if suggestion.inconsistent:
            suggestion.notes.append(
                "Scripts depois do primeiro ausente parecem presentes ("
                + ", ".join(str(n) for n in suggestion.inconsistent[:8])
                + "): a ordem pode nao refletir a aplicacao. Confirme manualmente."
            )
    else:
        reflected = [n for n, verdict in verdicts.items() if verdict == COMPATIVEL]
        suggestion.candidate = suggestion.upper = max(reflected) if reflected else None
        suggestion.notes.append("Todos os scripts verificaveis da base aparecem no banco: confirme o fork.")
    if suggestion.candidate is not None and suggestion.upper is not None and suggestion.candidate != suggestion.upper:
        suggestion.notes.append(
            f"Entre {suggestion.candidate} e {suggestion.upper} ha scripts sem prova estrutural: o fork exato e incerto."
        )
    return suggestion
