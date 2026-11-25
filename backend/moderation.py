"""Klasyfikacja tekstu przez lokalne API LM Studio."""

import math
import sys
from typing import Annotated, Literal
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from backend.config import get_moderation_settings

Probability = Annotated[float, Field(strict=True, ge=0, le=1)]
TIMEOUT = httpx.Timeout(15.0, connect=3.0)

class ModerationError(RuntimeError):
    """Klasyfikacja jest wyłączona lub nie udało się uzyskać poprawnego wyniku."""

class ModerationProbabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe: Probability
    review: Probability
    unsafe: Probability

    @model_validator(mode="after")
    def validate_total(self) -> "ModerationProbabilities":
        total = self.safe + self.review + self.unsafe
        if not math.isclose(total, 1, rel_tol=0, abs_tol=0.001):
            raise ValueError("Prawdopodobieństwa muszą sumować się do 1.")
        return self

class ModerationResult(BaseModel):
    decision: Literal["safe", "review", "unsafe"] = Field(validation_alias="choice")
    confidence: Probability
    probabilities: ModerationProbabilities

def moderate_text(content: str) -> ModerationResult:
    settings = get_moderation_settings()
    if not settings.enabled:
        raise ModerationError("Moderacja jest wyłączona (AI_MODERATION_ENABLED).")

    payload = {
        "model": settings.model,
        "state": content,
        "lang": "pl",
        "questions": {
            "moderation": {
                "type": "choice",
                "instructions": (
                    "Oceń treść posta na platformie społecznościowej. "
                    "Zwykła krytyka, opinie i nieszkodliwe wulgaryzmy są dozwolone."
                ),
                "criteria": {
                    "safe": "Treść bezpieczna, zwykła opinia lub nieszkodliwa wypowiedź.",
                    "review": "Treść niejednoznaczna, wymagająca sprawdzenia przez moderatora.",
                    "unsafe": "Poważne groźby, nawoływanie do przemocy lub nękanie.",
                },
            },
        },
    }
    try:
        base_url = httpx.URL(settings.base_url)
        if base_url.scheme not in {"http", "https"} or not base_url.host:
            raise ModerationError("AI_BASE_URL musi zawierać pełny adres HTTP lub HTTPS.")
        response = httpx.post(
            f"{settings.base_url}/v1/systemone",
            json=payload,
            timeout=TIMEOUT,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.InvalidURL:
        raise ModerationError("AI_BASE_URL zawiera nieprawidłowy adres.") from None
    except httpx.TimeoutException:
        raise ModerationError("Przekroczono limit czasu odpowiedzi LM Studio.") from None
    except httpx.RequestError:
        raise ModerationError("Nie udało się połączyć z LM Studio.") from None
    except httpx.HTTPStatusError as error:
        raise ModerationError(f"LM Studio zwróciło HTTP {error.response.status_code}.") from None

    try:
        data = response.json()
    except ValueError:
        raise ModerationError("LM Studio zwróciło nieprawidłowy JSON.") from None

    try:
        return ModerationResult.model_validate(data["answers"]["moderation"])
    except (KeyError, TypeError, ValidationError):
        raise ModerationError("LM Studio zwróciło nieprawidłowy wynik klasyfikacji.") from None

def main() -> int:
    try:
        result = moderate_text("Dzisiaj jest piękna pogoda!")
    except ModerationError as error:
        print(error, file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())