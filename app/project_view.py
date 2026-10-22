from datetime import datetime


def is_cloud_project(project):
    return project.folder_name.upper().endswith("_CLOUD")


def short_status(project):
    if project.status == "OK":
        return "OK"
    normalized = project.status.lower()
    if "ausente" in normalized:
        return "Arquivo ausente"
    if "zip" in normalized:
        return "Pendência ZIP"
    if "fileversion" in normalized or "productversion" in normalized:
        return "Versão incorreta"
    return "Pendência"


def status_category(project):
    return {"OK": "ok", "Arquivo ausente": "missing", "Versão incorreta": "version", "Pendência ZIP": "zip"}.get(short_status(project), "other")


def status_tag(project):
    category = status_category(project)
    if category == "ok":
        return "ok"
    return "error" if category in {"missing", "version", "zip"} else "warning"


def short_zip_status(project):
    return {"Autcom zip OK": "ZIP OK", "Sem autcom.zip": "Somente EXE", "Autcom ausente": "Sem Autcom"}.get(project.zip_status, "Pendência")


def project_type(project):
    return "Cloud" if is_cloud_project(project) else "Local"


def matches_search(project, term):
    term = str(term or "").strip().lower()
    if not term:
        return True
    fields = (project.folder_name, project.display_file_version or "", project.display_product_version or "", project.expected_file_version or "", project.expected_product_version or "", short_zip_status(project), short_status(project), project.status)
    return term in " ".join(fields).lower()


def matches_filter(project, selected_filter):
    if selected_filter == "Somente OK":
        return project.status == "OK"
    if selected_filter == "Com erro":
        return project.status != "OK"
    if selected_filter == "Local":
        return not is_cloud_project(project)
    if selected_filter == "Cloud":
        return is_cloud_project(project)
    return True


def summary_text(projects, analyzed_total, now=None):
    counts = {name: 0 for name in ("ok", "version", "missing", "zip", "other")}
    for project in projects:
        counts[status_category(project)] += 1
    pending = [f"{counts['version']} versão incorreta", f"{counts['missing']} arquivo ausente", f"{counts['zip']} pendência ZIP"]
    if counts["other"]:
        pending.append(f"{counts['other']} outras pendências")
    now_text = (now or datetime.now()).strftime("%H:%M:%S")
    return f"{len(projects)} exibido(s) de {analyzed_total} analisado(s) | {counts['ok']} OK | {' | '.join(pending)} | Atualizado às {now_text}"


def sort_value(project, column):
    values = {"folder": project.folder_name.lower(), "type": project_type(project), "file_version": project.display_file_version or "", "product_version": project.display_product_version or "", "expected_file": project.expected_file_version or "", "expected_product": project.expected_product_version or "", "autcom_size": project.display_autcom_size_mb or 0, "zip": short_zip_status(project), "status": short_status(project)}
    return values.get(column, "")


STATUS_SYMBOLS = {"ok": "●", "missing": "✖", "version": "▲", "zip": "▲", "other": "▲"}


def status_display(project):
    """Status curto com simbolo, legivel mesmo com a linha selecionada."""
    return f"{STATUS_SYMBOLS[status_category(project)]} {short_status(project)}"


def version_display(found, expected):
    """Marca com ✗ a versao encontrada quando difere da esperada."""
    found = found or ""
    if found and expected and found != expected:
        return f"✗ {found}"
    return found


def dashboard_counts(projects):
    total = len(projects)
    ok = sum(1 for project in projects if project.status == "OK")
    return {"total": total, "ok": ok, "pending": total - ok}
