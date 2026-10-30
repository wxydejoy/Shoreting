STATUS_BY_CODE = {
    "forbidden": 403,
    "unauthorized": 401,
    "bad_request": 400,
    "unsupported": 400,
    "not_found": 404,
    "offline": 409,
    "login_required": 503,
    "mijia_error": 502,
    "action_error": 502,
    "companion_unavailable": 502,
    "media_error": 502,
    "too_many": 429,
}


class HubError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else STATUS_BY_CODE.get(code, 502)
        # 目前只有 429 用得上（Retry-After），但接口留在这里比将来加个特例干净。
        self.headers = headers or {}

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}
