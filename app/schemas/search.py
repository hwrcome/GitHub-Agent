from pydantic import BaseModel, Field, field_validator


class SearchRequestCreate(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    max_results: int | None = Field(default=None, ge=1, le=100)
    per_page: int | None = Field(default=None, ge=1, le=50)
    include_code_quality: bool = True

    @field_validator("query")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    def to_config(self) -> dict[str, int | bool]:
        return {
            "max_results": self.max_results or 20,
            "per_page": self.per_page or 10,
            "include_code_quality": self.include_code_quality,
        }
