from fastapi import HTTPException, status


def not_found(what: str = "Resource") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def forbidden(message: str = "You do not have permission to perform this action") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, message)


def bad_request(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


def unauthorized(message: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, message, headers={"WWW-Authenticate": "Bearer"})
