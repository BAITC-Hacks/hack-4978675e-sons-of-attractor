from .catalog import Catalog
from .schemas import DemoQuery, RecommendationQuery


def demo_queries(catalog: Catalog) -> list[DemoQuery]:
    """Queries from the specification, with no cached recommendations."""
    cases = [
        ("halls", "Залы Алматы", "Алматы", "Банкетный зал", "2026-11-13", 7_000_000, "русский"),
        ("halls_next_day", "Залы на следующий день", "Алматы", "Банкетный зал", "2026-11-14", 7_000_000, "русский"),
        ("florist", "Флорист на корпоратив", "Алматы", "Флорист", "2026-11-13", 500_000, "русский"),
        ("decorator", "Декоратор в Астане", "Астана", "Декоратор", "2026-11-13", 3_000_000, "русский"),
        ("busy_halls", "Занятые залы", "Алматы", "Банкетный зал", "2026-12-19", 7_000_000, "русский"),
        ("host_budget", "Ведущий: ограниченный бюджет", "Алматы", "Ведущий", "2026-11-14", 600_000, "русский"),
        ("busy_hosts", "Ведущий в декабре", "Алматы", "Ведущий", "2026-12-12", 1_500_000, "русский"),
        ("bands", "Лайв-бэнды на казахском", "Алматы", "Лайв-бэнд", "2026-11-13", 1_200_000, "казахский"),
    ]
    dictionaries = catalog.dictionaries()
    return [
        DemoQuery(id=id_, label=label, query=RecommendationQuery(
            city=city, category=category, date=date, budget_kzt=budget,
            event_format="корпоратив", language=language, duration_hours=6,
        ))
        for id_, label, city, category, date, budget, language in cases
        if city in dictionaries["cities"] and category in dictionaries["categories"]
        and language in dictionaries["languages"] and "корпоратив" in dictionaries["event_formats"]
    ]
