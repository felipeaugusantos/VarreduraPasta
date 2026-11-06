"""Relatorios da auditoria (CSV, JSON, HTML) e a linha de resumo da Auditoria.

Nunca inclui senha. O conteudo vem de nomes de objetos e arquivos, entao o HTML
e escapado e o CSV neutraliza celulas que o Excel leria como formula.
"""

import csv
import html
import json
import os
from collections import Counter
from pathlib import Path

from app.sqlaudit.comparator import (
    AUSENTE,
    COMPATIVEL,
    DIVERGENTE,
    ERRO,
    INDETERMINADO,
    NAO_SUPORTADO,
    PRECEDENCE,
    SUBSTITUIDO,
)

STATUS_LABELS = {
    COMPATIVEL: "Compatível",
    AUSENTE: "Ausente",
    DIVERGENTE: "Divergente",
    SUBSTITUIDO: "Substituído",
    NAO_SUPORTADO: "Não suportado",
    INDETERMINADO: "Indeterminado",
    ERRO: "Erro",
}
DISCLAIMER = (
    "Compatível indica que a estrutura do banco é compatível com os scripts; "
    "não comprova que algum script foi executado."
)
PROBLEM_STATUSES = (ERRO, DIVERGENTE, AUSENTE)
CSV_COLUMNS = (
    "versao", "banco", "tipo", "tabela", "coluna", "estado", "diferencas", "notas",
    "script", "linha", "origem_certa",
)
FORMAT_SUFFIXES = {".csv": "csv", ".json": "json", ".html": "html", ".htm": "html"}


def label(status):
    return STATUS_LABELS.get(status, status)


def format_differences(differences):
    return "; ".join(f"{d.attribute}: esperado {d.expected}, encontrado {d.found}" for d in differences)


def object_rows(run):
    """Uma linha por objeto comparado (tabela/coluna), de todas as versoes."""
    rows = []
    for audit in run.versions:
        if audit.comparison is None:
            continue
        for item in audit.comparison.objects:
            provenance = item.provenance
            rows.append(
                {
                    "versao": audit.version,
                    "banco": audit.database,
                    "tipo": item.kind,
                    "tabela": item.table,
                    "coluna": item.column or "",
                    "estado": item.status,
                    "diferencas": format_differences(item.differences),
                    "notas": "; ".join(item.notes),
                    "script": provenance.script if provenance else "",
                    "linha": provenance.line if provenance else "",
                    "origem_certa": ("sim" if provenance.certain else "nao") if provenance else "",
                }
            )
    return rows


def status_counts(run):
    counts = Counter()
    for audit in run.versions:
        if audit.comparison is not None:
            counts.update(item.status for item in audit.comparison.objects)
    return counts


def summary_text(run):
    counts = status_counts(run)
    parts = [f"{counts[status]} {label(status).lower()}" for status in PRECEDENCE if counts.get(status)]
    failed = [audit.version for audit in run.versions if audit.error]
    text = f"{len(run.versions)} versao(oes): " + (", ".join(parts) if parts else "nenhum objeto comparado")
    if failed:
        text += f"; {len(failed)} com erro"
    if run.cancelled:
        text += "; cancelada"
    return text


def audit_line(run, result, reason=None):
    """Linha para o `write_log` (Auditoria): sem credenciais e sem SQL."""
    clean = lambda value: str(value).replace("|", "/").replace("\n", " ")
    databases = ",".join(audit.database for audit in run.versions)
    return (
        f"Auditoria SQL {result} | usuario={clean(run.user)} | projeto= | tipo= | origem={clean(run.root)} | "
        f"destino={clean(databases)} | bat= | fileversion= | productversion= | autcom_mb= | "
        f"motivo={clean(reason if reason is not None else summary_text(run))}"
    )


# -- formatos --

def _safe_cell(value):
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv_rows(run):
    return [[_safe_cell(row[column]) for column in CSV_COLUMNS] for row in object_rows(run)]


def build_json(run):
    versions = []
    for audit in run.versions:
        comparison = audit.comparison
        hint = audit.fork_hint
        versions.append(
            {
                "versao": audit.version,
                "banco": audit.database,
                "servidor": audit.server_version,
                "tls_verificado": audit.tls_verified,
                "erro": audit.error,
                "scripts_aplicados": audit.scripts_total,
                "scripts_alem_do_fork": [{"versao": v, "arquivo": f} for v, f in audit.excluded],
                "avisos": audit.warnings + (comparison.warnings if comparison else []),
                "duracao_s": round(audit.duration, 2),
                "contagem_objetos": dict(comparison.object_counts()) if comparison else {},
                "contagem_comandos": dict(comparison.operation_counts()) if comparison else {},
                "fork_sugerido": None
                if hint is None
                else {
                    "candidato": hint.candidate,
                    "limite_superior": hint.upper,
                    "primeiro_ausente": hint.first_missing,
                    "inconsistentes": hint.inconsistent,
                    "restaurado_ate": hint.earliest_restore.isoformat() if hint.earliest_restore else None,
                    "notas": hint.notes,
                },
                "scripts": [
                    {
                        "versao_origem": f.version, "script": f.script, "numero": f.number, "sha256": f.sha256,
                        "bytes": f.size, "origem_certa": f.certain, "herdado": f.inherited,
                    }
                    for f in audit.files
                ],
                "arquivos": [
                    {"script": f.script, "numero": f.number, "estado": f.status, "contagem": f.counts, "parcial": f.partial}
                    for f in (comparison.files() if comparison else [])
                ],
                "comandos": [
                    {
                        "script": o.applied.provenance.script,
                        "linha": o.applied.provenance.line,
                        "tipo": o.applied.operation.kind,
                        "tabela": o.applied.operation.table,
                        "coluna": o.applied.operation.column.name if o.applied.operation.column else "",
                        "estado": o.status,
                        "detalhe": o.detail,
                    }
                    for o in (comparison.operations if comparison else [])
                ],
            }
        )
    return {
        "gerado_em": (run.finished or run.started).isoformat(timespec="seconds"),
        "usuario": run.user,
        "raiz": str(run.root),
        "cancelada": run.cancelled,
        "resumo": summary_text(run),
        "aviso": DISCLAIMER,
        "versoes": versions,
        "objetos": object_rows(run),
    }


def build_html(run):
    esc = lambda value: html.escape(str(value), quote=True)
    out = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<title>Auditoria de Estrutura SQL</title>",
        "<style>body{font:14px Segoe UI,Arial,sans-serif;margin:24px;color:#1f2937}"
        "table{border-collapse:collapse;width:100%;margin:8px 0 24px}"
        "th,td{border:1px solid #d8dee8;padding:4px 8px;text-align:left;vertical-align:top}"
        "th{background:#eef2f7}.AUSENTE,.DIVERGENTE,.ERRO{background:#fdf0f0}"
        ".INDETERMINADO,.NAO_SUPORTADO{background:#fff7e6}.SUBSTITUIDO{background:#eaf1fe}"
        ".muted{color:#6b7280}</style></head><body>",
        "<h1>Auditoria de Estrutura SQL</h1>",
        f"<p>Gerado em {esc((run.finished or run.started).strftime('%d/%m/%Y %H:%M'))} por {esc(run.user)}. "
        f"Raiz: {esc(run.root)}.</p>",
        f"<p><strong>{esc(summary_text(run))}</strong></p>",
        f"<p class='muted'>{esc(DISCLAIMER)}</p>",
    ]
    for audit in run.versions:
        out.append(f"<h2>Versão {esc(audit.version)} — banco {esc(audit.database)}</h2>")
        if audit.error:
            out.append(f"<p class='ERRO'>Erro: {esc(audit.error)}</p>")
        if audit.server_version:
            out.append(f"<p class='muted'>MySQL {esc(audit.server_version)} · {audit.scripts_total} script(s) aplicado(s) no modelo</p>")
        warnings = audit.warnings + (audit.comparison.warnings if audit.comparison else [])
        if warnings:
            out.append("<ul>" + "".join(f"<li>{esc(w)}</li>" for w in warnings) + "</ul>")
        if audit.comparison is None:
            continue
        out.append("<table><tr><th>Estado</th><th>Tipo</th><th>Tabela</th><th>Coluna</th><th>Diferenças</th><th>Notas</th><th>Script</th></tr>")
        for item in audit.comparison.objects:
            provenance = item.provenance
            origin = f"{provenance.script}:{provenance.line}" if provenance else ""
            out.append(
                f"<tr class='{esc(item.status)}'><td>{esc(label(item.status))}</td><td>{esc(item.kind)}</td>"
                f"<td>{esc(item.table)}</td><td>{esc(item.column or '')}</td>"
                f"<td>{esc(format_differences(item.differences))}</td><td>{esc('; '.join(item.notes))}</td>"
                f"<td>{esc(origin)}</td></tr>"
            )
        out.append("</table>")
    out.append("</body></html>")
    return "".join(out)


def write_report(run, path):
    """Grava o relatorio no formato da extensao, de forma atomica. Levanta OSError/ValueError."""
    path = Path(path)
    kind = FORMAT_SUFFIXES.get(path.suffix.lower())
    if kind is None:
        raise ValueError("Use a extensao .html, .csv ou .json.")
    temp = path.with_name(f".{path.name}.tmp")
    try:
        if kind == "csv":
            with temp.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(CSV_COLUMNS)
                writer.writerows(build_csv_rows(run))
        else:
            text = json.dumps(build_json(run), ensure_ascii=False, indent=2) if kind == "json" else build_html(run)
            temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return kind
