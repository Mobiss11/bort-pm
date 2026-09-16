"""Summary acceptance using an isolated database; never touches live records."""
import re


def test_one_portfolio_panel_and_two_tables_even_when_empty(client):
    html = client.get('/').text
    assert len(re.findall(r'<section class="portfolio"', html)) == 1
    assert html.count('<table class="projects"') == 2
    assert 'Открытые' in html and 'Закрытых проектов пока нет' in html
    assert html.count('id="projects-tbody"') == 1
    assert html.count('id="projects-tbody-closed"') == 1


def test_portfolio_paid_remaining_and_search_independence(client):
    for status, name, amount, paid in [('active', 'Открытый', 120050, 30020), ('closed', 'Закрытый', 90070, 60030)]:
        p = client.post('/api/v1/projects', json={'name': name, 'status': status, 'deal_amount_minor': amount}).json()
        client.post(f'/api/v1/projects/{p["id"]}/payments', json={'amount_minor': paid, 'paid_on': '2026-09-01', 'kind': 'partial'})
    totals = client.get('/api/v1/summary').json()['portfolio_totals']
    assert totals['paid_total_minor'] == 30020 + 60030
    assert totals['remaining_total_minor'] == 120050 + 90070 - 30020 - 60030
    assert client.get('/api/v1/summary?q=Открытый').json()['portfolio_totals'] == totals
    html = client.get('/?q=Открытый').text
    for key in ['deal_total_minor', 'paid_total_minor', 'remaining_total_minor', 'expenses_total_minor', 'margin_total_minor']:
        assert f'data-metric="{key}" data-minor="{totals[key]}"' in html
    # оплата видна прямо в строке проекта, маржа уехала за раскрытие «Ещё»
    assert 'data-label="Оплата"' in html
    assert 'Маржа:' in html


def test_sections_stay_separate_for_legacy_scope_and_htmx(client):
    for status, name in [('active', 'Open marker'), ('closed', 'Closed marker')]:
        assert client.post('/api/v1/projects', json={'name': name, 'status': status}).status_code == 201
    for url in ['/?scope=all', '/ui/projects?scope=closed']:
        html = client.get(url).text
        sections = re.findall(r'<tbody id="projects-tbody(?:-closed)?">(.*?)</tbody>', html, re.S)
        assert len(sections) == 2
        assert 'Open marker' in sections[0] and 'Closed marker' not in sections[0]
        assert 'Closed marker' in sections[1] and 'Open marker' not in sections[1]
    filtered = client.get('/ui/projects?q=missing').text
    assert filtered.count('Нет проектов по запросу') == 2


def test_create_project_via_ui_refreshes_table_preserving_filters(client):
    """Создание проекта через UI: таблица обновляется OOB-свопом с текущими фильтрами."""
    p = client.post("/api/v1/projects", json={"name": "Старый с задачей", "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "Таска"})
    r = client.post(
        "/ui/projects",
        data={"name": "Совсем новый", "scope": "open", "tasks": "open", "q": "", "status": "active", "priority": "2"},
    )
    assert r.status_code == 200
    assert "Проект создан" in r.text
    # OOB-обновление таблицы и панелей с сохранением фильтра tasks=open
    assert 'id="projects-table-box" hx-swap-oob="innerHTML"' in r.text
    assert re.search(r'<section class="attention[^"]*"[^>]*id="attention-panel"[^>]*hx-swap-oob="outerHTML"', r.text)
    assert re.search(r'<section class="portfolio"[^>]*id="portfolio-panel"[^>]*hx-swap-oob="outerHTML"', r.text)

    oob_box = re.search(r'<div id="projects-table-box" hx-swap-oob="innerHTML">(.*?)</div>\s*<section', r.text, re.S).group(1)
    assert "Старый с задачей" in oob_box
    assert 'id="projects-tbody-closed"' in oob_box
    # под фильтром tasks=open новый пустой проект не появляется — без выдуманных строк
    open_tbody = re.search(r'<tbody id="projects-tbody">(.*?)</tbody>', oob_box, re.S).group(1)
    assert "Совсем новый" not in open_tbody
    # блок внимания приезжает тем же ответом; проекта без задач и дедлайна в нём нет —
    # внимания он не требует, а в CTA «добавь задачу» больше не перечисляется
    attention = re.search(r'<section class="attention.*?</section>', r.text, re.S).group(0)
    assert "Совсем новый" not in attention
