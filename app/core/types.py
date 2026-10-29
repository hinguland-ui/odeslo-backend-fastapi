from typing import Annotated

from pydantic import AfterValidator, StringConstraints

from app.core.utils import normalize_mobile

Mobile = Annotated[str, AfterValidator(normalize_mobile)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TimeHHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
