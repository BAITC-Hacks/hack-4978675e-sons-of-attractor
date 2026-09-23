import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .catalog import Catalog, CatalogError, load_catalog
from .core.config import Settings
from .demo_queries import demo_queries
from .facts import load_facts
from .recommendations import recommend
from .schemas import ErrorDetail, ErrorResponse, HealthResponse, MetaResponse, RecommendationQuery, RecommendationResponse, Versions

logger = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict[str, str] | None = None):
        self.status, self.code, self.message = status, code, message
        self.fields = fields or {}


def error_response(status: int, code: str, message: str, fields: dict[str, str] | None = None):
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, fields=fields or {}))
    return JSONResponse(status_code=status, content=body.model_dump())


def get_catalog(request: Request) -> Catalog:
    catalog = request.app.state.catalog
    if catalog is None:
        raise APIError(503, "data_unavailable", "Каталог недоступен или повреждён")
    return catalog


def validate_query(query: RecommendationQuery, catalog: Annotated[Catalog, Depends(get_catalog)]) -> RecommendationQuery:
    fields = {}
    dictionaries = catalog.dictionaries()
    for field, dictionary in (("city", "cities"), ("category", "categories"),
                              ("event_format", "event_formats"), ("language", "languages")):
        value = getattr(query, field)
        if value is not None and value not in dictionaries[dictionary]:
            fields[field] = "Неизвестное значение справочника"
    if fields:
        raise APIError(422, "validation_error", "Проверьте параметры запроса", fields)
    return query


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog = None
        try:
            catalog = load_catalog(settings.dataset_path)
            facts = load_facts(settings.facts_path, catalog)
            app.state.catalog = replace(catalog, facts=facts)
            if facts.warning:
                logger.warning(facts.warning)
        except CatalogError:
            logger.exception("Catalog initialization failed")
        yield

    app = FastAPI(title="Event contractors API", lifespan=lifespan)
    app.state.settings = settings
    app.state.catalog = None

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return error_response(exc.status, exc.code, exc.message, exc.fields)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        fields = {}
        for issue in exc.errors():
            location = ".".join(str(part) for part in issue["loc"] if part != "body") or "body"
            message = "Некорректное значение"
            if issue["type"] == "missing":
                message = "Обязательное поле"
            elif issue["type"] == "extra_forbidden":
                message = "Лишнее поле"
            elif location == "date":
                message = "Ожидается дата YYYY-MM-DD в диапазоне 2026-09-23–2026-12-31"
            fields.setdefault(location, message)
        return error_response(422, "validation_error", "Проверьте параметры запроса", fields)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        return error_response(exc.status_code, "not_found" if exc.status_code == 404 else "http_error",
                              "Ресурс не найден" if exc.status_code == 404 else "Запрос не может быть выполнен")

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled API error")
        return error_response(500, "internal_error", "Внутренняя ошибка сервиса")

    @app.get("/api/health", response_model=HealthResponse, responses={503: {"model": ErrorResponse}})
    def health(catalog: Annotated[Catalog, Depends(get_catalog)]):
        return HealthResponse(catalog_count=len(catalog.profiles), explanation_mode=catalog.explanation_mode,
                              versions=Versions(dataset=catalog.sha256, facts=catalog.facts_sha256))

    @app.get("/api/meta", response_model=MetaResponse, responses={503: {"model": ErrorResponse}})
    def meta(catalog: Annotated[Catalog, Depends(get_catalog)]):
        return MetaResponse(**catalog.dictionaries(), explanation_mode=catalog.explanation_mode,
                            versions=Versions(dataset=catalog.sha256, facts=catalog.facts_sha256), demo_queries=demo_queries(catalog))

    @app.post("/api/recommendations", response_model=RecommendationResponse,
              responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
    def recommendations(query: Annotated[RecommendationQuery, Depends(validate_query)],
                        catalog: Annotated[Catalog, Depends(get_catalog)]):
        return recommend(query, catalog)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
    @app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
    def unknown_api(path: str = ""):
        raise HTTPException(404)

    if settings.frontend_path.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_path, html=True), name="frontend")

    return app


app = create_app()
