"""Точка входа MCP-сервера «Борт»: python -m bort.mcp.server. Транспорт stdio.

Регистрация у MCP-клиента (см. README; подставь свой путь к venv):
    {"mcpServers": {"bort": {
        "command": "<ПУТЬ К РЕПО>/.venv/bin/python",
        "args": ["-m", "bort.mcp.server"],
        "env": {"BORT_DB": "<ПУТЬ К РЕПО>/data/bort.db", "BORT_TZ": "Europe/Moscow"}
    }}}
"""

from mcp.server.mcpserver import MCPServer

from .tools import register_tools, summary_markdown

TOOLS_COUNT = 14


def create_server() -> MCPServer:
    server = MCPServer(
        name="bort",
        version="0.1.0",
        instructions=(
            "«Борт» — персональная сводка проектов пользователя: деньги (канон — целые "
            "копейки *_minor, рядом человекочитаемое поле), дедлайны, задачи, чаты и люди. "
            "Проекты можно находить по названию (name) — регистронезависимо; при "
            "неоднозначности возвращается ошибка conflict со списком candidates. "
            "Каждый инструмент возвращает {\"ok\": true, ...} либо "
            "{\"ok\": false, \"error\": {code, message, details}}. "
            "Ресурс bort://summary — текстовая сводка одним чтением."
        ),
    )
    register_tools(server)

    @server.resource(
        "bort://summary",
        name="Сводка",
        description="Markdown-сводка открытых проектов: деньги, маржа, дедлайны",
        mime_type="text/markdown",
    )
    def bort_summary() -> str:
        from .. import db

        conn = db.connect()
        try:
            return summary_markdown(conn)
        finally:
            conn.close()

    return server


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()
